"""Anomaly detection / 3-class classification models (normal / caida / subida)."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from databricks.src.backtest import jury_metrics

MODEL_FEATURES = [
    "z_ventas",
    "abs_z_ventas",
    "z_tickets",
    "abs_z_tickets",
    "z_ticket_medio",
    "abs_z_tm",
    "z_max",
    "z_peer_diff",
    "abs_z_peer_diff",
    "z_peer_diff_t",
    "abs_z_peer_diff_t",
    "z_cross_channel",
    "abs_z_cross_channel",
    "otros_canales_int",
    "delta_channel_share",
    "abs_delta_share",
    "cqr_exceed_v",
    "cqr_exceed_t",
    "z_neighbor_same_sign",
    "desviacion_ventas_pct",
    "desviacion_tickets_pct",
    "canal_code",
    "log_exp_ventas",
    "log_exp_tickets",
]

CANAL_MAP = {"sala": 0, "domicilio": 1, "autoking": 2}


def extract_model_matrix(df: pd.DataFrame) -> np.ndarray:
    """Build deterministic scale-invariant feature matrix for classifiers."""
    zv = df["z_ventas"].to_numpy(dtype=np.float64)
    zt = df["z_tickets"].to_numpy(dtype=np.float64)
    ztm = df["z_ticket_medio"].to_numpy(dtype=np.float64)
    zpd = df["z_peer_diff"].to_numpy(dtype=np.float64)
    zpdt = df["z_peer_diff_t"].to_numpy(dtype=np.float64)
    zxc = df["z_cross_channel"].to_numpy(dtype=np.float64)
    dsh = df["delta_channel_share"].to_numpy(dtype=np.float64)

    cols = [
        zv,
        np.abs(zv),
        zt,
        np.abs(zt),
        ztm,
        np.abs(ztm),
        df["z_max"].to_numpy(dtype=np.float64),
        zpd,
        np.abs(zpd),
        zpdt,
        np.abs(zpdt),
        zxc,
        np.abs(zxc),
        df["otros_canales_normales"].to_numpy(dtype=np.float64),
        dsh,
        np.abs(dsh),
        df["cqr_exceed_v"].to_numpy(dtype=np.float64),
        df["cqr_exceed_t"].to_numpy(dtype=np.float64),
        df["z_neighbor_same_sign"].to_numpy(dtype=np.float64),
        df["desviacion_ventas_pct"].to_numpy(dtype=np.float64),
        df["desviacion_tickets_pct"].to_numpy(dtype=np.float64),
        df["canal"].map(CANAL_MAP).fillna(0).to_numpy(dtype=np.float64),
        np.log1p(np.maximum(df["ventas_esperadas"].to_numpy(dtype=np.float64), 1.0)),
        np.log1p(np.maximum(df["tickets_esperados"].to_numpy(dtype=np.float64), 1.0)),
    ]
    mat = np.column_stack(cols).astype(np.float32)
    return np.nan_to_num(mat, nan=0.0, posinf=10.0, neginf=-10.0)


def resolve_official_tipo(df: pd.DataFrame) -> np.ndarray:
    """Resolve 'caida'/'subida' using the metric with largest absolute deviation."""
    zv = df["z_ventas"].to_numpy(dtype=np.float64)
    zt = df["z_tickets"].to_numpy(dtype=np.float64)
    dom = np.where(np.abs(zv) >= np.abs(zt), zv, zt)
    return np.where(dom < 0, "caida", "subida")


def _format_alert_predictions(
    df: pd.DataFrame,
    mask: np.ndarray,
    scores: np.ndarray,
    tipos: np.ndarray,
) -> pd.DataFrame:
    """Return minimal standardized prediction DataFrame sorted by descending score."""
    if not np.any(mask):
        return pd.DataFrame(columns=["restaurante_id", "canal", "fecha", "tipo", "score"])
    out = pd.DataFrame({
        "restaurante_id": df.loc[mask, "restaurante_id"].to_numpy(),
        "canal": df.loc[mask, "canal"].to_numpy(),
        "fecha": df.loc[mask, "fecha"].to_numpy(),
        "tipo": tipos[mask],
        "score": np.round(scores[mask].astype(np.float64), 6),
    })
    return out.sort_values("score", ascending=False, kind="mergesort").reset_index(drop=True)


def tune_threshold_for_f1(
    scores: np.ndarray,
    tipos: np.ndarray,
    y_class: np.ndarray,
    eval_mask: np.ndarray,
    elig_mask: np.ndarray,
) -> Tuple[float, float]:
    """Pick the score threshold maximizing exact-match F1 via a sorted cumulative sum (O(N log N)).

    Only rows that are eligible and not wild-neutral are scored; a true positive needs the exact tipo.
    Returns (threshold, best_f1).
    """
    valid = elig_mask & eval_mask
    scores_v = scores[valid]
    if len(scores_v) == 0:
        return 0.5, 0.0
    y_v = y_class[valid]
    t_v = tipos[valid]
    n_gt = int(np.sum(y_v > 0))
    if n_gt == 0:
        return float(np.max(scores_v)), 0.0

    is_tp = ((y_v == 1) & (t_v == "caida")) | ((y_v == 2) & (t_v == "subida"))
    k_top = int(min(len(scores_v), max(n_gt * 6, 12000)))
    if k_top < len(scores_v):
        top_idx = np.argpartition(-scores_v, k_top - 1)[:k_top]
    else:
        top_idx = np.arange(len(scores_v))
    order = top_idx[np.argsort(-scores_v[top_idx], kind="mergesort")]
    scores_sorted = scores_v[order]
    tp_cum = np.cumsum(is_tp[order].astype(np.float64))
    k_arr = np.arange(1, len(tp_cum) + 1, dtype=np.float64)
    f1_arr = 2.0 * tp_cum / np.maximum(k_arr + n_gt, 1.0)
    best_idx = int(np.argmax(f1_arr))
    return float(scores_sorted[best_idx]), float(f1_arr[best_idx])


class BaseAnomalyClassifier:
    """Common interface for all anomaly classification models."""

    name: str = "base"
    threshold: float = 0.5

    def fit(
        self,
        df_train: pd.DataFrame,
        y_train: Optional[np.ndarray] = None,
        gt_train: Optional[pd.DataFrame] = None,
        eval_mask_train: Optional[np.ndarray] = None,
    ) -> "BaseAnomalyClassifier":
        raise NotImplementedError

    def predict_scores_and_tipos(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (scores, tipos, prob_like_class_probs_or_scores)."""
        raise NotImplementedError

    def predict_alerts(self, df: pd.DataFrame, threshold: Optional[float] = None) -> pd.DataFrame:
        thr = self.threshold if threshold is None else float(threshold)
        scores, tipos, elig_mask = self.predict_scores_and_tipos(df)
        return _format_alert_predictions(df, elig_mask & (scores >= thr), scores, tipos)


class SimpleSameDOWClassifier(BaseAnomalyClassifier):
    """Naive RBE reference baseline: trailing 4-week same-DOW ratio without guardrails/peers."""

    name = "0. SimpleSameDOW (Ref RBE)"

    def __init__(self, pct_threshold: float = 0.45):
        self.pct_threshold = pct_threshold
        self.threshold = pct_threshold

    def fit(self, df_train, y_train=None, gt_train=None, eval_mask_train=None):
        return self

    def predict_scores_and_tipos(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        v = df["ventas"].to_numpy(dtype=np.float64)
        exp = np.maximum(df["naive_samedow_ventas_exp"].to_numpy(dtype=np.float64), 1.0)
        rel_diff = (v - exp) / exp
        scores = np.abs(rel_diff)
        tipos = np.where(rel_diff < 0, "caida", "subida")
        elig_mask = ~df["in_warmup"].to_numpy(dtype=bool)  # Deliberately omits holiday/lifecycle/systemic guardrails
        return scores, tipos, elig_mask


class RobustZClassifier(BaseAnomalyClassifier):
    """Statistical robust z-score classifier combining series MAD, peer diff, and cross-channel."""

    name = "1. RobustZ + Peer + CrossCh"

    def __init__(self, threshold: float = 3.8):
        self.threshold = threshold

    def predict_scores_and_tipos(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        zv = df["z_ventas"].to_numpy(dtype=np.float64)
        zt = df["z_tickets"].to_numpy(dtype=np.float64)
        z_peer = np.abs(df["z_peer_diff"].to_numpy(dtype=np.float64))
        z_peer_t = np.abs(df["z_peer_diff_t"].to_numpy(dtype=np.float64))
        z_xc = np.abs(df["z_cross_channel"].to_numpy(dtype=np.float64))
        z_v_eff = 0.65 * np.abs(zv) + 0.35 * z_peer
        z_t_eff = 0.65 * np.abs(zt) + 0.35 * z_peer_t
        scores = np.maximum(z_v_eff, z_t_eff) + 0.15 * np.minimum(z_xc, 6.0)
        tipos = resolve_official_tipo(df)
        elig_mask = df["is_eligible_for_alert"].to_numpy(dtype=bool)
        return scores, tipos, elig_mask

    def fit(self, df_train, y_train=None, gt_train=None, eval_mask_train=None):
        if y_train is not None and eval_mask_train is not None:
            scores, tipos, elig = self.predict_scores_and_tipos(df_train)
            self.threshold, _ = tune_threshold_for_f1(scores, tipos, y_train, eval_mask_train, elig)
        return self


class CQRQuantileClassifier(BaseAnomalyClassifier):
    """Conformalized Quantile Regression (CQR) interval exceedance classifier."""

    name = "2. LightGBM CQR (Quantiles)"

    def __init__(self, threshold: float = 1.15):
        self.threshold = threshold

    def predict_scores_and_tipos(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        ev = df["cqr_exceed_v"].to_numpy(dtype=np.float64)
        et = df["cqr_exceed_t"].to_numpy(dtype=np.float64)
        scores = np.maximum(ev, et)
        tipos = resolve_official_tipo(df)
        elig_mask = df["is_eligible_for_alert"].to_numpy(dtype=bool)
        return scores, tipos, elig_mask

    def fit(self, df_train, y_train=None, gt_train=None, eval_mask_train=None):
        if y_train is not None and eval_mask_train is not None:
            scores, tipos, elig = self.predict_scores_and_tipos(df_train)
            self.threshold, _ = tune_threshold_for_f1(scores, tipos, y_train, eval_mask_train, elig)
        return self


class IsolationForestClassifier(BaseAnomalyClassifier):
    """Unsupervised multivariate Isolation Forest over residual and channel-mix features."""

    name = "3. IsolationForest Multiv."

    def __init__(self, threshold: float = 0.05):
        self.threshold = threshold
        self.scaler = RobustScaler()
        self.iso = IsolationForest(
            n_estimators=120,
            max_samples=4096,
            contamination="auto",
            random_state=42,
            n_jobs=-1,
        )

    def fit(self, df_train, y_train=None, gt_train=None, eval_mask_train=None):
        elig = df_train["is_eligible_for_alert"].to_numpy(dtype=bool)
        # Fit on clean baseline rows of training cohort
        fit_mask = elig & (df_train["z_max"].to_numpy(dtype=np.float64) < 3.5)
        X_all = extract_model_matrix(df_train)
        self.scaler.fit(X_all[fit_mask])

        elig_idx = np.flatnonzero(elig)
        if len(elig_idx) > 120_000:
            rng = np.random.default_rng(42)
            elig_idx = np.sort(rng.choice(elig_idx, size=120_000, replace=False))
        self.iso.fit(self.scaler.transform(X_all[elig_idx]))

        if y_train is not None and eval_mask_train is not None:
            scores, tipos, elig_m = self.predict_scores_and_tipos(df_train)
            self.threshold, _ = tune_threshold_for_f1(scores, tipos, y_train, eval_mask_train, elig_m)
        return self

    def predict_scores_and_tipos(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        X_scaled = self.scaler.transform(extract_model_matrix(df))
        # Negate decision_function so higher = more anomalous
        scores = -self.iso.decision_function(X_scaled)
        tipos = resolve_official_tipo(df)
        elig_mask = df["is_eligible_for_alert"].to_numpy(dtype=bool)
        return scores, tipos, elig_mask


class HybridMetaClassifier(BaseAnomalyClassifier):
    """3-class LightGBM meta-classifier trained on synthetic anomalies over OOF + CQR + peer signals."""

    name = "4. HybridMetaClassifier (LGBM)"

    def __init__(self, threshold: float = 0.55):
        self.threshold = threshold
        self.model = lgb.LGBMClassifier(
            objective="multiclass",
            num_class=3,
            n_estimators=220,
            learning_rate=0.05,
            num_leaves=31,
            max_depth=6,
            min_child_samples=40,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
            verbose=-1,
        )
        self.last_probs: Optional[np.ndarray] = None

    def fit(self, df_train, y_train=None, gt_train=None, eval_mask_train=None):
        if y_train is None or eval_mask_train is None:
            raise ValueError("HybridMetaClassifier requires y_train and eval_mask_train.")

        elig = df_train["is_eligible_for_alert"].to_numpy(dtype=bool)
        fit_mask = elig & eval_mask_train

        if y_train is not None:
            # Out-of-sample restaurant split within df_train so threshold calibration avoids in-sample bias
            tr_rests = np.sort(df_train["restaurante_id"].unique())
            cal_cut = max(1, int(len(tr_rests) * 0.25))
            cal_set = set(tr_rests[:cal_cut])
            is_cal_rest = df_train["restaurante_id"].isin(cal_set).to_numpy(dtype=bool)

            m_tr_sub = fit_mask & (~is_cal_rest)
            m_cal_sub = is_cal_rest

            X_sub = extract_model_matrix(df_train.loc[m_tr_sub])
            y_sub = y_train[m_tr_sub]
            w_sub = np.where(y_sub > 0, 2.0, 1.0)
            self.model.fit(X_sub, y_sub, sample_weight=w_sub)

            df_cal = df_train.loc[m_cal_sub]
            scores_cal, tipos_cal, elig_cal = self.predict_scores_and_tipos(df_cal)
            self.threshold, _ = tune_threshold_for_f1(
                scores_cal, tipos_cal, y_train[m_cal_sub], eval_mask_train[m_cal_sub], elig_cal
            )

        X_tr = extract_model_matrix(df_train.loc[fit_mask])
        y_tr = y_train[fit_mask]
        weights = np.where(y_tr > 0, 2.0, 1.0)
        self.model.fit(X_tr, y_tr, sample_weight=weights)
        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(extract_model_matrix(df))

    def predict_scores_and_tipos(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return pure anomaly probability (0..1), direction label, and eligibility mask."""
        probs = self.predict_proba(df)
        self.last_probs = probs
        p_caida = probs[:, 1]
        p_subida = probs[:, 2]
        p_anom = p_caida + p_subida

        # Direction: official FAQ rule on larger |z| when z_max >= 2.0, otherwise class probability
        z_max = df["z_max"].to_numpy(dtype=np.float64)
        official_tipo = resolve_official_tipo(df)
        prob_tipo = np.where(p_caida >= p_subida, "caida", "subida")
        tipos = np.where(z_max >= 2.0, official_tipo, prob_tipo)

        elig_mask = df["is_eligible_for_alert"].to_numpy(dtype=bool)
        return p_anom, tipos, elig_mask

    def predict_alerts(self, df: pd.DataFrame, threshold: Optional[float] = None) -> pd.DataFrame:
        """Filter purely on p_anom >= threshold, then tie-break high-confidence alerts cleanly."""
        thr = self.threshold if threshold is None else float(threshold)
        p_anom, tipos, elig_mask = self.predict_scores_and_tipos(df)
        mask = elig_mask & (p_anom >= thr)
        # Monotone post-filter tie-breaker preserving exact filtered alert set
        z_max = df["z_max"].to_numpy(dtype=np.float64)
        abs_imp = np.abs(df["impacto_estimado_eur"].to_numpy(dtype=np.float64))
        tie_bonus = 0.005 * np.clip(z_max / 12.0, 0.0, 1.0) + 0.005 * np.clip(
            np.log1p(abs_imp) / np.log1p(5000.0), 0.0, 1.0
        )
        ranked_scores = p_anom + tie_bonus
        return _format_alert_predictions(df, mask, ranked_scores, tipos)

    def feature_importances(self) -> pd.DataFrame:
        return pd.DataFrame({
            "feature": MODEL_FEATURES,
            "importance_gain": self.model.booster_.feature_importance(importance_type="gain"),
        }).sort_values("importance_gain", ascending=False).reset_index(drop=True)


class EnsembleCQRClassifier(BaseAnomalyClassifier):
    """Contextual CQR ensemble combining conformal exceedance with peer, cross-channel, and streak signals."""

    name = "5. Ensemble CQR + Peer + CrossCh"

    def __init__(
        self,
        threshold: float = 3.0,
        alpha_peer: float = 0.20,
        alpha_xc: float = 0.15,
        alpha_nbr: float = 0.12,
    ):
        self.threshold = threshold
        self.alpha_peer = alpha_peer
        self.alpha_xc = alpha_xc
        self.alpha_nbr = alpha_nbr

    def _score_with_weights(
        self, df: pd.DataFrame, alpha_peer: float, alpha_xc: float, alpha_nbr: float
    ) -> np.ndarray:
        cqr_base = np.maximum(
            df["cqr_exceed_v"].to_numpy(dtype=np.float64),
            df["cqr_exceed_t"].to_numpy(dtype=np.float64),
        )
        peer_sig = np.maximum(
            np.abs(df["z_peer_diff"].to_numpy(dtype=np.float64)),
            np.abs(df["z_peer_diff_t"].to_numpy(dtype=np.float64)),
        )
        xc_raw = np.abs(df["z_cross_channel"].to_numpy(dtype=np.float64))
        otros_ok = df["otros_canales_normales"].to_numpy(dtype=bool)
        xc_sig = np.where(otros_ok, np.minimum(xc_raw, 6.0), 0.0)
        nbr_sig = np.minimum(np.maximum(df["z_neighbor_same_sign"].to_numpy(dtype=np.float64), 0.0), 6.0)
        return (
            cqr_base
            + alpha_peer * np.minimum(peer_sig, 8.0)
            + alpha_xc * xc_sig
            + alpha_nbr * nbr_sig
        )

    def fit(self, df_train, y_train=None, gt_train=None, eval_mask_train=None):
        if y_train is None or eval_mask_train is None:
            return self

        elig = df_train["is_eligible_for_alert"].to_numpy(dtype=bool)
        tipos = resolve_official_tipo(df_train)
        cqr_base = np.maximum(
            df_train["cqr_exceed_v"].to_numpy(dtype=np.float64),
            df_train["cqr_exceed_t"].to_numpy(dtype=np.float64),
        )
        peer_sig = np.minimum(
            np.maximum(
                np.abs(df_train["z_peer_diff"].to_numpy(dtype=np.float64)),
                np.abs(df_train["z_peer_diff_t"].to_numpy(dtype=np.float64)),
            ),
            8.0,
        )
        otros_ok = df_train["otros_canales_normales"].to_numpy(dtype=bool)
        xc_sig = np.where(otros_ok, np.minimum(np.abs(df_train["z_cross_channel"].to_numpy(dtype=np.float64)), 6.0), 0.0)
        nbr_sig = np.minimum(np.maximum(df_train["z_neighbor_same_sign"].to_numpy(dtype=np.float64), 0.0), 6.0)

        # Hold out 25% of training restaurants to avoid in-sample overfitting of contextual weights
        tr_rests = np.sort(df_train["restaurante_id"].unique())
        cal_cut = max(1, int(len(tr_rests) * 0.25))
        is_val = df_train["restaurante_id"].isin(set(tr_rests[:cal_cut])).to_numpy(dtype=bool)
        elig_sub = elig[~is_val]
        elig_val = elig[is_val]
        y_sub = y_train[~is_val]
        y_val = y_train[is_val]
        t_sub = tipos[~is_val]
        t_val = tipos[is_val]
        m_sub = eval_mask_train[~is_val]
        m_val = eval_mask_train[is_val]

        best_val_f1 = -1.0
        best_params = (0.0, 0.0, 0.0, 2.85)
        grid = [
            (0.00, 0.00, 0.00),
            (0.15, 0.10, 0.08),
            (0.25, 0.15, 0.10),
            (0.35, 0.15, 0.12),
            (0.25, 0.22, 0.15),
        ]
        for ap, ax, an in grid:
            sc_all = cqr_base + ap * peer_sig + ax * xc_sig + an * nbr_sig
            thr, _ = tune_threshold_for_f1(sc_all[~is_val], t_sub, y_sub, m_sub, elig_sub)
            valid_v = elig_val & m_val & (sc_all[is_val] >= thr)
            tp = int((valid_v & (((y_val == 1) & (t_val == "caida")) | ((y_val == 2) & (t_val == "subida")))).sum())
            k = int(valid_v.sum())
            n_gt = int((y_val[m_val] > 0).sum())
            f1_v = (2.0 * tp / (k + n_gt)) if (k + n_gt) > 0 else 0.0
            if f1_v > best_val_f1:
                best_val_f1 = f1_v
                best_params = (ap, ax, an, thr)

        self.alpha_peer, self.alpha_xc, self.alpha_nbr, _ = best_params
        scores_full = self._score_with_weights(df_train, self.alpha_peer, self.alpha_xc, self.alpha_nbr)
        self.threshold, _ = tune_threshold_for_f1(scores_full, tipos, y_train, eval_mask_train, elig)
        return self

    def predict_scores_and_tipos(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        scores = self._score_with_weights(df, self.alpha_peer, self.alpha_xc, self.alpha_nbr)
        tipos = resolve_official_tipo(df)
        elig_mask = df["is_eligible_for_alert"].to_numpy(dtype=bool)
        return scores, tipos, elig_mask
