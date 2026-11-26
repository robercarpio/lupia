"""Silver-layer data loading, orphan province inference, and lifecycle/holiday flags."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from databricks.src.config import (
    EVAL_END,
    EVAL_START,
    N_DAYS,
    ORIGIN_DATE,
    RAMPDOWN_DAYS,
    RAMPUP_DAYS,
    REFORMA_MARGIN_DAYS,
    REFORMA_MIN_GAP_DAYS,
    SPECIAL_MMDD,
    get_data_dir,
)


def _clean_cat(series: pd.Series) -> pd.Series:
    """Normalize empty strings and '-' markers into 'desconocido'."""
    s = series.astype(str).str.strip()
    return s.where(~s.isin(["", "-", "nan", "None"]), "desconocido")


def load_raw_datasets(
    data_dir: Optional[Path] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load ventas_diarias, restaurantes, and calendario preserving empty strings."""
    base = Path(data_dir) if data_dir is not None else get_data_dir()
    origin_ts = pd.Timestamp(ORIGIN_DATE)

    ventas = pd.read_csv(base / "ventas_diarias.csv")
    ventas["fecha"] = pd.to_datetime(ventas["fecha"], format="%Y-%m-%d").dt.floor("D")
    ventas["day_idx"] = (ventas["fecha"] - origin_ts).dt.days.astype(np.int32)
    ventas["ventas"] = ventas["ventas"].astype(np.float64)
    ventas["tickets"] = ventas["tickets"].astype(np.int64)
    ventas["ticket_medio"] = ventas["ticket_medio"].astype(np.float64)
    ventas = ventas.sort_values(["restaurante_id", "canal", "fecha"]).reset_index(drop=True)

    restaurantes = pd.read_csv(base / "restaurantes.csv", keep_default_na=False)
    calendario = pd.read_csv(base / "calendario.csv", keep_default_na=False)
    calendario["fecha"] = pd.to_datetime(calendario["fecha"], format="%Y-%m-%d").dt.floor("D")
    calendario["day_idx"] = (calendario["fecha"] - origin_ts).dt.days.astype(np.int32)

    return ventas, restaurantes, calendario


def build_national_calendar_fallback(calendario: pd.DataFrame) -> pd.DataFrame:
    """Aggregate provincial calendar into a national fallback by date."""
    g = calendario.groupby("fecha", sort=True)
    n_prov = g["provincia"].nunique()
    nat_fest = g["tipo_festivo"].apply(lambda s: "national_holidays" if (s == "national_holidays").sum() >= 40 else "")
    nat_visp = g["vispera_festivo"].apply(lambda s: int((s == 1).sum() >= 40))
    nat_post = g["post_festivo"].apply(lambda s: int((s == 1).sum() >= 40))
    nat_puente = g["puente"].apply(lambda s: int((s == 1).sum() >= 40))
    nat_per_esp = g["periodo_especial"].apply(lambda s: int((s == 1).sum() >= 24))
    nat_fds_largo = g["fin_de_semana_largo"].apply(lambda s: s.mode().iloc[0] if len(s.mode()) else "")
    nat_tipo_dia = g["tipo_dia"].apply(lambda s: s.mode().iloc[0] if len(s.mode()) else "laborable")

    nat_df = pd.DataFrame({
        "fecha": n_prov.index,
        "day_idx": g["day_idx"].first().values,
        "dia_semana_nat": g["dia_semana"].first().values,
        "tipo_festivo_nat": nat_fest.values,
        "vispera_festivo_nat": nat_visp.values,
        "post_festivo_nat": nat_post.values,
        "puente_nat": nat_puente.values,
        "fin_de_semana_largo_nat": nat_fds_largo.values,
        "navidad_nat": g["navidad"].first().values,
        "semana_santa_nat": g["semana_santa"].first().values,
        "fecha_especial_nat": g["fecha_especial"].first().values,
        "tipo_dia_nat": nat_tipo_dia.values,
        "periodo_especial_nat": nat_per_esp.values,
    })
    return nat_df.reset_index(drop=True)


def infer_orphan_provinces(
    ventas: pd.DataFrame,
    restaurantes: pd.DataFrame,
    calendario: pd.DataFrame,
) -> pd.DataFrame:
    """Infer most likely province for orphan stores by correlating regional holiday shocks."""
    orphans = restaurantes.loc[restaurantes["provincia"] == "", "restaurante_id"].tolist()
    if not orphans:
        return pd.DataFrame(
            columns=["restaurante_id", "provincia_inferida", "ccaa_inferida", "corr_score", "n_regional_days"]
        )

    sala = ventas.loc[ventas["canal"] == "sala", ["restaurante_id", "fecha", "day_idx", "ventas"]].copy()
    sala["dow"] = sala["fecha"].dt.day_of_week
    sala["log_v"] = np.log1p(sala["ventas"])
    store_dow_med = sala.groupby(["restaurante_id", "dow"], sort=False)["log_v"].transform("median")
    sala["res"] = sala["log_v"] - store_dow_med

    nat_cal = build_national_calendar_fallback(calendario)[["fecha", "tipo_festivo_nat", "vispera_festivo_nat"]]
    cal_m = calendario.merge(nat_cal, on="fecha", how="left")
    is_reg = (
        cal_m["tipo_festivo"].isin(["regional_holidays", "local_holidays"])
        | ((cal_m["vispera_festivo"] == 1) & (cal_m["vispera_festivo_nat"] == 0))
    )
    reg_dates = np.sort(cal_m.loc[is_reg, "fecha"].unique())

    known = restaurantes.loc[restaurantes["provincia"] != "", ["restaurante_id", "provincia", "comunidad_autonoma"]]
    sala_known = sala.loc[sala["fecha"].isin(reg_dates)].merge(known, on="restaurante_id", how="inner")
    prov_profile = (
        sala_known.groupby(["fecha", "provincia"], sort=False)["res"]
        .mean()
        .unstack("provincia")
        .reindex(reg_dates)
    )
    prov_to_ccaa = known.drop_duplicates("provincia").set_index("provincia")["comunidad_autonoma"].to_dict()

    sala_orph = sala.loc[sala["restaurante_id"].isin(orphans) & sala["fecha"].isin(reg_dates)]
    results = []
    for rid in orphans:
        s_orph = sala_orph.loc[sala_orph["restaurante_id"] == rid].set_index("fecha")["res"]
        common_idx = prov_profile.index.intersection(s_orph.index)
        n_reg = int(len(common_idx))
        best_prov = "madrid"
        best_corr = -1.0
        if n_reg >= 5:
            y = s_orph.loc[common_idx].to_numpy(dtype=np.float64)
            y_c = y - np.nanmean(y)
            y_norm = np.sqrt(np.nansum(y_c * y_c))
            if y_norm > 1e-9:
                for prov in prov_profile.columns:
                    x = prov_profile.loc[common_idx, prov].to_numpy(dtype=np.float64)
                    valid = ~np.isnan(x)
                    if valid.sum() < 5:
                        continue
                    xv = x[valid] - np.mean(x[valid])
                    yv = y[valid] - np.mean(y[valid])
                    denom = np.sqrt(np.sum(xv * xv) * np.sum(yv * yv))
                    if denom > 1e-9:
                        c = float(np.sum(xv * yv) / denom)
                        if c > best_corr:
                            best_corr = c
                            best_prov = str(prov)
        results.append({
            "restaurante_id": rid,
            "provincia_inferida": best_prov,
            "ccaa_inferida": prov_to_ccaa.get(best_prov, "comunidad-de-madrid"),
            "corr_score": round(float(best_corr), 4),
            "n_regional_days": n_reg,
        })
    return pd.DataFrame(results)


def _find_reforma_pairs(ventas: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Identify >=7-day restaurant closures and expand +-7d exclusion pairs."""
    rest_days = (
        ventas[["restaurante_id", "fecha"]]
        .drop_duplicates()
        .sort_values(["restaurante_id", "fecha"])
        .reset_index(drop=True)
    )
    rest_days["prev_fecha"] = rest_days.groupby("restaurante_id", sort=False)["fecha"].shift(1)
    rest_days["gap_days"] = (rest_days["fecha"] - rest_days["prev_fecha"]).dt.days - 1
    reformas = rest_days.loc[rest_days["gap_days"] >= REFORMA_MIN_GAP_DAYS].copy()

    if reformas.empty:
        empty_pairs = pd.DataFrame(columns=["restaurante_id", "fecha", "in_reforma_excl"])
        return reformas, empty_pairs

    rows = []
    margin = REFORMA_MARGIN_DAYS - 1
    for rid, prev_dt, reopen_dt in zip(
        reformas["restaurante_id"], reformas["prev_fecha"], reformas["fecha"]
    ):
        for d in pd.date_range(prev_dt - pd.Timedelta(days=margin), prev_dt, freq="D"):
            rows.append((rid, d))
        for d in pd.date_range(reopen_dt, reopen_dt + pd.Timedelta(days=margin), freq="D"):
            rows.append((rid, d))
    pairs = pd.DataFrame(rows, columns=["restaurante_id", "fecha"]).drop_duplicates()
    pairs["in_reforma_excl"] = True
    return reformas, pairs


def prepare_silver_data(
    ventas: pd.DataFrame,
    restaurantes: pd.DataFrame,
    calendario: pd.DataFrame,
    use_inferred_holidays_for_orphans: bool = True,
) -> Dict[str, object]:
    """Join sales, master, and calendar; compute clean dimensions and exclusion flags."""
    nat_cal = build_national_calendar_fallback(calendario)
    orphan_inf = infer_orphan_provinces(ventas, restaurantes, calendario)
    reformas_df, reforma_pairs = _find_reforma_pairs(ventas)

    # Build clean restaurants_dim matching sql/schema.sql
    ak_set = set(ventas.loc[ventas["canal"] == "autoking", "restaurante_id"].unique())
    dom_set = set(ventas.loc[ventas["canal"] == "domicilio", "restaurante_id"].unique())
    med_daily = (
        ventas.groupby(["restaurante_id", "fecha"], sort=False)["ventas"]
        .sum()
        .groupby("restaurante_id")
        .median()
        .round(2)
    )

    dim = restaurantes.merge(
        orphan_inf[["restaurante_id", "provincia_inferida", "ccaa_inferida", "corr_score"]],
        on="restaurante_id",
        how="left",
    )
    dim["is_orphan_master"] = dim["provincia"] == ""
    dim["comunidad_autonoma"] = _clean_cat(dim["comunidad_autonoma"])
    dim["provincia"] = _clean_cat(dim["provincia"])
    dim["tipologia"] = _clean_cat(dim["tipologia"])
    dim["tamano"] = _clean_cat(dim["tamano"])
    dim["ubicacion"] = _clean_cat(dim["ubicacion"])
    dim["imagen"] = _clean_cat(dim["imagen"])
    dim["terraza"] = _clean_cat(dim["terraza"])
    dim["distancia_reparto"] = _clean_cat(dim["distancia_reparto"])
    dim["estado"] = _clean_cat(dim["estado"])
    dim["tiene_autoking"] = dim["restaurante_id"].isin(ak_set)
    dim["tiene_domicilio"] = dim["restaurante_id"].isin(dom_set)
    dim["primera_venta"] = pd.to_datetime(dim["primera_venta"], format="%Y-%m-%d").dt.date
    dim["ultima_venta"] = pd.to_datetime(dim["ultima_venta"], format="%Y-%m-%d").dt.date
    dim["mediana_ventas_diaria_eur"] = dim["restaurante_id"].map(med_daily).astype(float)
    dim["total_alertas"] = 0

    dim_cols = [
        "restaurante_id", "marca", "pais", "comunidad_autonoma", "provincia",
        "provincia_inferida", "is_orphan_master", "tipologia", "tamano", "ubicacion",
        "imagen", "terraza", "tiene_autoking", "tiene_domicilio", "distancia_reparto",
        "estado", "primera_venta", "ultima_venta", "mediana_ventas_diaria_eur", "total_alertas",
    ]
    restaurants_dim = dim[dim_cols].copy()

    # Effective geographic key for Peer-Group & optional orphan regional holidays
    dim_join = dim[[
        "restaurante_id", "comunidad_autonoma", "provincia", "provincia_inferida",
        "ccaa_inferida", "corr_score", "is_orphan_master", "tipologia", "tamano", "ubicacion",
    ]].copy()
    dim_join["provincia_eff"] = np.where(
        dim_join["is_orphan_master"],
        dim_join["provincia_inferida"].fillna("desconocido"),
        dim_join["provincia"],
    )
    dim_join["ccaa_eff"] = np.where(
        dim_join["is_orphan_master"],
        dim_join["ccaa_inferida"].fillna("desconocido"),
        dim_join["comunidad_autonoma"],
    )
    use_prov = dim_join["is_orphan_master"] & (dim_join["corr_score"].fillna(-1.0) >= 0.35)
    dim_join["cal_prov_key"] = np.where(
        dim_join["is_orphan_master"],
        np.where(use_prov & use_inferred_holidays_for_orphans, dim_join["provincia_inferida"], "__ORPHAN__"),
        dim_join["provincia"],
    )
    dim_join = dim_join.drop(columns=["corr_score"])

    df = ventas.merge(dim_join, on="restaurante_id", how="left")

    cal_cols = [
        "fecha", "provincia", "dia_semana", "tipo_festivo", "vispera_festivo",
        "post_festivo", "puente", "fin_de_semana_largo", "navidad", "semana_santa",
        "fecha_especial", "tipo_dia", "periodo_especial",
    ]
    df = df.merge(
        calendario[cal_cols].rename(columns={"provincia": "cal_prov_key"}),
        on=["fecha", "cal_prov_key"],
        how="left",
    )
    df = df.merge(nat_cal.drop(columns=["day_idx"]), on="fecha", how="left", suffixes=("", "_drop"))

    # Fill missing calendar columns on orphans with national fallback
    df["dia_semana"] = df["dia_semana"].fillna(df["dia_semana_nat"]).astype(np.int16)
    df["tipo_festivo"] = df["tipo_festivo"].fillna(df["tipo_festivo_nat"]).astype(str)
    df["vispera_festivo"] = df["vispera_festivo"].fillna(df["vispera_festivo_nat"]).astype(np.int16) == 1
    df["post_festivo"] = df["post_festivo"].fillna(df["post_festivo_nat"]).astype(np.int16) == 1
    df["puente"] = df["puente"].fillna(df["puente_nat"]).astype(np.int16) == 1
    df["fin_de_semana_largo"] = df["fin_de_semana_largo"].fillna(df["fin_de_semana_largo_nat"]).astype(str)
    df["navidad"] = df["navidad"].fillna(df["navidad_nat"]).astype(np.int16) == 1
    df["semana_santa"] = df["semana_santa"].fillna(df["semana_santa_nat"]).astype(np.int16) == 1
    df["fecha_especial"] = df["fecha_especial"].fillna(df["fecha_especial_nat"]).astype(str)
    df["tipo_dia"] = df["tipo_dia"].fillna(df["tipo_dia_nat"]).astype(str)
    df["periodo_especial"] = df["periodo_especial"].fillna(df["periodo_especial_nat"]).astype(np.int16) == 1

    drop_nat_cols = [c for c in df.columns if c.endswith("_nat")] + ["cal_prov_key"]
    df = df.drop(columns=drop_nat_cols)

    # Lifecycle boundaries at restaurant and (restaurant, canal) level
    origin_ts = pd.Timestamp(ORIGIN_DATE)
    end_ts = pd.Timestamp(EVAL_END)
    eval_start_ts = pd.Timestamp(EVAL_START)

    df["rest_min_fecha"] = df.groupby("restaurante_id", sort=False)["fecha"].transform("min")
    df["rest_max_fecha"] = df.groupby("restaurante_id", sort=False)["fecha"].transform("max")
    df["ch_min_fecha"] = df.groupby(["restaurante_id", "canal"], sort=False)["fecha"].transform("min")
    df["ch_max_fecha"] = df.groupby(["restaurante_id", "canal"], sort=False)["fecha"].transform("max")

    df["in_warmup"] = df["fecha"] < eval_start_ts
    mmdd = df["fecha"].dt.strftime("%m-%d")
    df["in_special_mmdd"] = mmdd.isin(SPECIAL_MMDD)
    df["in_holiday_excl"] = (
        (df["tipo_festivo"] != "")
        | df["vispera_festivo"]
        | (df["fecha_especial"] != "")
        | df["in_special_mmdd"]
    )

    in_rampup = (
        ((df["rest_min_fecha"] > origin_ts) & ((df["fecha"] - df["rest_min_fecha"]).dt.days < RAMPUP_DAYS))
        | ((df["ch_min_fecha"] > origin_ts) & ((df["fecha"] - df["ch_min_fecha"]).dt.days < RAMPUP_DAYS))
    )
    in_rampdown = (
        ((df["rest_max_fecha"] < end_ts) & ((df["rest_max_fecha"] - df["fecha"]).dt.days < RAMPDOWN_DAYS))
        | ((df["ch_max_fecha"] < end_ts) & ((df["ch_max_fecha"] - df["fecha"]).dt.days < RAMPDOWN_DAYS))
    )
    df["in_rampup"] = in_rampup
    df["in_rampdown"] = in_rampdown
    df["in_lifecycle_excl"] = in_rampup | in_rampdown

    if not reforma_pairs.empty:
        df = df.merge(reforma_pairs, on=["restaurante_id", "fecha"], how="left")
        df["in_reforma_excl"] = df["in_reforma_excl"].eq(True)
    else:
        df["in_reforma_excl"] = False

    # Assign unique series_id (0..n_series-1) for fast dense grid computations
    series_keys = (
        df[["restaurante_id", "canal"]]
        .drop_duplicates()
        .sort_values(["restaurante_id", "canal"])
        .reset_index(drop=True)
    )
    series_keys["series_id"] = np.arange(len(series_keys), dtype=np.int32)
    df = df.merge(series_keys, on=["restaurante_id", "canal"], how="left")

    return {
        "df": df,
        "restaurants_dim": restaurants_dim,
        "orphan_inference": orphan_inf,
        "reformas": reformas_df,
        "national_calendar": nat_cal,
        "n_series": int(len(series_keys)),
        "n_days": N_DAYS,
    }
