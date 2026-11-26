"""Repositorio falso: filas con la misma forma (ya convertida) que devuelve Databricks."""
from __future__ import annotations

import datetime as dt
import os
import random

import pytest

os.environ.setdefault("DATABRICKS_WAREHOUSE_ID", "test-warehouse")
os.environ["WARM_UP_ON_START"] = "0"
for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"):
    os.environ.pop(k, None)

from app import database  # noqa: E402
from app.services import dataset  # noqa: E402
from app.services import repository as repo  # noqa: E402

START = dt.date(2026, 6, 1)
DAYS = 122  # hasta 2026-09-30
RESTS = [
    ("R001", "andalucia", "sevilla", "fs", "ordinal", True, True),
    ("R002", "pais-vasco", "vizcaya", "il", "mall", False, True),
    ("R003", "comunidad-de-madrid", "madrid", "dr", "city-center", True, False),
    ("R004", "desconocido", "desconocido", "desconocido", "desconocido", False, False),
]


def _build():
    rnd = random.Random(7)
    series, alerts = [], []
    alert_id = 1
    for rid, ccaa, prov, tip, ubi, ak, dom in RESTS:
        canales = ["sala"] + (["autoking"] if ak else []) + (["domicilio"] if dom else [])
        for canal in canales:
            base = {"sala": 2000, "domicilio": 900, "autoking": 700}[canal]
            for i in range(DAYS):
                d = START + dt.timedelta(days=i)
                esp = base * (1.25 if d.isoweekday() >= 6 else 1.0)
                ventas = esp * (1 + rnd.uniform(-0.08, 0.08))
                anomaly = None
                # Episodio de 3 días de caída en R001 sala y una subida aislada en R002 domicilio.
                if rid == "R001" and canal == "sala" and d in (dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)):
                    ventas, anomaly = esp * 0.45, "caida"
                if rid == "R002" and canal == "domicilio" and d == dt.date(2026, 9, 10):
                    ventas, anomaly = esp * 1.9, "subida"
                tickets = int(ventas / 12)
                tesp = esp / 12
                festivo = "national_holidays" if d == dt.date(2026, 8, 15) else ""
                row = {
                    "restaurante_id": rid, "canal": canal, "fecha": d.isoformat(), "ventas": round(ventas, 2),
                    "tickets": tickets, "ticket_medio": round(ventas / max(1, tickets), 2),
                    "ventas_esperadas": round(esp, 2), "ventas_p05": round(esp * 0.85, 2), "ventas_p95": round(esp * 1.15, 2),
                    "tickets_esperados": round(tesp, 2), "tickets_p05": round(tesp * 0.85, 2), "tickets_p95": round(tesp * 1.15, 2),
                    "ticket_medio_esperado": 12.0, "impacto_estimado_eur": round(ventas - esp, 2),
                    "desviacion_ventas_pct": round((ventas / esp - 1) * 100, 2),
                    "desviacion_tickets_pct": round((tickets / tesp - 1) * 100, 2),
                    "z_ventas": round((ventas / esp - 1) / 0.06, 3), "z_tickets": round((tickets / tesp - 1) / 0.06, 3),
                    "z_ticket_medio": 0.1, "z_peer_diff": None, "z_cross_channel": None, "dia_semana": d.isoweekday(),
                    "tipo_festivo": festivo, "vispera_festivo": False, "fecha_especial": "", "navidad": False,
                    "semana_santa": False, "is_eligible_for_alert": not festivo,
                    "exclusion_reason": "festivo" if festivo else None, "is_systemic_day": False,
                    "is_anomaly": anomaly is not None, "anomaly_tipo": anomaly,
                    "motivo": "festivo" if festivo else "normal",
                }
                series.append(row)
                if anomaly:
                    ep = f"EP-{rid}-{canal}-{'20260901' if rid == 'R001' else '20260910'}"
                    n = 3 if rid == "R001" else 1
                    alerts.append({
                        "alert_id": alert_id, "restaurante_id": rid, "canal": canal, "fecha": d.isoformat(),
                        "tipo": anomaly, "score": 0.97 if anomaly == "caida" else 0.71, "episode_id": ep,
                        "episode_start_date": "2026-09-01" if n == 3 else d.isoformat(),
                        "episode_end_date": "2026-09-03" if n == 3 else d.isoformat(), "episode_days": n,
                        "episode_day_index": (d - dt.date(2026, 9, 1)).days + 1 if n == 3 else 1,
                        "episode_total_impact_eur": -3300.0 if n == 3 else 810.0, "comunidad_autonoma": ccaa,
                        "provincia": prov, "tipologia": tip, "ubicacion": ubi, "priority_score": 91.5 if n == 3 else 48.0,
                        "prioridad_nivel": "P1" if n == 3 else "P3", "metrica_principal": "ambas",
                        "ventas_reales": row["ventas"], "ventas_esperadas": row["ventas_esperadas"],
                        "impacto_estimado_eur": row["impacto_estimado_eur"], "impacto_abs_eur": abs(row["impacto_estimado_eur"]),
                        "desviacion_ventas_pct": row["desviacion_ventas_pct"], "tickets_reales": tickets,
                        "tickets_esperados": row["tickets_esperados"], "desviacion_tickets_pct": row["desviacion_tickets_pct"],
                        "ticket_medio_real": row["ticket_medio"], "ticket_medio_esperado": 12.0,
                        "contribucion_tickets_pct": 96.0, "contribucion_tm_pct": 4.0, "z_ventas": row["z_ventas"],
                        "z_tickets": row["z_tickets"], "z_ticket_medio": 0.1,
                        "z_max": max(abs(row["z_ventas"]), abs(row["z_tickets"])), "z_peer_diff": -8.4,
                        "peer_delta_pct": 1.2, "z_cross_channel": -6.0, "otros_canales_normales": True,
                        "explicacion_negocio": None, "recomendacion_revision": None,
                        "evidencia_json": {"calendario": "laborable", "pares_normales": True}, "llm_cached": False,
                        "run_id": "run-1", "created_at": "2026-11-26T10:00:00",
                    })
                    alert_id += 1
    return series, alerts


SERIES, ALERTS = _build()
RUN = {"run_id": "run-1", "model_version": "v1", "is_active": True, "created_at": "2026-11-26T10:00:00",
       "eval_start_date": "2023-08-05", "eval_end_date": "2026-09-30", "total_candidate_rows": len(SERIES),
       "total_alerts": len(ALERTS), "total_caidas": 3, "total_subidas": 1, "f1_synthetic": 0.61,
       "precision_synthetic": 0.7, "recall_synthetic": 0.54, "precision_at_100_synthetic": 0.8,
       "params_json": {"z_threshold": 4.0, "min_rel_deviation": 0.3}}


def _rest_rows():
    out = []
    for rid, ccaa, prov, tip, ubi, ak, dom in RESTS:
        out.append({"restaurante_id": rid, "marca": "Burger King", "pais": "España", "comunidad_autonoma": ccaa,
                    "provincia": prov, "provincia_inferida": None, "is_orphan_master": prov == "desconocido",
                    "tipologia": tip, "tamano": "n", "ubicacion": ubi, "imagen": "prime", "terraza": "si",
                    "tiene_autoking": ak, "tiene_domicilio": dom, "distancia_reparto": "m", "estado": "estandar",
                    "primera_venta": "2023-07-01", "ultima_venta": "2026-09-30", "mediana_ventas_diaria_eur": 3100.0,
                    "total_alertas": sum(1 for a in ALERTS if a["restaurante_id"] == rid)})
    return out


REST_ROWS = _rest_rows()
REST_GEO = {r["restaurante_id"]: r for r in REST_ROWS}


def _sel(rows, f: repo.SqlFilter):
    out = []
    for s in rows:
        r = REST_GEO[s["restaurante_id"]]
        if f.restaurante and s["restaurante_id"] != f.restaurante:
            continue
        if f.ccaa_slug and r["comunidad_autonoma"] != f.ccaa_slug:
            continue
        if f.provincia_slug and r["provincia"] != f.provincia_slug:
            continue
        if f.canales and s["canal"] not in f.canales:
            continue
        out.append(s)
    return out


def fake_timeline(d_from, d_to, f):
    agg = {}
    for s in _sel(SERIES, f):
        if d_from <= s["fecha"] <= d_to:
            a = agg.setdefault(s["fecha"], {"fecha": s["fecha"], "ventas_totales": 0.0, "tickets_totales": 0, "ventas": 0.0,
                                            "esperadas": 0.0, "filas": 0, "filas_con_baseline": 0})
            a["ventas_totales"] += s["ventas"]
            a["tickets_totales"] += s["tickets"]
            a["filas"] += 1
            if s["ventas_esperadas"] is not None:
                a["ventas"] += s["ventas"]
                a["esperadas"] += s["ventas_esperadas"]
                a["filas_con_baseline"] += 1
    return [agg[k] for k in sorted(agg)]


def _key(dim, s):
    r = REST_GEO[s["restaurante_id"]]
    return {"canal": s["canal"], "restaurante": s["restaurante_id"], "comunidad": r["comunidad_autonoma"],
            "provincia": r["provincia"], "tipologia": r["tipologia"], "ubicacion": r["ubicacion"],
            "dia_semana": s["dia_semana"], "mes": s["fecha"][:7]}[dim]


def fake_sales_by(dimension, d_from, d_to, f, *, prev_from=None, sort="ventas", limit=50):
    """Mismo contrato que repository.sales_by, calculado en Python sobre la serie falsa."""
    lo = prev_from or d_from
    g = {}
    for s in _sel(SERIES, f):
        if f.restaurantes and s["restaurante_id"] not in f.restaurantes:
            continue
        if not lo <= s["fecha"] <= d_to:
            continue
        x = g.setdefault(_key(dimension, s), {"clave": _key(dimension, s), "ventas": None, "tickets": None, "rest": set(),
                                               "d": set(), "filas": 0, "ventas_prev": None, "tickets_prev": None, "dp": set()})
        if s["fecha"] >= d_from:
            x["ventas"] = (x["ventas"] or 0) + s["ventas"]
            x["tickets"] = (x["tickets"] or 0) + s["tickets"]
            x["rest"].add(s["restaurante_id"]); x["d"].add(s["fecha"]); x["filas"] += 1
        else:
            x["ventas_prev"] = (x["ventas_prev"] or 0) + s["ventas"]
            x["tickets_prev"] = (x["tickets_prev"] or 0) + s["tickets"]
            x["dp"].add(s["fecha"])
    rows = [{"clave": x["clave"], "ventas": x["ventas"], "tickets": x["tickets"], "restaurantes": len(x["rest"]),
             "dias": len(x["d"]), "filas": x["filas"], "ventas_prev": x["ventas_prev"], "tickets_prev": x["tickets_prev"],
             "dias_prev": len(x["dp"])} for x in g.values() if x["ventas"] is not None]
    keyf = {"ventas": lambda r: -r["ventas"], "ventas_asc": lambda r: r["ventas"], "tickets": lambda r: -r["tickets"],
            "variacion": lambda r: -(r["ventas"] / r["ventas_prev"]) if r["ventas_prev"] else 0,
            "variacion_asc": lambda r: (r["ventas"] / r["ventas_prev"]) if r["ventas_prev"] else 9e9,
            "ticket_medio": lambda r: -(r["ventas"] / r["tickets"]), "clave": lambda r: r["clave"]}[sort]
    return [{**r, "total_grupos": len(rows)} for r in sorted(rows, key=keyf)[:limit]]


def fake_restaurant_counts(d_from, d_to, f, prev_from=None):
    lo = prev_from or d_from
    rows = [s for s in _sel(SERIES, f) if lo <= s["fecha"] <= d_to]
    return {"actual": len({s["restaurante_id"] for s in rows if s["fecha"] >= d_from}),
            "anterior": len({s["restaurante_id"] for s in rows if s["fecha"] < d_from})}


def fake_sales_geo(d_from, d_to, f, prev_from=None):
    out = []
    for r in fake_sales_by("provincia", d_from, d_to, f, prev_from=prev_from, limit=999):
        ccaa = next(x["comunidad_autonoma"] for x in REST_ROWS if x["provincia"] == r["clave"])
        out.append({**r, "ccaa": ccaa})
    return out


def fake_sales_groups(dimension, d_from, d_to, f):
    return len({_key(dimension, s) for s in _sel(SERIES, f) if d_from <= s["fecha"] <= d_to})


def fake_episodes(**kw):
    eps = {}
    for a in ALERTS:
        e = eps.setdefault(a["episode_id"], {"episode_id": a["episode_id"], "restaurante_id": a["restaurante_id"],
                                             "canal": a["canal"], "tipo": a["tipo"], "comunidad_autonoma": a["comunidad_autonoma"],
                                             "provincia": a["provincia"], "tipologia": a["tipologia"], "ubicacion": a["ubicacion"],
                                             "fecha_inicio": a["fecha"], "fecha_fin": a["fecha"], "duracion_dias": 0,
                                             "max_score": 0, "max_priority_score": 0, "prioridad_nivel": a["prioridad_nivel"],
                                             "impacto_total_eur": 0.0, "impacto_abs_total_eur": 0.0,
                                             "desviacion_media_ventas_pct": a["desviacion_ventas_pct"],
                                             "desviacion_media_tickets_pct": a["desviacion_tickets_pct"],
                                             "explicacion_principal": "Caída sostenida en sala", "recomendacion_principal": "Revisar"})
        e["fecha_fin"] = max(e["fecha_fin"], a["fecha"])
        e["duracion_dias"] += 1
        e["max_score"] = max(e["max_score"], a["score"])
        e["impacto_total_eur"] += a["impacto_estimado_eur"]
        e["impacto_abs_total_eur"] += a["impacto_abs_eur"]
    rows = [e for e in eps.values() if e["fecha_fin"] >= kw["date_from"] and e["fecha_inicio"] <= kw["date_to"]
            and (not kw.get("tipo") or e["tipo"] == kw["tipo"]) and e["duracion_dias"] >= kw.get("min_dias", 1)
            and (not kw.get("prioridad_nivel") or e["prioridad_nivel"] == kw["prioridad_nivel"])
            and (not kw.get("restaurante") or e["restaurante_id"] == kw["restaurante"])]
    return len(rows), rows[kw.get("offset", 0): kw.get("offset", 0) + kw.get("limit", 25)]


@pytest.fixture(autouse=True)
def fake_repo(monkeypatch):
    database.clear_cache()
    dataset.reset_state()
    monkeypatch.setattr(repo, "sales_by", fake_sales_by)
    monkeypatch.setattr(repo, "sales_groups", fake_sales_groups)
    monkeypatch.setattr(repo, "anomaly_fingerprint", lambda: {"alertas": len(ALERTS), "ultima_alerta": "2026-11-26T10:00:00",
                                                              "ejecuciones": 1, "ultima_ejecucion": "2026-11-26T10:00:00"})
    dataset._state["sync"] = True
    monkeypatch.setattr(repo, "sales_restaurant_counts", fake_restaurant_counts)
    monkeypatch.setattr(repo, "sales_geo", fake_sales_geo)
    monkeypatch.setattr(repo, "series_overview", lambda: ({"date_min": "2026-06-01", "date_max": "2026-09-30",
                                                           "filas": len(SERIES), "filas_con_baseline": len(SERIES)},
                                                          {"normal": len(SERIES) - 9, "festivo": 9}))
    monkeypatch.setattr(repo, "active_run", lambda: RUN)
    monkeypatch.setattr(repo, "list_runs", lambda limit=20: [RUN])
    monkeypatch.setattr(repo, "restaurants", lambda: REST_ROWS)
    monkeypatch.setattr(repo, "alerts", lambda run_id: ALERTS)
    monkeypatch.setattr(repo, "alerts_by_episode", lambda ep: [a for a in ALERTS if a["episode_id"] == ep])
    monkeypatch.setattr(repo, "timeline_daily", fake_timeline)
    monkeypatch.setattr(repo, "series_rows", lambda rid, canal, a, b: [
        s for s in SERIES if s["restaurante_id"] == rid and (not canal or s["canal"] == canal) and a <= s["fecha"] <= b])
    monkeypatch.setattr(repo, "series_day", lambda rid, d: [s for s in SERIES if s["restaurante_id"] == rid and s["fecha"] == d])
    monkeypatch.setattr(repo, "peers_day", lambda d, canal, ccaa, rid: {"restaurantes": 3, "ventas": 6100.0, "esperadas": 6000.0,
                                                                         "con_anomalia": 0})
    monkeypatch.setattr(repo, "day_reason_counts", lambda d: {"normal": 9})
    monkeypatch.setattr(repo, "kpi_global", lambda: {"total_alertas_dia": 4, "total_episodios": 2, "restaurantes_afectados": 2,
                                                     "total_caidas": 3, "total_subidas": 1, "alertas_p1": 3,
                                                     "impacto_caidas_eur": -3300.0, "impacto_subidas_eur": 810.0,
                                                     "impacto_bruto_total_eur": 4110.0})
    monkeypatch.setattr(repo, "episodes", fake_episodes)
    monkeypatch.setattr(repo, "episode", lambda ep: next((e for e in fake_episodes(date_from="0000", date_to="9999")[1]
                                                          if e["episode_id"] == ep), None))
    monkeypatch.setattr(repo, "anomalias_export", lambda: [
        {"restaurante_id": a["restaurante_id"], "canal": a["canal"], "fecha": a["fecha"], "tipo": a["tipo"],
         "score": a["score"], "impacto_estimado_eur": a["impacto_estimado_eur"], "explicacion": "Caída, sin festivo"}
        for a in sorted(ALERTS, key=lambda x: -x["score"])])
    yield
    database.clear_cache()
    dataset.reset_state()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c

