# Databricks notebook source
# Populate Databricks Unity Catalog (dat_hack_rb06.rbe.*) preserving existing views.
# Supports two modes via widget `mode`:
#   - "from_stage" (fast path): loads verified Gold Parquets from UC Volume `_stage/`,
#     enriches Top-50 alerts with Databricks Foundation Model endpoint, casts cleanly to
#     exact UC Delta schema, truncates & inserts in FK order, exports `anomalias.csv`, and cleans `_stage/`.
#   - "full": recomputes Silver -> Features/CQR -> Guardrails -> CQRQuantileClassifier -> Gold
#     end-to-end on Serverless (installs lightgbm if missing), stages to Parquet, and loads UC.

# COMMAND ----------
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import sys
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType

# Resolve repo root when synced under /Workspace/Users/<user>/...
for cand in [Path.cwd(), Path.cwd().parent, Path.cwd().parents[1]]:
    if (cand / "databricks" / "src" / "config.py").is_file():
        if str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
        break

dbutils.widgets.text("mode", "from_stage")
dbutils.widgets.text("llm_endpoint", "databricks-claude-haiku-5-5")
dbutils.widgets.text("llm_top_n", "50")

MODE = dbutils.widgets.get("mode").strip().lower()
LLM_ENDPOINT = dbutils.widgets.get("llm_endpoint").strip()
LLM_TOP_N = int(dbutils.widgets.get("llm_top_n").strip() or "50")

CATALOG_SCHEMA = "dat_hack_rb06.rbe"
UPLOAD_DIR = Path("/Volumes/dat_hack_rb06/raw_sch/raw_vol/uploads")
STAGE_DIR = UPLOAD_DIR / "_stage"

# Columns explicitly omitted on INSERT because they are GENERATED ALWAYS in UC Delta DDL
GENERATED_COLS = {
    "alerts_prioritized": {"alert_id", "impacto_abs_eur"},
}

print(f"Running populate_uc_rbe.py | mode={MODE} | llm_endpoint={LLM_ENDPOINT} | top_n={LLM_TOP_N}")

# COMMAND ----------
# 1. Produce or load Gold DataFrames
if MODE == "full":
    try:
        import lightgbm  # noqa: F401
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "lightgbm>=4.0.0"])

    from databricks.src.backtest import (
        build_neutral_keys,
        create_synthetic_benchmark,
        jury_metrics,
        split_train_eval_restaurants,
    )
    from databricks.src.classifiers import (
        CQRQuantileClassifier,
        EnsembleCQRClassifier,
        HybridMetaClassifier,
    )
    from databricks.src.features import build_features_and_baselines
    from databricks.src.guardrails import apply_guardrails
    from databricks.src.scoring import build_gold_outputs
    from databricks.src.silver import load_raw_datasets, prepare_silver_data

    ventas, restaurantes, calendario = load_raw_datasets()
    silver = prepare_silver_data(ventas, restaurantes, calendario)
    df_feat = build_features_and_baselines(silver, n_folds=6, purge_days=14)
    df_feat = apply_guardrails(df_feat)

    df_tr_raw, df_ev_raw = split_train_eval_restaurants(df_feat, train_frac=0.60, seed=42)
    train_bench = create_synthetic_benchmark(df_tr_raw, n_events=800, seed=101)
    eval_bench = create_synthetic_benchmark(df_ev_raw, n_events=600, seed=2026)
    neutral_keys = build_neutral_keys(eval_bench["eval_mask"], eval_bench["df"])

    best = None
    for cand in (CQRQuantileClassifier(), HybridMetaClassifier(), EnsembleCQRClassifier()):
        cand.fit(
            train_bench["df"],
            y_train=train_bench["y_class"],
            gt_train=train_bench["gt_df"],
            eval_mask_train=train_bench["eval_mask"],
        )
        p_ev = cand.predict_alerts(eval_bench["df"])
        m = jury_metrics(eval_bench["gt_df"], p_ev, neutral_keys=neutral_keys)
        key = (m["f1"], m["precision_at_100"], m["recall_impact_eur"])
        if best is None or key > best[0]:
            best = (key, cand, m)

    assert best is not None
    _, clf, metrics = best
    print(f"Champion={clf.name} | Synthetic OOS Metrics:", json.dumps(metrics, indent=2))

    if "Ensemble" in clf.name:
        model_ver = "v3_ensemble_cqr_ctx"
    elif "Hybrid" in clf.name:
        model_ver = "v2_hybrid_lgbm_cqr"
    else:
        model_ver = "v2_lgbm_cqr_quantiles"

    pred_full = clf.predict_alerts(df_feat)
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    gold = build_gold_outputs(
        df_features=df_feat,
        restaurants_dim=silver["restaurants_dim"],
        pred_alerts=pred_full,
        run_id=run_id,
        model_version=model_ver,
        synthetic_metrics=metrics,
        params_dict={
            "modelo": clf.name,
            "umbral_raw": round(float(clf.threshold), 4),
            "bloques_oof": 6,
            "purge_dias": 14,
            "cobertura_cqr_objetivo": 0.90,
            "f1_oos": metrics["f1"],
            "precision_at_100_oos": metrics["precision_at_100"],
            "recall_impacto_eur_oos": metrics["recall_impact_eur"],
        },
    )
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    for tbl, pdf in gold.items():
        pdf.to_parquet(
            STAGE_DIR / f"gold_{tbl}.parquet",
            index=False,
            coerce_timestamps="us",
            allow_truncated_timestamps=True,
        )
    alerts_pdf = gold["alerts_prioritized"].copy()
    model_runs_pdf = gold["model_runs"].copy()
else:
    req_files = [
        "gold_model_runs.parquet",
        "gold_restaurants_dim.parquet",
        "gold_series_summary.parquet",
        "gold_alerts_prioritized.parquet",
    ]
    missing = [f for f in req_files if not (STAGE_DIR / f).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing staged files in {STAGE_DIR}: {missing}")
    alerts_pdf = pd.read_parquet(STAGE_DIR / "gold_alerts_prioritized.parquet")
    model_runs_pdf = pd.read_parquet(STAGE_DIR / "gold_model_runs.parquet")
    run_id = str(model_runs_pdf.iloc[0]["run_id"])
    print(f"Loaded staged Gold Parquets from {STAGE_DIR} | run_id={run_id} | alerts={len(alerts_pdf):,}")

# COMMAND ----------
# 2. Enrich Top-N alerts using READY Databricks Foundation Model Serving Endpoint (zero pip deps)
llm_success = int(alerts_pdf["llm_cached"].fillna(False).sum()) if len(alerts_pdf) > 0 else 0
if LLM_TOP_N > 0 and LLM_ENDPOINT and len(alerts_pdf) > 0:
    try:
        ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        host_url = ctx.apiUrl().get().rstrip("/")
        api_token = ctx.apiToken().get()
        endpoint_url = f"{host_url}/serving-endpoints/{LLM_ENDPOINT}/invocations"

        system_msg = (
            "Eres analista senior de Operaciones QSR en Restaurant Brands Europe (Burger King España). "
            "Usa EXCLUSIVAMENTE los datos numéricos del JSON proporcionado. No inventes cifras ni uses jerga estadística. "
            "Devuelve SOLO un objeto JSON válido sin bloques markdown con dos claves exactas: "
            "'explicacion_negocio' (máximo 2 frases claras en español de negocio con cifras) y "
            "'recomendacion_revision' (1 acción operativa concreta para gerencia)."
        )

        def _enrich_one(idx: int, ev_str: str):
            payload = json.dumps({
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": ev_str},
                ],
                "temperature": 0.1,
                "max_tokens": 260,
            }).encode("utf-8")
            req = urllib.request.Request(
                endpoint_url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            content = str(content).strip()
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.DOTALL).strip()
            parsed = json.loads(content)
            return idx, parsed

        top_n = min(LLM_TOP_N, len(alerts_pdf))
        todo_indices = [i for i in range(top_n) if not bool(alerts_pdf.at[i, "llm_cached"])]
        llm_success = top_n - len(todo_indices)
        if todo_indices:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futs = [
                    pool.submit(_enrich_one, i, str(alerts_pdf.at[i, "evidencia_json"]))
                    for i in todo_indices
                ]
                for fut in concurrent.futures.as_completed(futs):
                    try:
                        idx, parsed = fut.result()
                        exp = str(parsed.get("explicacion_negocio", "")).strip()
                        rec = str(parsed.get("recomendacion_revision", "")).strip()
                        if exp:
                            alerts_pdf.at[idx, "explicacion_negocio"] = exp
                        if rec:
                            alerts_pdf.at[idx, "recomendacion_revision"] = rec
                        if exp or rec:
                            alerts_pdf.at[idx, "llm_cached"] = True
                            llm_success += 1
                    except Exception as item_exc:
                        print(f"LLM single alert fallback: {item_exc}")

        alerts_pdf.to_parquet(
            STAGE_DIR / "gold_alerts_prioritized.parquet",
            index=False,
            coerce_timestamps="us",
            allow_truncated_timestamps=True,
        )
        print(f"LLM enrichment completed: {llm_success}/{top_n} alerts via {LLM_ENDPOINT}")
    except Exception as llm_exc:
        print(f"Skipping LLM Top-{LLM_TOP_N} enrichment (kept deterministic explanations): {llm_exc}")

# COMMAND ----------
# 3. Atomic Parquet -> Spark CAST -> TRUNCATE + INSERT INTO dat_hack_rb06.rbe.* (preserves views)
def _load_staged_parquet_to_uc(parquet_path: Path, table_name: str):
    full_table = f"{CATALOG_SCHEMA}.{table_name}"
    target_schema = spark.table(full_table).schema
    omit_cols = GENERATED_COLS.get(table_name, set())

    sdf = spark.read.parquet(str(parquet_path))
    select_exprs = []
    insert_cols = []

    for field in target_schema.fields:
        col_name = field.name
        if col_name in omit_cols:
            continue
        if col_name not in sdf.columns:
            raise ValueError(f"Column '{col_name}' missing in staged parquet {parquet_path}")

        col_expr = F.col(col_name)
        dt = field.dataType
        # Clamp floating values cleanly before DECIMAL cast to avoid ANSI overflow
        if isinstance(dt, DecimalType):
            max_abs = float(10 ** (dt.precision - dt.scale) - 10 ** (-dt.scale))
            dbl = col_expr.cast("double")
            col_expr = F.when(
                dbl.isNull() | F.isnan(dbl) | (dbl == float("inf")) | (dbl == float("-inf")),
                F.lit(None),
            ).otherwise(
                F.greatest(F.lit(-max_abs), F.least(F.lit(max_abs), dbl))
            ).cast(dt)
        else:
            col_expr = col_expr.cast(dt)

        select_exprs.append(col_expr.alias(col_name))
        insert_cols.append(col_name)

    aligned = sdf.select(*select_exprs)
    tmp_view = f"tmp_stage_{table_name}"
    aligned.createOrReplaceTempView(tmp_view)

    spark.sql(f"TRUNCATE TABLE {full_table}")
    col_list = ", ".join(insert_cols)
    spark.sql(f"INSERT INTO {full_table} ({col_list}) SELECT {col_list} FROM {tmp_view}")
    cnt = spark.table(full_table).count()
    print(f"Loaded {full_table}: {cnt:,} rows")
    return cnt


# Strict FK order: parent tables first, dependent tables after
c_runs = _load_staged_parquet_to_uc(STAGE_DIR / "gold_model_runs.parquet", "model_runs")
c_dim = _load_staged_parquet_to_uc(STAGE_DIR / "gold_restaurants_dim.parquet", "restaurants_dim")
c_series = _load_staged_parquet_to_uc(STAGE_DIR / "gold_series_summary.parquet", "series_summary")
c_alerts = _load_staged_parquet_to_uc(STAGE_DIR / "gold_alerts_prioritized.parquet", "alerts_prioritized")

# COMMAND ----------
# 4. Export validated anomalias.csv to UC Volume uploads and clean temporary _stage/
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
csv_df = pd.DataFrame({
    "restaurante_id": alerts_pdf["restaurante_id"],
    "canal": alerts_pdf["canal"],
    "fecha": pd.to_datetime(alerts_pdf["fecha"]).dt.strftime("%Y-%m-%d"),
    "tipo": alerts_pdf["tipo"],
    "score": np.round(alerts_pdf["score"].astype(np.float64), 6),
    "impacto_estimado_eur": np.round(alerts_pdf["impacto_estimado_eur"].astype(np.float64), 2),
    "explicacion": alerts_pdf["explicacion_negocio"],
    "recomendacion": alerts_pdf["recomendacion_revision"],
})
csv_out = UPLOAD_DIR / "anomalias.csv"
csv_df.to_csv(csv_out, index=False, encoding="utf-8")
print(f"Exported CSV to {csv_out} ({len(csv_df):,} rows)")

if STAGE_DIR.exists():
    shutil.rmtree(STAGE_DIR, ignore_errors=True)
    print(f"Cleaned temporary staging directory: {STAGE_DIR}")

result_summary = {
    "run_id": run_id,
    "mode": MODE,
    "model_runs": c_runs,
    "restaurants_dim": c_dim,
    "series_summary": c_series,
    "alerts_prioritized": c_alerts,
    "llm_cached_top_n": llm_success,
    "csv_path": str(csv_out),
}
print("POPULATE_UC_SUMMARY:", json.dumps(result_summary))
dbutils.notebook.exit(json.dumps(result_summary))
