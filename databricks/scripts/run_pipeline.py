"""Reproducible end-to-end CLI runner for the RBE Sales Anomaly Detection pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from databricks.src.backtest import (  # noqa: E402
    build_neutral_keys,
    create_synthetic_benchmark,
    jury_metrics,
    split_train_eval_restaurants,
)
from databricks.src.classifiers import (  # noqa: E402
    CQRQuantileClassifier,
    EnsembleCQRClassifier,
    HybridMetaClassifier,
    RobustZClassifier,
)
from databricks.src.config import get_data_dir, get_outputs_dir  # noqa: E402
from databricks.src.features import build_features_and_baselines  # noqa: E402
from databricks.src.guardrails import apply_guardrails  # noqa: E402
from databricks.src.scoring import build_gold_outputs, export_anomalias_csv  # noqa: E402
from databricks.src.silver import load_raw_datasets, prepare_silver_data  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RBE anomaly pipeline and export validated Gold outputs.")
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Reuse outputs/silver_features.parquet and outputs/restaurants_dim.parquet if present.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional output directory (useful for non-destructive smoke tests).",
    )
    args = parser.parse_args()

    cache_dir = get_outputs_dir()
    out_dir = Path(args.out_dir) if args.out_dir is not None else cache_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.perf_counter()

    feat_cache = cache_dir / "silver_features.parquet"
    dim_cache = cache_dir / "restaurants_dim.parquet"
    if args.from_cache and feat_cache.is_file() and dim_cache.is_file():
        print(f"Loading cached Silver/Features from {cache_dir}...")
        df_feat = pd.read_parquet(feat_cache)
        dim = pd.read_parquet(dim_cache)
    else:
        t0 = time.perf_counter()
        ventas, restaurantes, calendario = load_raw_datasets()
        silver = prepare_silver_data(ventas, restaurantes, calendario)
        print(f"[1/4] Silver prepared in {time.perf_counter() - t0:.1f}s ({len(silver['df']):,} rows)")

        t1 = time.perf_counter()
        df_feat = build_features_and_baselines(silver, n_folds=6, purge_days=14)
        df_feat = apply_guardrails(df_feat)
        dim = silver["restaurants_dim"]
        print(f"[2/4] Counterfactual LOO+OOF CQR + Guardrails in {time.perf_counter() - t1:.1f}s")

        df_feat.to_parquet(feat_cache, index=False, coerce_timestamps="us", allow_truncated_timestamps=True)
        dim.to_parquet(dim_cache, index=False)

    # Disjoint OOS restaurant split + synthetic benchmark model selection
    t2 = time.perf_counter()
    df_tr_raw, df_ev_raw = split_train_eval_restaurants(df_feat, train_frac=0.60, seed=42)
    train_bench = create_synthetic_benchmark(df_tr_raw, n_events=800, seed=101)
    eval_bench = create_synthetic_benchmark(df_ev_raw, n_events=600, seed=2026)
    neutral_keys = build_neutral_keys(eval_bench["eval_mask"], eval_bench["df"])

    candidates = [
        RobustZClassifier(),
        CQRQuantileClassifier(),
        HybridMetaClassifier(),
        EnsembleCQRClassifier(),
    ]
    best_tuple = None
    for clf in candidates:
        clf.fit(
            train_bench["df"],
            y_train=train_bench["y_class"],
            gt_train=train_bench["gt_df"],
            eval_mask_train=train_bench["eval_mask"],
        )
        p_ev = clf.predict_alerts(eval_bench["df"])
        m = jury_metrics(eval_bench["gt_df"], p_ev, neutral_keys=neutral_keys)
        rank_key = (m["f1"], m["precision_at_100"], m["recall_impact_eur"])
        print(f"  - {clf.name:32s} | thr={clf.threshold:.4f} | F1={m['f1']:.4f} | P@100={m['precision_at_100']:.4f}")
        if best_tuple is None or rank_key > best_tuple[0]:
            best_tuple = (rank_key, clf, m)

    assert best_tuple is not None
    _, champion, metrics = best_tuple
    pred_full = champion.predict_alerts(df_feat)
    print(f"[3/4] Champion={champion.name} | F1={metrics['f1']:.4f} | alerts={len(pred_full):,} ({time.perf_counter() - t2:.1f}s)")

    if "Ensemble" in champion.name:
        model_ver = "v3_ensemble_cqr_ctx"
    elif "Hybrid" in champion.name:
        model_ver = "v2_hybrid_lgbm_cqr"
    elif "CQR" in champion.name:
        model_ver = "v2_lgbm_cqr_quantiles"
    else:
        model_ver = "v2_robust_z_ctx"

    extra_params = {}
    if isinstance(champion, EnsembleCQRClassifier):
        extra_params = {
            "alpha_peer": float(champion.alpha_peer),
            "alpha_cross_channel": float(champion.alpha_xc),
            "alpha_persistencia": float(champion.alpha_nbr),
        }

    run_id = f"run_rb06_{uuid.uuid4().hex[:8]}"
    gold = build_gold_outputs(
        df_features=df_feat,
        restaurants_dim=dim,
        pred_alerts=pred_full,
        run_id=run_id,
        model_version=model_ver,
        synthetic_metrics=metrics,
        params_dict={
            "modelo": champion.name,
            "umbral_raw": round(float(champion.threshold), 4),
            "bloques_oof": 6,
            "purge_dias": 14,
            "cobertura_cqr_objetivo": 0.90,
            "f1_oos": metrics["f1"],
            "precision_at_100_oos": metrics["precision_at_100"],
            "recall_impacto_eur_oos": metrics["recall_impact_eur"],
            **extra_params,
        },
    )

    gold["model_runs"].to_parquet(out_dir / "gold_model_runs.parquet", index=False, coerce_timestamps="us", allow_truncated_timestamps=True)
    gold["restaurants_dim"].to_parquet(out_dir / "gold_restaurants_dim.parquet", index=False)
    gold["series_summary"].to_parquet(out_dir / "gold_series_summary.parquet", index=False)
    gold["alerts_prioritized"].to_parquet(out_dir / "gold_alerts_prioritized.parquet", index=False, coerce_timestamps="us", allow_truncated_timestamps=True)

    with open(out_dir / "champion_metrics.json", "w", encoding="utf-8") as f:
        json.dump(
            {"model_version": model_ver, "champion_name": champion.name, "threshold": float(champion.threshold), "extra_params": extra_params, "metrics": metrics},
            f,
            indent=2,
        )

    csv_path = export_anomalias_csv(gold["alerts_prioritized"], out_dir / "anomalias.csv")
    validator = get_data_dir() / "validar_entrega.py"
    proc = subprocess.run([sys.executable, str(validator), str(csv_path)], capture_output=True, text=True, check=False)
    print(f"[4/4] Validation ({time.perf_counter() - t_start:.1f}s total): {proc.stdout.strip()}")
    if proc.returncode != 0:
        print("STDERR:", proc.stderr)
        return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
