"""Gold-layer scoring, business prioritization, episode grouping, and export tables."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from databricks.src.config import EVAL_END, EVAL_START

SERIES_SUMMARY_COLS = [
    "restaurante_id", "canal", "fecha", "ventas", "tickets", "ticket_medio",
    "ventas_esperadas", "ventas_p05", "ventas_p95",
    "tickets_esperados", "tickets_p05", "tickets_p95", "ticket_medio_esperado",
    "impacto_estimado_eur", "desviacion_ventas_pct", "desviacion_tickets_pct",
    "z_ventas", "z_tickets", "z_ticket_medio", "z_peer_diff", "z_cross_channel",
    "dia_semana", "tipo_festivo", "vispera_festivo", "fecha_especial",
    "navidad", "semana_santa", "is_eligible_for_alert", "exclusion_reason",
    "is_systemic_day", "is_anomaly", "anomaly_tipo",
]

ALERTS_INSERT_COLS = [
    "restaurante_id", "canal", "fecha", "tipo", "score",
    "episode_id", "episode_start_date", "episode_end_date",
    "episode_days", "episode_day_index", "episode_total_impact_eur",
    "comunidad_autonoma", "provincia", "tipologia", "ubicacion",
    "priority_score", "prioridad_nivel", "metrica_principal",
    "ventas_reales", "ventas_esperadas", "impacto_estimado_eur",
    "desviacion_ventas_pct", "tickets_reales", "tickets_esperados",
    "desviacion_tickets_pct", "ticket_medio_real", "ticket_medio_esperado",
    "contribucion_tickets_pct", "contribucion_tm_pct",
    "z_ventas", "z_tickets", "z_ticket_medio", "z_max",
    "z_peer_diff", "peer_delta_pct", "z_cross_channel", "otros_canales_normales",
    "explicacion_negocio", "recomendacion_revision", "evidencia_json",
    "llm_cached", "run_id", "created_at",
]


def _assign_episodes(alerts: pd.DataFrame) -> pd.DataFrame:
    """Group consecutive alert days of same (restaurante_id, canal, tipo) into UX episodes."""
    if alerts.empty:
        return alerts
    a = alerts.sort_values(["restaurante_id", "canal", "tipo", "fecha"]).reset_index(drop=True)
    fecha_dt = pd.to_datetime(a["fecha"])
    prev_dt = fecha_dt.groupby([a["restaurante_id"], a["canal"], a["tipo"]], sort=False).shift(1)
    same_grp = (
        (a["restaurante_id"] == a["restaurante_id"].shift(1))
        & (a["canal"] == a["canal"].shift(1))
        & (a["tipo"] == a["tipo"].shift(1))
        & ((fecha_dt - prev_dt).dt.days == 1)
    )
    ep_num = (~same_grp).cumsum()
    a["episode_start_date"] = fecha_dt.groupby(ep_num, sort=False).transform("min").dt.date
    a["episode_end_date"] = fecha_dt.groupby(ep_num, sort=False).transform("max").dt.date
    a["episode_days"] = a.groupby(ep_num, sort=False)["fecha"].transform("count").astype(np.int16)
    a["episode_day_index"] = (a.groupby(ep_num, sort=False).cumcount() + 1).astype(np.int16)
    a["episode_total_impact_eur"] = np.round(
        a.groupby(ep_num, sort=False)["impacto_estimado_eur"].transform("sum"), 2
    )
    start_str = pd.to_datetime(a["episode_start_date"]).dt.strftime("%Y%m%d")
    a["episode_id"] = a["restaurante_id"] + "_" + a["canal"] + "_" + start_str
    return a


CANAL_LABELS = {"sala": "Sala", "domicilio": "Domicilio", "autoking": "AutoKing"}

CCAA_LABELS = {
    "andalucia": "Andalucía",
    "aragon": "Aragón",
    "asturias": "Asturias",
    "canarias": "Canarias",
    "cantabria": "Cantabria",
    "castilla-la-mancha": "Castilla-La Mancha",
    "castilla-y-leon": "Castilla y León",
    "cataluna": "Cataluña",
    "ceuta": "Ceuta",
    "comunidad-de-madrid": "Comunidad de Madrid",
    "comunidad-valenciana": "Comunidad Valenciana",
    "extremadura": "Extremadura",
    "galicia": "Galicia",
    "islas-baleares": "Illes Balears",
    "la-rioja": "La Rioja",
    "melilla": "Melilla",
    "navarra": "Navarra",
    "pais-vasco": "País Vasco",
    "region-de-murcia": "Región de Murcia",
}


def _fmt_es(val: float, decimals: int = 0, sign: bool = False) -> str:
    """Format number using Spanish thousands '.' and decimal ',' separators."""
    s = f"{abs(val):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if val < 0:
        return f"-{s}"
    if sign and val > 0:
        return f"+{s}"
    return s


def _build_explanations(a: pd.DataFrame) -> Tuple[list, list, list]:
    """Generate grounded plain-Spanish explanations, operational checks, and evidence JSON."""
    exps, recs, evs = [], [], []
    for row in a.itertuples(index=False):
        canal_lbl = CANAL_LABELS.get(row.canal, row.canal)
        dir_word = "caída" if row.tipo == "caida" else "subida"
        tk_word = "ticket" if int(row.tickets_reales) == 1 else "tickets"
        dv_str = _fmt_es(float(row.desviacion_ventas_pct), 1, sign=True)
        dt_str = _fmt_es(float(row.desviacion_tickets_pct), 1, sign=True)
        if row.metrica_principal == "tickets":
            msg = (
                f"{canal_lbl}: {dir_word} atípica de tickets ({_fmt_es(int(row.tickets_reales))} {tk_word} frente a "
                f"{_fmt_es(float(row.tickets_esperados))} esperados, {dt_str} %), con ticket medio de "
                f"{_fmt_es(float(row.ticket_medio_real), 2)} € frente a {_fmt_es(float(row.ticket_medio_esperado), 2)} €."
            )
        else:
            msg = (
                f"{canal_lbl}: {dir_word} atípica de ventas ({_fmt_es(float(row.ventas_reales))} € frente a "
                f"{_fmt_es(float(row.ventas_esperadas))} € esperados, {dv_str} %; impacto {_fmt_es(float(row.impacto_estimado_eur), 0, sign=True)} €) "
                f"y {_fmt_es(int(row.tickets_reales))} {tk_word} ({dt_str} %)."
            )
        if row.otros_canales_normales:
            msg += " Los demás canales del restaurante operaron dentro de lo normal."
        if abs(row.peer_delta_pct) >= 15.0:
            geo_slug = str(row.comunidad_autonoma or "")
            geo_lbl = CCAA_LABELS.get(geo_slug, geo_slug if geo_slug not in ("", "desconocido") else "su referencia nacional")
            msg += f" Frente a locales comparables de {geo_lbl} se desvía {_fmt_es(float(row.peer_delta_pct), 1, sign=True)} %."
        if row.episode_days > 1:
            msg += f" Episodio activo de {row.episode_days} días (día {row.episode_day_index}/{row.episode_days})."

        if row.tipo == "caida" and row.canal == "domicilio" and row.otros_canales_normales:
            rec = "Confirmar con operaciones si el canal domicilio estuvo activo durante toda la jornada y sin cierres parciales de plataformas o incidencias de registro."
        elif row.tipo == "caida" and row.canal == "autoking" and row.otros_canales_normales:
            rec = "Confirmar con operaciones si el carril AutoKing operó todo el horario previsto y si las ventas se registraron completas."
        elif row.tipo == "caida":
            rec = "Confirmar con operaciones si el local operó en su horario habitual y verificar el registro completo de ventas en el canal."
        elif row.metrica_principal == "tickets":
            rec = "Revisar si la subida inusual de tickets responde a campañas locales, redención de cupones o división de pedidos antes de usar el día en entrenamiento."
        else:
            rec = "Verificar que las ventas no estén duplicadas y confirmar si existió algún evento o acción comercial puntual antes de ajustar la previsión."

        ev = {
            "restaurante_id": row.restaurante_id,
            "canal": row.canal,
            "fecha": str(row.fecha),
            "tipo": row.tipo,
            "ventas_reales": round(float(row.ventas_reales), 2),
            "ventas_esperadas": round(float(row.ventas_esperadas), 2),
            "impacto_eur": round(float(row.impacto_estimado_eur), 2),
            "desviacion_ventas_pct": round(float(row.desviacion_ventas_pct), 2),
            "tickets_reales": int(row.tickets_reales),
            "tickets_esperados": round(float(row.tickets_esperados), 2),
            "desviacion_tickets_pct": round(float(row.desviacion_tickets_pct), 2),
            "ticket_medio_real": round(float(row.ticket_medio_real), 2),
            "ticket_medio_esperado": round(float(row.ticket_medio_esperado), 2),
            "peer_delta_pct": round(float(row.peer_delta_pct), 2),
            "otros_canales_normales": bool(row.otros_canales_normales),
            "episode_days": int(row.episode_days),
        }
        exps.append(msg)
        recs.append(rec)
        evs.append(json.dumps(ev, ensure_ascii=False))
    return exps, recs, evs


def build_gold_outputs(
    df_features: pd.DataFrame,
    restaurants_dim: pd.DataFrame,
    pred_alerts: pd.DataFrame,
    run_id: str,
    model_version: str,
    synthetic_metrics: Dict[str, float],
    params_dict: Dict[str, object],
) -> Dict[str, pd.DataFrame]:
    """Assemble contract-compliant Gold tables (alerts, series_summary, restaurants_dim, model_runs)."""
    now_ts = datetime.now(timezone.utc)

    # Deduplicate safety guarantee matching validar_entrega.py
    p = pred_alerts.sort_values("score", ascending=False, kind="mergesort").drop_duplicates(
        subset=["restaurante_id", "canal", "fecha"], keep="first"
    ).copy()
    p["fecha_dt"] = pd.to_datetime(p["fecha"]).dt.floor("D")

    join_cols = [
        "restaurante_id", "canal", "fecha",
        "comunidad_autonoma", "provincia", "provincia_eff", "tipologia", "ubicacion",
        "ventas", "ventas_esperadas", "impacto_estimado_eur", "desviacion_ventas_pct",
        "tickets", "tickets_esperados", "desviacion_tickets_pct",
        "ticket_medio", "ticket_medio_esperado",
        "z_ventas", "z_tickets", "z_ticket_medio", "z_max",
        "z_peer_diff", "peer_delta_pct", "z_cross_channel", "otros_canales_normales",
    ]
    a = p[["restaurante_id", "canal", "fecha_dt", "tipo", "score"]].rename(
        columns={"fecha_dt": "fecha"}
    ).merge(df_features[join_cols], on=["restaurante_id", "canal", "fecha"], how="inner")

    a = a.rename(columns={
        "ventas": "ventas_reales",
        "tickets": "tickets_reales",
        "ticket_medio": "ticket_medio_real",
    })
    a["fecha"] = pd.to_datetime(a["fecha"]).dt.date

    # Primary metric classification
    zv = np.abs(a["z_ventas"].to_numpy(dtype=np.float64))
    zt = np.abs(a["z_tickets"].to_numpy(dtype=np.float64))
    ztm = np.abs(a["z_ticket_medio"].to_numpy(dtype=np.float64))
    a["metrica_principal"] = np.where(
        (zv >= 2.5) & (zt >= 2.5),
        "ambas",
        np.where(
            (zt > zv) & (zt > ztm),
            "tickets",
            np.where((ztm > zv) & (ztm >= 2.5), "ticket_medio", "ventas"),
        ),
    )

    # Multiplicative log decomposition
    dt = np.log(np.maximum(a["tickets_reales"].to_numpy(dtype=np.float64), 1.0) / np.maximum(a["tickets_esperados"].to_numpy(dtype=np.float64), 1.0))
    dtm = np.log(np.maximum(a["ticket_medio_real"].to_numpy(dtype=np.float64), 0.1) / np.maximum(a["ticket_medio_esperado"].to_numpy(dtype=np.float64), 0.1))
    denom = np.abs(dt) + np.abs(dtm)
    safe_denom = np.where(denom > 1e-6, denom, 1.0)
    a["contribucion_tickets_pct"] = np.round(np.where(denom > 1e-6, 100.0 * dt / safe_denom, 50.0), 2)
    a["contribucion_tm_pct"] = np.round(np.where(denom > 1e-6, 100.0 * dtm / safe_denom, 50.0), 2)

    a = _assign_episodes(a)

    # Priority score (0..100)
    if not a.empty:
        e_p99 = max(500.0, float(np.quantile(np.abs(a["impacto_estimado_eur"]), 0.99)))
        e_val = np.maximum(
            np.abs(a["episode_total_impact_eur"].to_numpy(dtype=np.float64)),
            np.abs(a["impacto_estimado_eur"].to_numpy(dtype=np.float64)),
        )
        i_norm = np.clip(np.log1p(e_val) / np.log1p(e_p99), 0.0, 1.0)
        s_norm = np.clip(a["z_max"].to_numpy(dtype=np.float64) / 6.0, 0.0, 1.0)
        p_norm = 1.0 - np.exp(-a["episode_days"].to_numpy(dtype=np.float64) / 3.0)
        w_dir = np.where(a["tipo"].to_numpy() == "caida", 1.0, 0.80)
        pscore = np.round(100.0 * w_dir * (0.45 * i_norm + 0.30 * s_norm + 0.25 * p_norm), 2)
        a["priority_score"] = np.clip(pscore, 0.0, 100.0)
        a["prioridad_nivel"] = np.where(
            a["priority_score"] >= 75.0, "P1", np.where(a["priority_score"] >= 50.0, "P2", "P3")
        )
    else:
        a["priority_score"] = np.array([], dtype=np.float64)
        a["prioridad_nivel"] = np.array([], dtype=object)

    exps, recs, evs = _build_explanations(a)
    a["explicacion_negocio"] = exps
    a["recomendacion_revision"] = recs
    a["evidencia_json"] = evs
    a["llm_cached"] = False
    a["run_id"] = run_id
    a["created_at"] = now_ts

    a = a.sort_values(["score", "restaurante_id", "canal", "fecha"], ascending=[False, True, True, True]).reset_index(drop=True)
    # Monotone calibration into [0.50, 0.999999] preserving exact descending ranking for FilterBar slider & Copilot
    if not a.empty:
        raw_s = a["score"].to_numpy(dtype=np.float64)
        s_min = float(np.min(raw_s))
        excess = np.maximum(raw_s - s_min, 0.0)
        scale = max(float(np.quantile(excess, 0.85)), 1e-6)
        cal_s = 0.50 + 0.495 * (1.0 - np.exp(-excess / scale))
        rank_eps = np.linspace(0.0049, 0.0, len(raw_s))
        a["score"] = np.round(np.clip(cal_s + rank_eps, 0.50, 0.999999), 6)
    for col in ("ventas_reales", "ventas_esperadas", "impacto_estimado_eur", "desviacion_ventas_pct",
                "tickets_esperados", "desviacion_tickets_pct", "ticket_medio_real", "ticket_medio_esperado",
                "peer_delta_pct", "episode_total_impact_eur"):
        a[col] = np.round(a[col].astype(np.float64), 2)
    for col in ("z_ventas", "z_tickets", "z_ticket_medio", "z_max", "z_peer_diff", "z_cross_channel"):
        a[col] = np.round(a[col].astype(np.float64), 3)
    alerts_prioritized = a[ALERTS_INSERT_COLS].copy()

    # Build series_summary with anomaly flags synced 1:1 with alerts_prioritized
    s = df_features.copy()
    s["fecha"] = pd.to_datetime(s["fecha"]).dt.date
    anom_map = a[["restaurante_id", "canal", "fecha", "tipo"]].rename(columns={"tipo": "anomaly_tipo"})
    s = s.merge(anom_map, on=["restaurante_id", "canal", "fecha"], how="left")
    s["is_anomaly"] = s["anomaly_tipo"].notna()
    for col in ("impacto_estimado_eur", "desviacion_ventas_pct", "desviacion_tickets_pct"):
        s[col] = np.round(s[col].astype(np.float64), 2)
    for col in ("z_ventas", "z_tickets", "z_ticket_medio", "z_peer_diff", "z_cross_channel"):
        s[col] = np.round(s[col].astype(np.float64), 3)
    series_summary = s[SERIES_SUMMARY_COLS].copy()

    # Update restaurants_dim.total_alertas
    cnt_map = a.groupby("restaurante_id").size().to_dict()
    dim = restaurants_dim.copy()
    dim["total_alertas"] = dim["restaurante_id"].map(cnt_map).fillna(0).astype(np.int32)

    # Build model_runs row
    n_caidas = int((alerts_prioritized["tipo"] == "caida").sum())
    n_subidas = int((alerts_prioritized["tipo"] == "subida").sum())
    model_runs = pd.DataFrame([{
        "run_id": run_id,
        "model_version": model_version,
        "is_active": True,
        "created_at": now_ts,
        "eval_start_date": pd.to_datetime(EVAL_START).date(),
        "eval_end_date": pd.to_datetime(EVAL_END).date(),
        "total_candidate_rows": int(df_features["is_eligible_for_alert"].sum()),
        "total_alerts": int(len(alerts_prioritized)),
        "total_caidas": n_caidas,
        "total_subidas": n_subidas,
        "f1_synthetic": round(float(synthetic_metrics.get("f1", 0.0)), 4),
        "precision_synthetic": round(float(synthetic_metrics.get("precision", 0.0)), 4),
        "recall_synthetic": round(float(synthetic_metrics.get("recall", 0.0)), 4),
        "precision_at_100_synthetic": round(float(synthetic_metrics.get("precision_at_100", 0.0)), 4),
        "params_json": json.dumps(params_dict, ensure_ascii=False),
    }])

    return {
        "model_runs": model_runs,
        "restaurants_dim": dim,
        "series_summary": series_summary,
        "alerts_prioritized": alerts_prioritized,
    }


def export_anomalias_csv(alerts_prioritized: pd.DataFrame, out_path: Path) -> Path:
    """Export submission CSV strictly formatted for validar_entrega.py with recommendation column."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sub = pd.DataFrame({
        "restaurante_id": alerts_prioritized["restaurante_id"],
        "canal": alerts_prioritized["canal"],
        "fecha": pd.to_datetime(alerts_prioritized["fecha"]).dt.strftime("%Y-%m-%d"),
        "tipo": alerts_prioritized["tipo"],
        "score": np.round(alerts_prioritized["score"].astype(np.float64), 6),
        "impacto_estimado_eur": np.round(alerts_prioritized["impacto_estimado_eur"].astype(np.float64), 2),
        "explicacion": alerts_prioritized["explicacion_negocio"],
        "recomendacion": alerts_prioritized["recomendacion_revision"],
    })
    sub.to_csv(out_path, index=False, encoding="utf-8")
    return out_path
