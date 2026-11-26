"""Synthetic anomaly injection benchmark and exact jury evaluation metrics."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from databricks.src.features import compute_diagnostic_signals


def split_train_eval_restaurants(
    df: pd.DataFrame, train_frac: float = 0.60, seed: int = 42
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split restaurants deterministically into disjoint train and evaluation cohorts."""
    rests = np.sort(df["restaurante_id"].unique())
    rng = np.random.default_rng(seed)
    perm = rng.permutation(rests)
    cut = int(round(len(perm) * train_frac))
    train_set = set(perm[:cut])
    is_tr = df["restaurante_id"].isin(train_set)
    return df.loc[is_tr].copy(), df.loc[~is_tr].copy()


def _dominant_z_sign(z_v: np.ndarray, z_t: np.ndarray) -> np.ndarray:
    """Return 'caida' or 'subida' per official FAQ rule when sales/tickets diverge."""
    dom = np.where(np.abs(z_v) >= np.abs(z_t), z_v, z_t)
    return np.where(dom < 0, "caida", "subida")


def create_synthetic_benchmark(
    df_cohort: pd.DataFrame,
    n_events: int = 650,
    seed: int = 2026,
    clean_inject_cutoff: float = 2.5,
    wild_z_cutoff: float = 4.3,
) -> Dict[str, object]:
    """Inject realistic 1-day, multi-day streak, and pure-ticket anomalies into a cohort.

    Injection targets are calm eligible days (orig z_max < clean_inject_cutoff). Pre-existing extreme
    unlabelled deviations in the wild (orig z_max >= wild_z_cutoff) are masked from FP/FN accounting via
    `eval_mask`; normal noisy days in [clean_inject_cutoff, wild_z_cutoff) stay as negatives so the
    classifier learns a realistic heavy-tailed normal class.
    """
    rng = np.random.default_rng(seed)
    work = df_cohort.copy().reset_index(drop=True)

    orig_z_max = work["z_max"].to_numpy(dtype=np.float64)
    elig = work["is_eligible_for_alert"].to_numpy(dtype=bool)
    clean_pool = elig & (orig_z_max < clean_inject_cutoff)

    # Map (series_id, day_idx) -> row position for fast streak lookup
    s_arr = work["series_id"].to_numpy(dtype=np.int32)
    d_arr = work["day_idx"].to_numpy(dtype=np.int32)
    pos_map = {(int(s), int(d)): i for i, (s, d) in enumerate(zip(s_arr, d_arr))}

    candidate_indices = np.flatnonzero(clean_pool)
    rng.shuffle(candidate_indices)

    injected_mask = np.zeros(len(work), dtype=bool)
    gt_rows: List[dict] = []
    used_rows = set()

    # Sample archetype counts: 35% 1d drop, 25% 1d spike, 25% 2-4d streak, 15% pure-ticket
    archetypes = rng.choice(
        ["drop_1d", "spike_1d", "streak_2_4d", "pure_ticket"],
        size=n_events * 2,
        p=[0.35, 0.25, 0.25, 0.15],
    )

    ventas_new = work["ventas"].to_numpy(dtype=np.float64).copy()
    tickets_new = work["tickets"].to_numpy(dtype=np.int64).copy()
    exp_v = work["ventas_esperadas"].to_numpy(dtype=np.float64)
    exp_t = work["tickets_esperados"].to_numpy(dtype=np.float64)
    sig_v = work["sigma_v"].to_numpy(dtype=np.float64)
    sig_t = work["sigma_t"].to_numpy(dtype=np.float64)

    events_created = 0
    ptr = 0
    arch_ptr = 0
    while events_created < n_events and ptr < len(candidate_indices) and arch_ptr < len(archetypes):
        idx0 = int(candidate_indices[ptr])
        ptr += 1
        if idx0 in used_rows:
            continue

        arch = str(archetypes[arch_ptr])
        arch_ptr += 1

        s0 = int(s_arr[idx0])
        d0 = int(d_arr[idx0])

        # Prevent injecting adjacent to another injected event
        if any(pos_map.get((s0, d0 + off)) in used_rows for off in (-2, -1, 1, 2)):
            continue

        if arch == "streak_2_4d":
            dur = int(rng.integers(2, 5))
            streak_idxs = []
            ok = True
            for off in range(dur):
                p = pos_map.get((s0, d0 + off))
                if p is None or (not clean_pool[p]) or (p in used_rows):
                    ok = False
                    break
                streak_idxs.append(p)
            if not ok:
                continue
            is_drop = bool(rng.random() < 0.60)
            base_mult = float(rng.uniform(0.15, 0.52) if is_drop else rng.uniform(1.70, 2.60))
            tipo = "caida" if is_drop else "subida"
            for p in streak_idxs:
                mv = float(np.clip(base_mult * rng.uniform(0.90, 1.10), 0.08, 4.0))
                mt = float(np.clip(mv * rng.uniform(0.88, 1.12), 0.08, 4.0))
                ventas_new[p] = round(max(1.5, exp_v[p] * mv), 2)
                tickets_new[p] = max(1, int(round(exp_t[p] * mt)))
                used_rows.add(p)
                injected_mask[p] = True
                gt_rows.append({
                    "row_idx": p,
                    "restaurante_id": work.at[p, "restaurante_id"],
                    "canal": work.at[p, "canal"],
                    "fecha": work.at[p, "fecha"],
                    "tipo": tipo,
                    "archetype": arch,
                    "impact_abs_eur": abs(ventas_new[p] - exp_v[p]),
                })
            events_created += 1
            continue

        if arch == "drop_1d":
            mv = float(rng.uniform(0.12, 0.52))
            mt = float(np.clip(mv * rng.uniform(0.82, 1.18), 0.05, 0.72))
            tipo = "caida"
        elif arch == "spike_1d":
            mv = float(rng.uniform(1.75, 2.95))
            mt = float(np.clip(mv * rng.uniform(0.82, 1.18), 1.45, 3.50))
            tipo = "subida"
        else:
            # Pure ticket shock with sales staying close to normal
            is_drop = bool(rng.random() < 0.45)
            mv = float(rng.uniform(0.90, 1.10))
            if is_drop:
                mt = float(rng.uniform(0.22, 0.45))
                tipo = "caida"
            else:
                mt = float(rng.uniform(2.15, 3.50))
                tipo = "subida"
            # Verify |z_t| dominates |z_v| cleanly
            zt_est = abs(np.log1p(max(1.0, exp_t[idx0] * mt)) - np.log1p(exp_t[idx0])) / max(sig_t[idx0], 0.045)
            zv_est = abs(np.log1p(max(1.0, exp_v[idx0] * mv)) - np.log1p(exp_v[idx0])) / max(sig_v[idx0], 0.045)
            if zt_est <= zv_est + 1.2:
                continue

        ventas_new[idx0] = round(max(1.5, exp_v[idx0] * mv), 2)
        tickets_new[idx0] = max(1, int(round(exp_t[idx0] * mt)))
        used_rows.add(idx0)
        injected_mask[idx0] = True
        gt_rows.append({
            "row_idx": idx0,
            "restaurante_id": work.at[idx0, "restaurante_id"],
            "canal": work.at[idx0, "canal"],
            "fecha": work.at[idx0, "fecha"],
            "tipo": tipo,
            "archetype": arch,
            "impact_abs_eur": abs(ventas_new[idx0] - exp_v[idx0]),
        })
        events_created += 1

    work["ventas"] = ventas_new
    work["tickets"] = tickets_new
    work["ticket_medio"] = np.round(work["ventas"] / np.maximum(work["tickets"], 1), 2)

    work = compute_diagnostic_signals(work)
    work["is_injected"] = injected_mask

    gt_df = pd.DataFrame(gt_rows)
    if not gt_df.empty:
        gt_df["fecha_str"] = pd.to_datetime(gt_df["fecha"]).dt.strftime("%Y-%m-%d")

    # Rows that were already unlabelled extreme anomalies in the wild are neutral in benchmark evaluation
    eval_mask = ~((~injected_mask) & elig & (orig_z_max >= wild_z_cutoff))
    y_class = np.zeros(len(work), dtype=np.int32)
    if not gt_df.empty:
        y_class[gt_df.loc[gt_df["tipo"] == "caida", "row_idx"].to_numpy()] = 1
        y_class[gt_df.loc[gt_df["tipo"] == "subida", "row_idx"].to_numpy()] = 2

    return {
        "df": work,
        "gt_df": gt_df,
        "y_class": y_class,
        "eval_mask": eval_mask,
        "wild_neutral_count": int((~eval_mask).sum()),
    }


def build_neutral_keys(eval_mask: np.ndarray, df_ref: pd.DataFrame) -> set:
    """Precompute (restaurante_id, canal, 'YYYY-MM-DD') keys for wild-neutral rows."""
    neutral = df_ref.loc[~eval_mask, ["restaurante_id", "canal", "fecha"]]
    if neutral.empty:
        return set()
    f_str = pd.to_datetime(neutral["fecha"]).dt.strftime("%Y-%m-%d")
    return set(zip(neutral["restaurante_id"], neutral["canal"], f_str))


def jury_metrics(
    gt_df: pd.DataFrame,
    pred_df: pd.DataFrame,
    eval_mask: Optional[np.ndarray] = None,
    df_ref: Optional[pd.DataFrame] = None,
    neutral_keys: Optional[set] = None,
) -> Dict[str, float]:
    """Compute exact official jury metrics: F1 (with tipo), precision@100, recall_impact, F1_det."""
    empty = {
        "n_gt": int(len(gt_df)),
        "n_pred": 0,
        "tp_exact": 0,
        "tp_det": 0,
        "fp": 0,
        "fn": int(len(gt_df)),
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "f1_detection": 0.0,
        "precision_at_100": 0.0,
        "recall_impact_eur": 0.0,
    }
    if pred_df.empty:
        return empty

    p = pred_df.copy()
    p["fecha_str"] = pd.to_datetime(p["fecha"]).dt.strftime("%Y-%m-%d")

    # Exclude wild-neutral predictions (neither TP nor FP)
    if neutral_keys is None and eval_mask is not None and df_ref is not None:
        neutral_keys = build_neutral_keys(eval_mask, df_ref)
    if neutral_keys:
        keep = [
            (r, c, f) not in neutral_keys
            for r, c, f in zip(p["restaurante_id"], p["canal"], p["fecha_str"])
        ]
        p = p.loc[keep].reset_index(drop=True)

    if p.empty:
        return empty

    gt_exact = {
        (r, c, f): (t, float(imp))
        for r, c, f, t, imp in zip(
            gt_df["restaurante_id"], gt_df["canal"], gt_df["fecha_str"], gt_df["tipo"], gt_df["impact_abs_eur"]
        )
    }

    p = p.sort_values("score", ascending=False, kind="mergesort").reset_index(drop=True)
    exact_hits = []
    det_hits = []
    matched_impact = 0.0
    for r, c, f, t in zip(p["restaurante_id"], p["canal"], p["fecha_str"], p["tipo"]):
        match = gt_exact.get((r, c, f))
        if match is not None:
            det_hits.append(1)
            if match[0] == t:
                exact_hits.append(1)
                matched_impact += match[1]
            else:
                exact_hits.append(0)
        else:
            det_hits.append(0)
            exact_hits.append(0)

    tp_exact = int(sum(exact_hits))
    tp_det = int(sum(det_hits))
    n_pred = int(len(p))
    n_gt = int(len(gt_df))
    prec = tp_exact / n_pred if n_pred > 0 else 0.0
    rec = tp_exact / n_gt if n_gt > 0 else 0.0
    f1 = (2.0 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

    prec_det = tp_det / n_pred if n_pred > 0 else 0.0
    rec_det = tp_det / n_gt if n_gt > 0 else 0.0
    f1_det = (2.0 * prec_det * rec_det / (prec_det + rec_det)) if (prec_det + rec_det) > 0 else 0.0

    p100 = float(sum(exact_hits[:100])) / 100.0
    tot_impact = float(gt_df["impact_abs_eur"].sum())
    rec_imp = matched_impact / tot_impact if tot_impact > 0 else 0.0

    return {
        "n_gt": n_gt,
        "n_pred": n_pred,
        "tp_exact": tp_exact,
        "tp_det": tp_det,
        "fp": n_pred - tp_exact,
        "fn": n_gt - tp_exact,
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "f1_detection": round(f1_det, 4),
        "precision_at_100": round(p100, 4),
        "recall_impact_eur": round(rec_imp, 4),
    }


def archetype_recall_breakdown(gt_df: pd.DataFrame, pred_df: pd.DataFrame) -> pd.DataFrame:
    """Compute exact recall per injected anomaly archetype."""
    if pred_df.empty:
        out = gt_df.groupby("archetype").size().reset_index(name="total")
        out["detected"] = 0
        out["recall"] = 0.0
        return out
    p = pred_df.copy()
    p["fecha_str"] = pd.to_datetime(p["fecha"]).dt.strftime("%Y-%m-%d")
    pred_set = set(zip(p["restaurante_id"], p["canal"], p["fecha_str"], p["tipo"]))
    g = gt_df.copy()
    g["hit"] = [
        (r, c, f, t) in pred_set
        for r, c, f, t in zip(g["restaurante_id"], g["canal"], g["fecha_str"], g["tipo"])
    ]
    res = g.groupby("archetype", sort=True).agg(
        total=("hit", "count"),
        detected=("hit", "sum"),
    ).reset_index()
    res["recall"] = (res["detected"] / res["total"]).round(4)
    return res
