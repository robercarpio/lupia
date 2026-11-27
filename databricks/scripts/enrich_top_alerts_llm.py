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

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from databricks.src.config import get_data_dir, get_outputs_dir  # noqa: E402
from databricks.src.scoring import export_anomalias_csv  # noqa: E402

DEFAULT_HOST = "https://adb-7405605482140954.14.azuredatabricks.net"
DEFAULT_PROFILE = "adb-7405605482140954"
DEFAULT_ENDPOINT = "databricks-claude-haiku-5-5"

SYSTEM_PROMPT = (
    "Eres analista senior de Operaciones QSR en Restaurant Brands Europe (Burger King España). "
    "Usa EXCLUSIVAMENTE los datos numéricos y hechos del JSON proporcionado. "
    "REGLAS ESTRICTAS DE NEGOCIO Y ESTILO:\n"
    "1. NO inventes ni menciones causas externas no observadas (PROHIBIDO escribir palabras como TPV, avería, corte eléctrico, obras, huelga, meteorología, tablet o fallo técnico).\n"
    "2. IDIOMA 100 % ESPAÑOL: PROHIBIDO usar palabras en inglés como 'peer', 'peer group', 'group', 'also', 'score' o 'baseline'. Usa siempre 'locales comparables' o 'referencia de la zona'.\n"
    "3. INTERPRETACIÓN DE 'diferencia_vs_locales_comparables_pct': este valor es (desviación_de_este_local − desviación_de_locales_comparables). "
    "Un valor muy negativo (p. ej. -98 %) significa que ESTE local cayó 98 puntos porcentuales MÁS que sus locales comparables (es una caída propia y aislada de este local; NUNCA digas que los locales comparables cayeron igual ni de forma similar o idéntica). "
    "Un valor muy positivo significa que ESTE local subió mucho más que sus locales comparables.\n"
    "4. OTROS CANALES: respeta estrictamente el campo 'estado_otros_canales'. Si indica que el local solo opera un canal, NO menciones otros canales.\n"
    "5. Usa formato numérico español (miles con punto y decimales con coma, ej. 10.381 € y -99,9 %).\n"
    "6. Devuelve SOLO un objeto JSON válido sin markdown con dos claves exactas: "
    "'explicacion_negocio' (máximo 2 frases breves y claras en español de negocio con ventas/tickets reales vs esperados, impacto y comparativa con locales comparables) y "
    "'recomendacion_revision' (1 frase operativa concreta: confirmar apertura/registro completo en caídas o revisar duplicidad/campaña puntual en subidas)."
)

FORBIDDEN_WORDS_RE = re.compile(
    r"\b(peer|group|also|score|baseline|tpv|aver[ií]a|el[eé]ctric\w*|obras|huelga|meteorolog\w*|tablet|fallo\s+t[eé]cnico)\b",
    flags=re.IGNORECASE,
)
LOGICAL_INVERSION_RE = re.compile(
    r"(id[eé]ntic\w*|similar\w*|igual\s+(?:a|que)\s+(?:la\s+de\s+)?(?:los\s+)?(?:locales|pares|red|comparables)"
    r"|en\s+l[ií]nea\s+con\s+(?:los\s+)?(?:locales|pares|red|comparables)"
    r"|los\s+locales\s+comparables\s+(?:también\s+)?cayeron"
    r"|la\s+(?:red|zona)\s+(?:también\s+)?cayó\s+un\s+-?\d)",
    flags=re.IGNORECASE,
)
OTHER_CHANNELS_MENTION_RE = re.compile(
    r"(otros\s+canales|dem[aá]s\s+canales|resto\s+de\s+canales)",
    flags=re.IGNORECASE,
)


def _build_llm_payload(ev_dict: dict) -> dict:
    """Transform internal evidence JSON into an unambiguous all-Spanish payload for the LLM."""
    diff_comp = float(
        ev_dict.get("diferencia_vs_locales_comparables_pct", ev_dict.get("peer_delta_pct", 0.0))
    )
    if diff_comp <= -15.0:
        interp_comp = (
            f"Este restaurante cayó {abs(diff_comp):.1f} puntos porcentuales MÁS que sus locales comparables de la zona "
            "(desviación propia de este restaurante; los comparables NO tuvieron esta caída)."
        )
    elif diff_comp >= 15.0:
        interp_comp = (
            f"Este restaurante subió {abs(diff_comp):.1f} puntos porcentuales MÁS que sus locales comparables de la zona "
            "(desviación propia de este restaurante)."
        )
    else:
        interp_comp = "Desviación moderada respecto a los locales comparables de la zona."

    canales_activos = ev_dict.get("canales_activos_restaurante")
    tiene_otros = ev_dict.get("tiene_otros_canales")
    if tiene_otros is None and isinstance(canales_activos, list):
        tiene_otros = len(canales_activos) > 1
    estado_raw = ev_dict.get("estado_otros_canales")
    if tiene_otros is False or estado_raw == "sin_otros_canales_activos":
        estado_otros = "Este restaurante solo opera este canal (NO tiene otros canales activos; NO menciones otros canales)."
    elif ev_dict.get("otros_canales_normales") is True or estado_raw == "otros_canales_normales":
        estado_otros = "Los demás canales activos del restaurante operaron dentro de lo normal."
    else:
        estado_otros = "Otros canales activos del restaurante también mostraron desviación."

    return {
        "restaurante_id": ev_dict.get("restaurante_id"),
        "canal": ev_dict.get("canal"),
        "fecha": ev_dict.get("fecha"),
        "tipo": ev_dict.get("tipo"),
        "ventas_reales_eur": ev_dict.get("ventas_reales"),
        "ventas_esperadas_eur": ev_dict.get("ventas_esperadas"),
        "impacto_estimado_eur": ev_dict.get("impacto_eur"),
        "desviacion_ventas_pct": ev_dict.get("desviacion_ventas_pct"),
        "tickets_reales": ev_dict.get("tickets_reales"),
        "tickets_esperados": ev_dict.get("tickets_esperados"),
        "desviacion_tickets_pct": ev_dict.get("desviacion_tickets_pct"),
        "ticket_medio_real_eur": ev_dict.get("ticket_medio_real"),
        "ticket_medio_esperado_eur": ev_dict.get("ticket_medio_esperado"),
        "diferencia_vs_locales_comparables_pct": round(diff_comp, 2),
        "interpretacion_locales_comparables": interp_comp,
        "canales_activos_restaurante": canales_activos or [ev_dict.get("canal")],
        "estado_otros_canales": estado_otros,
        "episodio_dias_con_alerta": ev_dict.get("episode_days", 1),
        "episodio_fecha_inicio": ev_dict.get("episode_start_date"),
        "episodio_fecha_fin": ev_dict.get("episode_end_date"),
    }


def _validate_llm_output(exp: str, rec: str, ev_dict: dict) -> list[str]:
    """Validate LLM text against anglicisms, forbidden causes, logical inversions, and phantom channels."""
    errors: list[str] = []
    combined = f"{exp} {rec}"
    bad_words = sorted({m.group(0) for m in FORBIDDEN_WORDS_RE.finditer(combined)})
    if bad_words:
        errors.append(f"Contiene palabras prohibidas o en inglés: {bad_words}")

    diff_comp = float(
        ev_dict.get("diferencia_vs_locales_comparables_pct", ev_dict.get("peer_delta_pct", 0.0))
    )
    if abs(diff_comp) >= 35.0 and LOGICAL_INVERSION_RE.search(exp):
        errors.append(
            f"Inversión lógica sobre locales comparables (diferencia={diff_comp:.1f} %): "
            "no indiques que la caída/subida fue similar o idéntica a la de los comparables."
        )

    tiene_otros = ev_dict.get("tiene_otros_canales")
    if tiene_otros is False and OTHER_CHANNELS_MENTION_RE.search(combined):
        errors.append(
            "El restaurante solo opera un canal activo: no menciones otros canales."
        )
    return errors


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
    ev_dict = json.loads(ev_str)
    llm_input = json.dumps(_build_llm_payload(ev_dict), ensure_ascii=False)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": llm_input},
    ]

    last_err = "No valid response"
    for val_attempt in range(3):
        body_dict = {
            "messages": messages,
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
            last_err = f"Missing keys in LLM JSON response for idx={idx}: {cleaned[:160]}"
            continue

        val_errors = _validate_llm_output(exp, rec, ev_dict)
        if not val_errors:
            ev_dict["resumen"] = exp
            ev_dict["recomendacion"] = rec
            return idx, exp, rec, json.dumps(ev_dict, ensure_ascii=False)

        last_err = f"idx={idx} validation failed (attempt {val_attempt + 1}): {'; '.join(val_errors)}"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": llm_input},
            {"role": "assistant", "content": json.dumps({"explicacion_negocio": exp, "recomendacion_revision": rec}, ensure_ascii=False)},
            {
                "role": "user",
                "content": (
                    "Tu respuesta anterior incumplió estas reglas: "
                    + " | ".join(val_errors)
                    + ". Reescribe SOLO el JSON válido corrigiendo esos fallos."
                ),
            },
        ]

    raise ValueError(last_err)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-cache Top-N LLM explanations in Gold alerts Parquet.")
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--endpoint", default=os.environ.get("DBX_LLM_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--host", default=os.environ.get("DATABRICKS_HOST", DEFAULT_HOST))
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", DEFAULT_PROFILE))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="Regenerate Top-N alerts even if llm_cached=True.")
    args = parser.parse_args()

    out_dir = get_outputs_dir()
    alerts_path = out_dir / "gold_alerts_prioritized.parquet"
    if not alerts_path.is_file():
        raise FileNotFoundError(f"Missing {alerts_path}; run Notebooks 02-03 or databricks/scripts/run_pipeline.py first.")

    alerts_df = pd.read_parquet(alerts_path)
    alerts_df = alerts_df.sort_values("score", ascending=False, kind="mergesort").reset_index(drop=True)

    top_n = min(args.top_n, len(alerts_df))
    if args.force:
        alerts_df.loc[: top_n - 1, "llm_cached"] = False
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
