"""Counterfactual dual baseline (LOO + OOF LightGBM Quantile + CQR) and Layer-2 signals."""

from __future__ import annotations

import warnings
from typing import Dict, Tuple

import lightgbm as lgb
import numpy as np
import pandas as pd

from databricks.src.config import N_DAYS, SYSTEMIC_SHARE_THRESHOLD, SYSTEMIC_Z_THRESHOLD

LOO_OFFSETS = (-56, -49, -42, -35, -28, -21, -14, 14, 21, 28, 35, 42, 49, 56)
WIDE_OFFSETS = (-112, -105, -98, -91, -84, -77, -70, -63, 63, 70, 77, 84, 91, 98, 105, 112)


def _nanmedian_axis0(arr: np.ndarray) -> np.ndarray:
    """Compute nanmedian along axis=0 without all-NaN runtime warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmedian(arr, axis=0)


def _shifted_grid_stack(grid: np.ndarray, offsets: Tuple[int, ...]) -> np.ndarray:
    """Stack shifted views of a (n_series, n_days) grid along axis=0."""
    n_series, n_days = grid.shape
    stack = np.full((len(offsets), n_series, n_days), np.nan, dtype=np.float64)
    for k, off in enumerate(offsets):
        if off < 0:
            shift = -off
            if shift < n_days:
                stack[k, :, shift:] = grid[:, :-shift]
        elif off > 0:
            if off < n_days:
                stack[k, :, :-off] = grid[:, off:]
    return stack


def _compute_bilateral_loo(clean_grid: np.ndarray, raw_grid: np.ndarray) -> np.ndarray:
    """Compute bilateral same-DOW median excluding [t-13, t+13] with hierarchical fallbacks."""
    n_series, n_days = raw_grid.shape
    near_stack = _shifted_grid_stack(clean_grid, LOO_OFFSETS)
    near_cnt = np.sum(~np.isnan(near_stack), axis=0)
    loo = _nanmedian_axis0(near_stack)

    # Fallback 1: wider same-DOW window (+-63..112d) where <3 near points exist
    need_wide = (near_cnt < 3) & (~np.isnan(raw_grid))
    if np.any(need_wide):
        wide_stack = _shifted_grid_stack(clean_grid, WIDE_OFFSETS)
        comb = np.concatenate([near_stack[:, need_wide], wide_stack[:, need_wide]], axis=0)
        wide_med = _nanmedian_axis0(comb)
        cur = loo[need_wide]
        loo[need_wide] = np.where(np.isnan(wide_med), cur, wide_med)

    # Fallback 2: series-level same-DOW median on clean observations
    need_dow = np.isnan(loo) & (~np.isnan(raw_grid))
    if np.any(need_dow):
        dow_med = np.full((n_series, n_days), np.nan, dtype=np.float64)
        for dow in range(7):
            cols = np.arange(dow, n_days, 7)
            med_d = _nanmedian_axis0(clean_grid[:, cols].T)[:, None]
            dow_med[:, cols] = np.repeat(med_d, len(cols), axis=1)
        loo = np.where(need_dow & (~np.isnan(dow_med)), dow_med, loo)

    # Fallback 3: overall clean series median, then raw series median
    still_na = np.isnan(loo) & (~np.isnan(raw_grid))
    if np.any(still_na):
        clean_series_med = _nanmedian_axis0(clean_grid.T)[:, None]
        raw_series_med = _nanmedian_axis0(raw_grid.T)[:, None]
        series_med = np.where(~np.isnan(clean_series_med), clean_series_med, raw_series_med)
        loo = np.where(still_na, np.repeat(series_med, n_days, axis=1), loo)

    return loo


def _series_mad_scale(
    res_grid: np.ndarray, clean_mask: np.ndarray, min_sigma: float = 0.045
) -> np.ndarray:
    """Compute robust per-series scale (1.4826 * MAD) on clean observations."""
    clean_res = np.where(clean_mask, res_grid, np.nan)
    med = _nanmedian_axis0(clean_res.T)[:, None]
    mad = _nanmedian_axis0(np.abs(clean_res - med).T)
    sigma = np.maximum(1.4826 * mad, min_sigma)
    sigma = np.where(np.isnan(sigma), min_sigma * 2.0, sigma)
    return sigma


def _loo_group_shift(
    df: pd.DataFrame, col: str, group_cols: list, valid_mask: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """Compute vectorized Leave-One-Out clipped mean and other-count per group."""
    vals = np.where(valid_mask, np.clip(df[col].to_numpy(dtype=np.float64), -0.65, 0.65), 0.0)
    cnts = valid_mask.astype(np.float64)
    tmp = pd.DataFrame({"val": vals, "cnt": cnts})
    for gc in group_cols:
        tmp[gc] = df[gc].values
    grp = tmp.groupby(group_cols, sort=False)
    tot_sum = grp["val"].transform("sum").to_numpy(dtype=np.float64)
    tot_cnt = grp["cnt"].transform("sum").to_numpy(dtype=np.float64)
    other_cnt = tot_cnt - cnts
    other_sum = tot_sum - vals
    mean_other = np.where(other_cnt > 0, other_sum / np.maximum(other_cnt, 1.0), 0.0)
    return mean_other, other_cnt


def _compute_hierarchical_peer_shift(df: pd.DataFrame, res_col: str, z_col: str) -> np.ndarray:
    """Compute LOO hierarchical peer shift: (prov, canal, tip) -> (ccaa, canal) -> (canal)."""
    valid_peer = (np.abs(df[z_col].to_numpy(dtype=np.float64)) <= 4.5) & (~df["in_lifecycle_excl"].to_numpy())

    m1, c1 = _loo_group_shift(df, res_col, ["fecha", "provincia_eff", "canal", "tipologia"], valid_peer)
    m2, c2 = _loo_group_shift(df, res_col, ["fecha", "ccaa_eff", "canal"], valid_peer)
    m3, c3 = _loo_group_shift(df, res_col, ["fecha", "canal"], valid_peer)

    ok1 = (c1 >= 7) & (df["provincia_eff"].values != "desconocido") & (df["tipologia"].values != "desconocido")
    ok2 = (c2 >= 7) & (df["ccaa_eff"].values != "desconocido")
    shift = np.where(ok1, m1, np.where(ok2, m2, np.where(c3 >= 5, m3, 0.0)))
    return np.clip(shift, -0.65, 0.65)


def _compute_systemic_flag(df: pd.DataFrame, z_raw_col: str) -> np.ndarray:
    """Flag dates where >=35% of active stores in Spain or a CCAA move in the same direction."""
    z = df[z_raw_col].to_numpy(dtype=np.float64)
    pos = (z > SYSTEMIC_Z_THRESHOLD).astype(np.float64)
    neg = (z < -SYSTEMIC_Z_THRESHOLD).astype(np.float64)
    tmp = pd.DataFrame({
        "fecha": df["fecha"].values,
        "canal": df["canal"].values,
        "ccaa": df["ccaa_eff"].values,
        "pos": pos,
        "neg": neg,
    })

    g_nat = tmp.groupby(["fecha", "canal"], sort=False)
    nat_pos = g_nat["pos"].transform("mean").to_numpy()
    nat_neg = g_nat["neg"].transform("mean").to_numpy()

    g_ccaa = tmp.groupby(["fecha", "canal", "ccaa"], sort=False)
    ccaa_n = g_ccaa["pos"].transform("count").to_numpy()
    ccaa_pos = g_ccaa["pos"].transform("mean").to_numpy()
    ccaa_neg = g_ccaa["neg"].transform("mean").to_numpy()

    known_ccaa = df["ccaa_eff"].to_numpy() != "desconocido"
    sys_nat = (nat_pos >= SYSTEMIC_SHARE_THRESHOLD) | (nat_neg >= SYSTEMIC_SHARE_THRESHOLD)
    sys_ccaa = known_ccaa & (ccaa_n >= 5) & (
        (ccaa_pos >= SYSTEMIC_SHARE_THRESHOLD) | (ccaa_neg >= SYSTEMIC_SHARE_THRESHOLD)
    )
    return sys_nat | sys_ccaa


def _encode_categories(df: pd.DataFrame) -> pd.DataFrame:
    """Create deterministic integer codes for LightGBM categorical splits."""
    cat_cols = ["canal", "tipologia", "tamano", "ubicacion", "fin_de_semana_largo", "tipo_dia"]
    out = pd.DataFrame(index=df.index)
    for c in cat_cols:
        codes, _ = pd.factorize(df[c].astype(str), sort=True)
        out[f"{c}_code"] = codes.astype(np.int16)
    return out


def compute_diagnostic_signals(
    df: pd.DataFrame,
    n_series: Optional[int] = None,
    n_days: int = N_DAYS,
) -> pd.DataFrame:
    """Compute standardized residuals, peer diff, cross-channel diff, CQR exceed, and neighbor continuity.

    Shared by both real pipeline inference and synthetic benchmark evaluation.
    """
    out = df.copy()
    if n_series is None:
        n_series = int(out["series_id"].max()) + 1

    log_v = np.log1p(out["ventas"].to_numpy(dtype=np.float64))
    log_t = np.log1p(out["tickets"].to_numpy(dtype=np.float64))
    log_tm = np.log(np.maximum(out["ticket_medio"].to_numpy(dtype=np.float64), 0.01))

    exp_log_v = np.log1p(np.maximum(out["ventas_esperadas"].to_numpy(dtype=np.float64), 1.0))
    exp_log_t = np.log1p(np.maximum(out["tickets_esperados"].to_numpy(dtype=np.float64), 1.0))
    exp_log_tm = np.log(np.maximum(out["ticket_medio_esperado"].to_numpy(dtype=np.float64), 0.5))

    sig_v = np.maximum(out["sigma_v"].to_numpy(dtype=np.float64), 0.045)
    sig_t = np.maximum(out["sigma_t"].to_numpy(dtype=np.float64), 0.045)
    sig_tm = np.maximum(out["sigma_tm"].to_numpy(dtype=np.float64), 0.035)

    res_v = log_v - exp_log_v
    res_t = log_t - exp_log_t
    res_tm = log_tm - exp_log_tm

    z_v = res_v / sig_v
    z_t = res_t / sig_t
    z_tm = res_tm / sig_tm

    out["impacto_estimado_eur"] = out["ventas"] - out["ventas_esperadas"]
    out["desviacion_ventas_pct"] = 100.0 * (out["ventas"] - out["ventas_esperadas"]) / np.maximum(
        out["ventas_esperadas"], 1.0
    )
    out["desviacion_tickets_pct"] = 100.0 * (out["tickets"] - out["tickets_esperados"]) / np.maximum(
        out["tickets_esperados"], 1.0
    )
    out["z_ventas"] = z_v
    out["z_tickets"] = z_t
    out["z_ticket_medio"] = z_tm
    out["z_max"] = np.maximum(np.abs(z_v), np.abs(z_t))

    # Peer diff (vs CCAA/province same-day residual median)
    tmp_p = pd.DataFrame({
        "fecha": out["fecha"].values,
        "canal": out["canal"].values,
        "ccaa": out["ccaa_eff"].values,
        "z_v": z_v,
        "z_t": z_t,
        "dv": out["desviacion_ventas_pct"].to_numpy(dtype=np.float64),
    })
    g_ccaa = tmp_p.groupby(["fecha", "canal", "ccaa"], sort=False)
    med_peer_zv = g_ccaa["z_v"].transform("median").to_numpy()
    med_peer_zt = g_ccaa["z_t"].transform("median").to_numpy()
    med_peer_dv = g_ccaa["dv"].transform("median").to_numpy()
    cnt_peer = g_ccaa["z_v"].transform("count").to_numpy()

    g_nat = tmp_p.groupby(["fecha", "canal"], sort=False)
    med_nat_zv = g_nat["z_v"].transform("median").to_numpy()
    med_nat_zt = g_nat["z_t"].transform("median").to_numpy()
    med_nat_dv = g_nat["dv"].transform("median").to_numpy()

    use_ccaa = (cnt_peer >= 5) & (out["ccaa_eff"].to_numpy() != "desconocido")
    peer_zv = np.where(use_ccaa, med_peer_zv, med_nat_zv)
    peer_zt = np.where(use_ccaa, med_peer_zt, med_nat_zt)
    peer_dv = np.where(use_ccaa, med_peer_dv, med_nat_dv)

    out["z_peer_diff"] = z_v - peer_zv
    out["z_peer_diff_t"] = z_t - peer_zt
    out["peer_delta_pct"] = out["desviacion_ventas_pct"] - peer_dv

    # Cross-channel signal: divergence vs sibling active channels at same restaurant & day
    dominant_z = np.where(np.abs(z_v) >= np.abs(z_t), z_v, z_t)
    tmp_x = pd.DataFrame({
        "restaurante_id": out["restaurante_id"].values,
        "fecha": out["fecha"].values,
        "dom_z": dominant_z,
        "abs_dom_z": np.abs(dominant_z),
    })
    gx = tmp_x.groupby(["restaurante_id", "fecha"], sort=False)
    sum_z = gx["dom_z"].transform("sum").to_numpy()
    cnt_ch = gx["dom_z"].transform("count").to_numpy()
    other_cnt = cnt_ch - 1
    other_mean_z = np.where(other_cnt > 0, (sum_z - dominant_z) / np.maximum(other_cnt, 1), 0.0)
    out["z_cross_channel"] = np.where(other_cnt > 0, dominant_z - other_mean_z, 0.0)
    out["otros_canales_normales"] = (other_cnt >= 1) & (np.abs(other_mean_z) < 1.8)

    # Channel share shift inside restaurant
    tot_obs_rest = out.groupby(["restaurante_id", "fecha"], sort=False)["ventas"].transform("sum").to_numpy()
    tot_exp_rest = out.groupby(["restaurante_id", "fecha"], sort=False)["ventas_esperadas"].transform("sum").to_numpy()
    share_obs = out["ventas"].to_numpy(dtype=np.float64) / np.maximum(tot_obs_rest, 1.0)
    share_exp = out["ventas_esperadas"].to_numpy(dtype=np.float64) / np.maximum(tot_exp_rest, 1.0)
    out["delta_channel_share"] = share_obs - share_exp

    # CQR exceedance (positive = outside conformal [P05, P95] interval, standardized)
    v_p05_log = np.log1p(np.maximum(out["ventas_p05"].to_numpy(dtype=np.float64), 0.5))
    v_p95_log = np.log1p(np.maximum(out["ventas_p95"].to_numpy(dtype=np.float64), 1.0))
    t_p05_log = np.log1p(np.maximum(out["tickets_p05"].to_numpy(dtype=np.float64), 0.5))
    t_p95_log = np.log1p(np.maximum(out["tickets_p95"].to_numpy(dtype=np.float64), 1.0))

    out["cqr_exceed_v"] = np.maximum(v_p05_log - log_v, log_v - v_p95_log) / sig_v
    out["cqr_exceed_t"] = np.maximum(t_p05_log - log_t, log_t - t_p95_log) / sig_t

    # Neighboring day same-sign continuity on (n_series, n_days) grid (2-4d abrupt steps)
    s_idx = out["series_id"].to_numpy(dtype=np.int32)
    d_idx = out["day_idx"].to_numpy(dtype=np.int32)
    dom_grid = np.full((n_series, n_days), np.nan, dtype=np.float64)
    dom_grid[s_idx, d_idx] = dominant_z
    prev_z = np.full(len(out), np.nan, dtype=np.float64)
    next_z = np.full(len(out), np.nan, dtype=np.float64)
    has_prev = d_idx > 0
    has_next = d_idx < (n_days - 1)
    prev_z[has_prev] = dom_grid[s_idx[has_prev], d_idx[has_prev] - 1]
    next_z[has_next] = dom_grid[s_idx[has_next], d_idx[has_next] + 1]
    prev_same = np.where((~np.isnan(prev_z)) & (prev_z * dominant_z > 0), np.abs(prev_z), 0.0)
    next_same = np.where((~np.isnan(next_z)) & (next_z * dominant_z > 0), np.abs(next_z), 0.0)
    out["z_neighbor_same_sign"] = np.maximum(prev_same, next_same)

    return out


def build_features_and_baselines(
    silver_bundle: Dict[str, object],
    n_folds: int = 6,
    purge_days: int = 14,
) -> pd.DataFrame:
    """Compute bilateral LOO, OOF LightGBM Quantile + CQR baselines, and Layer-2 signals."""
    df = silver_bundle["df"].copy()
    n_series = int(silver_bundle["n_series"])
    n_days = int(silver_bundle["n_days"])

    s_idx = df["series_id"].to_numpy(dtype=np.int32)
    d_idx = df["day_idx"].to_numpy(dtype=np.int32)

    log_v_grid = np.full((n_series, n_days), np.nan, dtype=np.float64)
    log_t_grid = np.full((n_series, n_days), np.nan, dtype=np.float64)
    log_tm_grid = np.full((n_series, n_days), np.nan, dtype=np.float64)
    clean_grid = np.zeros((n_series, n_days), dtype=bool)

    log_v_obs = np.log1p(df["ventas"].to_numpy(dtype=np.float64))
    log_t_obs = np.log1p(df["tickets"].to_numpy(dtype=np.float64))
    log_tm_obs = np.log(np.maximum(df["ticket_medio"].to_numpy(dtype=np.float64), 0.01))

    is_blackout = (df["fecha"] == pd.Timestamp("2025-04-28")).to_numpy()
    is_clean_obs = ~(
        df["in_holiday_excl"].to_numpy()
        | df["in_lifecycle_excl"].to_numpy()
        | df["in_reforma_excl"].to_numpy()
        | is_blackout
    )

    log_v_grid[s_idx, d_idx] = log_v_obs
    log_t_grid[s_idx, d_idx] = log_t_obs
    log_tm_grid[s_idx, d_idx] = log_tm_obs
    clean_grid[s_idx, d_idx] = is_clean_obs

    clean_v_grid = np.where(clean_grid, log_v_grid, np.nan)
    clean_t_grid = np.where(clean_grid, log_t_grid, np.nan)
    clean_tm_grid = np.where(clean_grid, log_tm_grid, np.nan)

    # 1. Vectorized Bilateral LOO Baseline
    loo_v_grid = _compute_bilateral_loo(clean_v_grid, log_v_grid)
    loo_t_grid = _compute_bilateral_loo(clean_t_grid, log_t_grid)
    loo_tm_grid = _compute_bilateral_loo(clean_tm_grid, log_tm_grid)

    loo_v = loo_v_grid[s_idx, d_idx]
    loo_t = loo_t_grid[s_idx, d_idx]
    loo_tm = loo_tm_grid[s_idx, d_idx]

    raw_res_v = log_v_obs - loo_v
    raw_res_t = log_t_obs - loo_t
    raw_res_tm = log_tm_obs - loo_tm

    sigma_raw_v = _series_mad_scale(log_v_grid - loo_v_grid, clean_grid, min_sigma=0.045)
    sigma_raw_t = _series_mad_scale(log_t_grid - loo_t_grid, clean_grid, min_sigma=0.045)
    sigma_raw_tm = _series_mad_scale(log_tm_grid - loo_tm_grid, clean_grid, min_sigma=0.035)

    df["loo_log_v"] = loo_v
    df["loo_log_t"] = loo_t
    df["raw_res_v"] = raw_res_v
    df["raw_res_t"] = raw_res_t
    df["z_raw_v"] = raw_res_v / sigma_raw_v[s_idx]
    df["z_raw_t"] = raw_res_t / sigma_raw_t[s_idx]

    # 2. Hierarchical LOO Peer-Group Shift & Systemic Day Detection
    df["peer_shift_v"] = _compute_hierarchical_peer_shift(df, "raw_res_v", "z_raw_v")
    df["peer_shift_t"] = _compute_hierarchical_peer_shift(df, "raw_res_t", "z_raw_t")
    df["is_systemic_day"] = _compute_systemic_flag(df, "z_raw_v")

    # 3. Purged Lags / Leads (>=14d from target day t) relative to LOO anchor
    feat_df = pd.DataFrame(index=df.index)
    for lag in (14, 21, 28, 35):
        valid = d_idx >= lag
        lv = np.full(len(df), np.nan, dtype=np.float64)
        lt = np.full(len(df), np.nan, dtype=np.float64)
        lv[valid] = clean_v_grid[s_idx[valid], d_idx[valid] - lag] - loo_v[valid]
        lt[valid] = clean_t_grid[s_idx[valid], d_idx[valid] - lag] - loo_t[valid]
        feat_df[f"lag{lag}_rel_v"] = np.clip(lv, -1.0, 1.0)
        feat_df[f"lag{lag}_rel_t"] = np.clip(lt, -1.0, 1.0)

    for lead in (14, 21):
        valid = d_idx < (n_days - lead)
        lv = np.full(len(df), np.nan, dtype=np.float64)
        lt = np.full(len(df), np.nan, dtype=np.float64)
        lv[valid] = clean_v_grid[s_idx[valid], d_idx[valid] + lead] - loo_v[valid]
        lt[valid] = clean_t_grid[s_idx[valid], d_idx[valid] + lead] - loo_t[valid]
        feat_df[f"lead{lead}_rel_v"] = np.clip(lv, -1.0, 1.0)
        feat_df[f"lead{lead}_rel_t"] = np.clip(lt, -1.0, 1.0)

    feat_df["peer_shift_v"] = df["peer_shift_v"].to_numpy(dtype=np.float64)
    feat_df["peer_shift_t"] = df["peer_shift_t"].to_numpy(dtype=np.float64)
    feat_df["loo_log_v"] = loo_v
    feat_df["loo_log_t"] = loo_t
    feat_df["dia_semana"] = df["dia_semana"].to_numpy(dtype=np.int16)
    feat_df["month"] = df["fecha"].dt.month.to_numpy(dtype=np.int16)
    feat_df["is_holiday_int"] = df["in_holiday_excl"].to_numpy(dtype=np.int8)
    feat_df["vispera_int"] = df["vispera_festivo"].to_numpy(dtype=np.int8)
    feat_df["post_festivo_int"] = df["post_festivo"].to_numpy(dtype=np.int8)
    feat_df["puente_int"] = df["puente"].to_numpy(dtype=np.int8)
    feat_df["navidad_int"] = df["navidad"].to_numpy(dtype=np.int8)
    feat_df["semana_santa_int"] = df["semana_santa"].to_numpy(dtype=np.int8)
    feat_df["periodo_especial_int"] = df["periodo_especial"].to_numpy(dtype=np.int8)
    feat_df["is_systemic_int"] = df["is_systemic_day"].to_numpy(dtype=np.int8)

    cat_df = _encode_categories(df)
    X_mat = pd.concat([feat_df, cat_df], axis=1).to_numpy(dtype=np.float32)

    # 4. 6-Fold Purged Temporal OOF LightGBM Quantile Regression (P05, P50, P95)
    fold_edges = np.linspace(0, n_days, n_folds + 1, dtype=int)
    train_eligible = ~(df["in_lifecycle_excl"].to_numpy() | df["in_reforma_excl"].to_numpy() | is_blackout)

    oof_v = {q: np.zeros(len(df), dtype=np.float64) for q in (0.05, 0.50, 0.95)}
    oof_t = {q: np.zeros(len(df), dtype=np.float64) for q in (0.05, 0.50, 0.95)}

    y_v = np.clip(raw_res_v, -1.8, 1.8)
    y_t = np.clip(raw_res_t, -1.8, 1.8)

    lgb_params = dict(
        objective="quantile",
        n_estimators=85,
        learning_rate=0.08,
        num_leaves=31,
        max_depth=6,
        max_bin=127,
        min_child_samples=80,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )
    max_train_rows = 200_000

    for f_idx in range(n_folds):
        d0, d1 = int(fold_edges[f_idx]), int(fold_edges[f_idx + 1])
        val_mask = (d_idx >= d0) & (d_idx < d1)
        tr_mask = train_eligible & ((d_idx < d0 - purge_days) | (d_idx >= d1 + purge_days))

        tr_idx = np.flatnonzero(tr_mask)
        if len(tr_idx) > max_train_rows:
            rng = np.random.default_rng(42 + f_idx)
            tr_idx = np.sort(rng.choice(tr_idx, size=max_train_rows, replace=False))

        X_tr = X_mat[tr_idx]
        X_va = X_mat[val_mask]
        yv_tr = y_v[tr_idx]
        yt_tr = y_t[tr_idx]

        for q in (0.05, 0.50, 0.95):
            m_v = lgb.LGBMRegressor(alpha=q, **lgb_params)
            m_v.fit(X_tr, yv_tr)
            oof_v[q][val_mask] = m_v.predict(X_va)

            m_t = lgb.LGBMRegressor(alpha=q, **lgb_params)
            m_t.fit(X_tr, yt_tr)
            oof_t[q][val_mask] = m_t.predict(X_va)

    # Blend OOF LightGBM P50 + Peer-adjusted LOO for maximum robustness
    lgbm_pred_log_v = loo_v + oof_v[0.50]
    peer_pred_log_v = loo_v + df["peer_shift_v"].to_numpy(dtype=np.float64)
    exp_log_v = 0.65 * lgbm_pred_log_v + 0.35 * peer_pred_log_v

    lgbm_pred_log_t = loo_t + oof_t[0.50]
    peer_pred_log_t = loo_t + df["peer_shift_t"].to_numpy(dtype=np.float64)
    exp_log_t = 0.65 * lgbm_pred_log_t + 0.35 * peer_pred_log_t

    # Enforce quantile crossing guard around blended center
    shift_v = exp_log_v - lgbm_pred_log_v
    raw_q05_v = np.minimum(loo_v + oof_v[0.05] + shift_v, exp_log_v - 0.02)
    raw_q95_v = np.maximum(loo_v + oof_v[0.95] + shift_v, exp_log_v + 0.02)

    shift_t = exp_log_t - lgbm_pred_log_t
    raw_q05_t = np.minimum(loo_t + oof_t[0.05] + shift_t, exp_log_t - 0.02)
    raw_q95_t = np.maximum(loo_t + oof_t[0.95] + shift_t, exp_log_t + 0.02)

    # Final per-series idiosyncratic sigma on clean OOF residuals
    oof_res_v_grid = np.full((n_series, n_days), np.nan, dtype=np.float64)
    oof_res_t_grid = np.full((n_series, n_days), np.nan, dtype=np.float64)
    oof_res_tm_grid = np.full((n_series, n_days), np.nan, dtype=np.float64)
    oof_res_v_grid[s_idx, d_idx] = log_v_obs - exp_log_v
    oof_res_t_grid[s_idx, d_idx] = log_t_obs - exp_log_t
    exp_log_tm = np.log(np.maximum(np.expm1(exp_log_v), 1.0) / np.maximum(np.expm1(exp_log_t), 1.0))
    oof_res_tm_grid[s_idx, d_idx] = log_tm_obs - exp_log_tm

    sigma_v = _series_mad_scale(oof_res_v_grid, clean_grid, min_sigma=0.045)
    sigma_t = _series_mad_scale(oof_res_t_grid, clean_grid, min_sigma=0.045)
    sigma_tm = _series_mad_scale(oof_res_tm_grid, clean_grid, min_sigma=0.035)

    sig_v_row = sigma_v[s_idx]
    sig_t_row = sigma_t[s_idx]
    sig_tm_row = sigma_tm[s_idx]

    # 5. Conformalized Quantile Regression (CQR) calibration per channel (90% coverage)
    cqr_low_v = np.zeros(len(df), dtype=np.float64)
    cqr_high_v = np.zeros(len(df), dtype=np.float64)
    cqr_low_t = np.zeros(len(df), dtype=np.float64)
    cqr_high_t = np.zeros(len(df), dtype=np.float64)

    canal_arr = df["canal"].to_numpy()
    alpha_cqr = 0.10
    for ch in ("sala", "domicilio", "autoking"):
        ch_mask = canal_arr == ch
        cal_mask = ch_mask & is_clean_obs
        n_cal = int(cal_mask.sum())
        q_level = min(0.999, (1.0 - alpha_cqr) * (1.0 + 1.0 / max(n_cal, 1)))

        ev = np.maximum(raw_q05_v[cal_mask] - log_v_obs[cal_mask], log_v_obs[cal_mask] - raw_q95_v[cal_mask]) / sig_v_row[cal_mask]
        et = np.maximum(raw_q05_t[cal_mask] - log_t_obs[cal_mask], log_t_obs[cal_mask] - raw_q95_t[cal_mask]) / sig_t_row[cal_mask]
        qv = float(np.quantile(ev, q_level)) if n_cal > 10 else 0.0
        qt = float(np.quantile(et, q_level)) if n_cal > 10 else 0.0

        cqr_low_v[ch_mask] = raw_q05_v[ch_mask] - qv * sig_v_row[ch_mask]
        cqr_high_v[ch_mask] = raw_q95_v[ch_mask] + qv * sig_v_row[ch_mask]
        cqr_low_t[ch_mask] = raw_q05_t[ch_mask] - qt * sig_t_row[ch_mask]
        cqr_high_t[ch_mask] = raw_q95_t[ch_mask] + qt * sig_t_row[ch_mask]

    df["ventas_esperadas"] = np.round(np.maximum(np.expm1(exp_log_v), 1.0), 2)
    df["ventas_p05"] = np.round(np.maximum(np.expm1(cqr_low_v), 0.5), 2)
    df["ventas_p95"] = np.round(np.maximum(np.expm1(cqr_high_v), 1.0), 2)
    df["tickets_esperados"] = np.round(np.maximum(np.expm1(exp_log_t), 1.0), 2)
    df["tickets_p05"] = np.round(np.maximum(np.expm1(cqr_low_t), 0.5), 2)
    df["tickets_p95"] = np.round(np.maximum(np.expm1(cqr_high_t), 1.0), 2)
    df["ticket_medio_esperado"] = np.round(df["ventas_esperadas"] / np.maximum(df["tickets_esperados"], 1.0), 2)

    df["sigma_v"] = sig_v_row
    df["sigma_t"] = sig_t_row
    df["sigma_tm"] = sig_tm_row

    # Store naive trailing 4-week same-DOW expectation for RBE baseline comparison
    trail_offsets = (-28, -21, -14, -7)
    trail_v_stack = _shifted_grid_stack(log_v_grid, trail_offsets)
    trail_v_med = _nanmedian_axis0(trail_v_stack)[s_idx, d_idx]
    trail_v_med = np.where(np.isnan(trail_v_med), loo_v, trail_v_med)
    df["naive_samedow_ventas_exp"] = np.maximum(np.expm1(trail_v_med), 1.0)

    df = compute_diagnostic_signals(df, n_series=n_series, n_days=n_days)
    return df
