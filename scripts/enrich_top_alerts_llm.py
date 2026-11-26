"""Enrich Top-N prioritized alerts via Databricks Foundation Model Endpoint (databricks-claude-haiku-5-5)."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from databricks.src.config import get_data_dir, get_outputs_dir  # noqa: E402
from databricks.src.scoring import export_anomalias_csv  # noqa: E402

DEFAULT_HOST = "https://adb-7405605482140954.14.azuredatabricks.net"
DEFAULT_PROFILE = "adb-7405605482140954"
DEFAULT_ENDPOINT = "databricks-claude-haiku-5-5"

SYSTEM_PROMPT = (
    "Eres analista senior de Operaciones QSR en Restaurant Brands Europe (Burger King España). "
    "Usa EXCLUSIVAMENTE los datos numéricos del JSON proporcionado. "
    "REGLA ESTRICTA DEL RETO: NO inventes ni menciones causas externas no observadas "
    "(PROHIBIDO escribir palabras como TPV, avería, corte eléctrico, obras, huelga, meteorología, tablet o fallo técnico). "
    "Usa formato numérico español (miles con punto y decimales con coma, ej. 10.381 € y -99,9 %). "
    "Devuelve SOLO un objeto JSON válido sin markdown con dos claves exactas: "
    "'explicacion_negocio' (máximo 2 frases breves y claras en español de negocio con ventas/tickets reales vs esperados e impacto) y "
    "'recomendacion_revision' (1 frase operativa concreta: confirmar apertura/registro completo en caídas o revisar duplicidad/campaña puntual en subidas)."
)


def _resolve_auth_token(host: str, profile: str) -> str:
    env_tok = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if env_tok:
        return env_tok
    raw = subprocess.check_output(
        ["databricks", "auth", "token", "-p", profile, "--host", host],
        text=True,
    )
    return json.loads(raw)["access_token"]


def _enrich_single_alert(
    idx: int,
    ev_str: str,
    endpoint_url: str,
    token: str,
) -> tuple[int, str, str, str]:
    body_dict = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ev_str},
        ],
        "max_tokens": 1800,
    }
    # Reasoning models on Databricks (-5-5) reject explicit `temperature`
    if "5-5" not in endpoint_url and "opus-5" not in endpoint_url and "sonnet-5" not in endpoint_url:
        body_dict["temperature"] = 0.1
        body_dict["max_tokens"] = 500
    payload = json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(
        endpoint_url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    body = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as err:
            if err.code in (429, 502, 503, 504) and attempt < 5:
                time.sleep(min(20.0, (1.8 ** attempt) + random.uniform(0.2, 1.0)))
                continue
            raise
    assert body is not None

    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type", "text") == "text"
        )
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip(), flags=re.DOTALL).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if m:
        cleaned = m.group(0)
    parsed = json.loads(cleaned)

    exp = str(parsed.get("explicacion_negocio", "")).strip()
    rec = str(parsed.get("recomendacion_revision", "")).strip()
    if not exp or not rec:
        raise ValueError(f"Missing keys in LLM JSON response for idx={idx}: {cleaned[:160]}")

    ev_dict = json.loads(ev_str)
    ev_dict["resumen"] = exp
    ev_dict["recomendacion"] = rec
    return idx, exp, rec, json.dumps(ev_dict, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-cache Top-N LLM explanations in Gold alerts Parquet.")
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--endpoint", default=os.environ.get("DBX_LLM_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--host", default=os.environ.get("DATABRICKS_HOST", DEFAULT_HOST))
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", DEFAULT_PROFILE))
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    out_dir = get_outputs_dir()
    alerts_path = out_dir / "gold_alerts_prioritized.parquet"
    if not alerts_path.is_file():
        raise FileNotFoundError(f"Missing {alerts_path}; run Notebooks 02-03 or scripts/run_pipeline.py first.")

    alerts_df = pd.read_parquet(alerts_path)
    alerts_df = alerts_df.sort_values("score", ascending=False, kind="mergesort").reset_index(drop=True)

    top_n = min(args.top_n, len(alerts_df))
    todo = [i for i in range(top_n) if not bool(alerts_df.at[i, "llm_cached"])]
    print(f"Top-{top_n} LLM check: already_cached={top_n - len(todo)} | pending={len(todo)} | endpoint={args.endpoint}")

    failures = []
    if todo:
        token = _resolve_auth_token(args.host.rstrip("/"), args.profile)
        endpoint_url = f"{args.host.rstrip('/')}/serving-endpoints/{args.endpoint}/invocations"
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = [
                pool.submit(_enrich_single_alert, i, str(alerts_df.at[i, "evidencia_json"]), endpoint_url, token)
                for i in todo
            ]
            for fut in concurrent.futures.as_completed(futs):
                try:
                    idx, exp, rec, ev_updated = fut.result()
                    alerts_df.at[idx, "explicacion_negocio"] = exp
                    alerts_df.at[idx, "recomendacion_revision"] = rec
                    alerts_df.at[idx, "evidencia_json"] = ev_updated
                    alerts_df.at[idx, "llm_cached"] = True
                except Exception as exc:
                    failures.append(str(exc))

    cached_count = int(alerts_df.iloc[:top_n]["llm_cached"].sum())
    print(f"Top-{top_n} enrichment result: llm_cached={cached_count}/{top_n} | failures={len(failures)}")
    if failures:
        print("Sample failures:", failures[:3])

    alerts_df.to_parquet(
        alerts_path,
        index=False,
        coerce_timestamps="us",
        allow_truncated_timestamps=True,
    )

    csv_path = export_anomalias_csv(alerts_df, out_dir / "anomalias.csv")
    validator = get_data_dir() / "validar_entrega.py"
    proc = subprocess.run([sys.executable, str(validator), str(csv_path)], capture_output=True, text=True, check=False)
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print("STDERR:", proc.stderr)
        return proc.returncode

    max_allowed_fail = max(1, int(top_n * 0.10))
    if len(failures) > max_allowed_fail:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
