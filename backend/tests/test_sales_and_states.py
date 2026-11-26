"""Ventas reales, estados vacíos del modelo, recarga automática de anomalías y memoria del copiloto."""
from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from app import database
from app.services import dataset
from app.services import repository as repo

import conftest
from conftest import ALERTS, SERIES
from test_api import _sse

ALL = quote(json.dumps({"from": "2026-06-01", "to": "2026-09-30"}))
SEPT = quote(json.dumps({"from": "2026-09-01", "to": "2026-09-30"}))


def _sum(rid=None, d_from="2026-09-01", d_to="2026-09-30", canal=None):
    return sum(s["ventas"] for s in SERIES if d_from <= s["fecha"] <= d_to and (not rid or s["restaurante_id"] == rid)
               and (not canal or s["canal"] == canal))


# ── Ventas ────────────────────────────────────────────────────────────
def test_sales_summary_matches_rows_and_previous_period(client):
    s = client.get(f"/api/sales/summary?f={SEPT}").json()
    assert s["ventas"] == pytest.approx(_sum(), abs=0.05)
    assert s["prevRange"] == {"from": "2026-08-02", "to": "2026-08-31"}
    assert s["prev"]["ventas"] == pytest.approx(_sum(d_from="2026-08-02", d_to="2026-08-31"), abs=0.05)
    assert s["variacion"]["ventas"] == pytest.approx(s["ventas"] / s["prev"]["ventas"] - 1, abs=1e-4)
    assert s["restaurantes"] == 4 and s["dias"] == 30 and s["ticket_medio"] > 0
    assert len(s["spark"]["ventas"]) == 24


def test_sales_summary_without_comparable_previous_period(client):
    # Los datos falsos empiezan el 2026-06-01: el periodo anterior a «todo» no existe → prev = None, no ceros.
    s = client.get(f"/api/sales/summary?f={ALL}").json()
    assert s["prev"] is None and s["prevRange"] is None and s["variacion"]["ventas"] is None


def test_sales_filters_and_channels(client):
    s = client.get("/api/sales/summary?from=2026-09-01&to=2026-09-30&restaurante=R002&canal=domicilio").json()
    assert s["ventas"] == pytest.approx(_sum("R002", canal="domicilio"), abs=0.05)
    ch = client.get(f"/api/sales/channels?f={SEPT}").json()["rows"]
    assert {c["canal"] for c in ch} == {"sala", "domicilio", "autoking"}
    assert sum(c["share"] for c in ch) == pytest.approx(1, abs=1e-3)


def test_sales_ranking_and_sorting(client):
    r = client.get(f"/api/sales/ranking?f={SEPT}&sort=ventas&limit=2").json()
    assert r["total"] == 4 and len(r["rows"]) == 2
    assert r["rows"][0]["ventas"] >= r["rows"][1]["ventas"]
    assert r["rows"][0]["provincia"]  # enriquecido con el maestro
    low = client.get(f"/api/sales/ranking?f={SEPT}&sort=variacion_asc").json()["rows"]
    vs = [x["variacion"] for x in low]
    assert vs == sorted(vs)
    assert client.get(f"/api/sales/ranking?f={SEPT}&sort=hack").status_code == 400


def test_sales_breakdown_geo_and_compare(client):
    p = client.get(f"/api/sales/breakdown/provincia?f={SEPT}").json()["rows"]
    assert {"Sevilla", "Bizkaia", "Madrid", "Sin dato de maestro"} == {x["clave"] for x in p}
    m = client.get(f"/api/sales/breakdown/mes?f={ALL}").json()["rows"]
    assert [x["clave"] for x in m] == ["2026-06", "2026-07", "2026-08", "2026-09"]
    assert client.get(f"/api/sales/breakdown/producto?f={SEPT}").status_code == 400
    c = client.get(f"/api/sales/compare?f={SEPT}&ids=R001&ids=3").json()["rows"]
    assert [x["restaurante_id"] for x in c] == ["R001", "R003"]
    assert c[0]["ventas_periodo"]["ventas"] == pytest.approx(_sum("R001"), abs=0.05) and c[0]["anomalias"]["total"] == 3
    assert client.get(f"/api/sales/compare?f={SEPT}&ids=R999").status_code == 404
    cp = client.get("/api/sales/compare-periods?a_from=2026-08-01&a_to=2026-08-31&b_from=2026-09-01&b_to=2026-09-30").json()
    assert cp["b"]["ventas"] == pytest.approx(_sum(), abs=0.05)


def test_dashboard_includes_sales_and_model_status(client):
    d = client.get(f"/api/dashboard?f={SEPT}").json()
    assert d["sales"]["kpis"]["ventas"] == pytest.approx(_sum(), abs=0.05)
    assert d["sales"]["geo"]["provincia"] and d["sales"]["channels"]
    assert d["modelStatus"]["anomalias"] == "ok" and d["timeline"]["baseline"] == "completo"
    assert all("tickets_totales" in b for b in d["timeline"]["buckets"])


def test_restaurant_detail_includes_sales(client):
    r = client.get(f"/api/restaurants/R001?f={SEPT}").json()
    assert r["ventas"]["ventas"] == pytest.approx(_sum("R001"), abs=0.05) and r["ventas_por_canal"]


# ── Tablas del modelo vacías (estado real de hoy en Databricks) ───────
@pytest.fixture
def empty_model(monkeypatch):
    database.clear_cache()
    dataset.reset_state()
    monkeypatch.setattr(repo, "active_run", lambda: None)
    monkeypatch.setattr(repo, "list_runs", lambda limit=20: [])
    monkeypatch.setattr(repo, "alerts", lambda run_id: [])
    monkeypatch.setattr(repo, "anomaly_fingerprint", lambda: {"alertas": 0, "ultima_alerta": None, "ejecuciones": 0,
                                                              "ultima_ejecucion": None})
    monkeypatch.setattr(repo, "series_overview", lambda: ({"date_min": "2026-06-01", "date_max": "2026-09-30",
                                                           "filas": len(SERIES), "filas_con_baseline": 0}, {"normal": len(SERIES)}))
    plain = [{**s, "ventas_esperadas": None} for s in SERIES]
    pass

    monkeypatch.setattr(conftest, "SERIES", plain)
    yield


def test_empty_model_tables_give_informative_state_not_fake_data(client, empty_model):
    m = client.get("/api/meta").json()
    assert m["totalAnomalias"] == 0 and m["modelRun"] is None
    assert m["modelStatus"] == {**m["modelStatus"], "anomalias": "pendiente", "baseline": "pendiente"}
    assert "zThreshold" not in m["config"]  # no se presentan umbrales inventados
    d = client.get(f"/api/dashboard?f={SEPT}").json()
    assert d["kpis"]["total"] == 0 and d["channels"][0]["total"] == 0
    assert d["sales"]["kpis"]["ventas"] > 0  # las ventas reales siguen disponibles
    assert d["timeline"]["baseline"] == "pendiente"
    assert all(b["ventas_totales"] > 0 for b in d["timeline"]["buckets"])
    t = client.get(f"/api/anomalies?f={ALL}").json()
    assert t["total"] == 0 and t["rows"] == []
    assert client.get(f"/api/episodes?f={ALL}").json()["total"] >= 0
    assert client.get("/api/anomalies/R001-sala-2026-09-01").status_code == 404


def test_empty_model_chat_explains_and_answers_with_sales(client, empty_model):
    ev = _sse(client.post("/api/chat", json={"message": "¿Qué anomalías hay?", "dashboardFilters": {"from": "2026-09-01",
                                                                                                "to": "2026-09-30"}}).text)
    text = "".join(d["delta"] for e, d in ev if e == "text")
    assert "todavía no ha escrito resultados" in text
    assert not any(e == "block" and d["block"]["type"] == "anomalies" for e, d in ev)
    ev = _sse(client.post("/api/chat", json={"message": "¿Cuáles han sido las ventas?",
                                             "dashboardFilters": {"from": "2026-09-01", "to": "2026-09-30"}}).text)
    blocks = [d["block"]["type"] for e, d in ev if e == "block"]
    assert blocks == ["sales_kpis", "sales_ranking"]
    text = "".join(d["delta"] for e, d in ev if e == "text")
    assert "2026-09-01" in text and "2026-09-30" in text  # el periodo se dice siempre


def test_new_alerts_appear_without_code_changes(client, monkeypatch, empty_model):
    assert client.get("/api/meta").json()["totalAnomalias"] == 0
    v0 = client.get("/api/status").json()["version"]
    # El modelo externo escribe alertas: la huella cambia y el snapshot se recarga solo.
    from conftest import RUN

    monkeypatch.setattr(repo, "alerts", lambda run_id: ALERTS)
    monkeypatch.setattr(repo, "active_run", lambda: RUN)
    monkeypatch.setattr(repo, "anomaly_fingerprint", lambda: {"alertas": len(ALERTS), "ultima_alerta": "x", "ejecuciones": 1,
                                                              "ultima_ejecucion": "y"})
    dataset._state["checked"] = 0.0  # simula que ha pasado ANOMALY_REFRESH_SECONDS
    assert client.get("/api/meta").json()["totalAnomalias"] == 4
    st = client.get("/api/status").json()
    assert st["version"] != v0 and st["modelStatus"]["anomalias"] == "ok"


# ── Errores y seguridad ───────────────────────────────────────────────
def test_database_errors_are_not_empty_success(client, monkeypatch):
    def boom(*a, **k):
        raise database.DataError("Error en la consulta a Databricks: TABLE_OR_VIEW_NOT_FOUND")

    monkeypatch.setattr(repo, "sales_by", boom)
    r = client.get(f"/api/sales/ranking?f={SEPT}")
    assert r.status_code == 500 and r.json()["code"] == "DB_ERROR"


def test_read_only_guard():
    for bad in ("DROP TABLE x", "DELETE FROM x", "SELECT 1; DROP TABLE x", "INSERT INTO x VALUES (1)"):
        with pytest.raises(database.BadRequest):
            database._run(bad, None)


def test_settings_accept_catalog_schema_and_http_path(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("DATABRICKS_CATALOG", "cat")
    monkeypatch.setenv("DATABRICKS_SCHEMA", "sch")
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc123")
    s = Settings()
    assert s.rbe_schema == "cat.sch" and s.warehouse_id == "abc123"
    monkeypatch.setenv("DATABRICKS_SCHEMA", "sch; DROP")
    with pytest.raises(ValueError):
        Settings()


# ── Copiloto: herramientas de ventas y seguimiento ────────────────────
def test_copilot_sales_tools_and_follow_up_memory(client):
    from app.services import copilot
    from app.services.dataset import get_dataset

    ds = get_dataset()
    session = {"id": "t", "messages": []}
    out = copilot.run_tool(ds, "ranking_ventas", {"filtros": {"desde": "2026-09-01", "hasta": "2026-09-30"}, "limite": 3,
                                                  "mostrar": True}, session)
    top = [r["restaurante_id"] for r in out["data"]["ranking"]]
    assert len(top) == 3 and out["block"]["type"] == "sales_ranking"
    assert session["restaurantes"] == top and session["periodo"] == {"desde": "2026-09-01", "hasta": "2026-09-30"}
    # «¿Y cómo han evolucionado respecto al mes anterior?» → comparar esos mismos restaurantes.
    cmp = copilot.run_tool(ds, "comparar_restaurantes", {"restaurantes": session["restaurantes"],
                                                         "filtros": {"desde": "2026-09-01", "hasta": "2026-09-30"}}, session)
    assert [r["restaurante_id"] for r in cmp["data"]["restaurantes"]] == top
    assert cmp["data"]["periodo_anterior"] == {"from": "2026-08-02", "to": "2026-08-31"}
    s = copilot.run_tool(ds, "resumen_ventas", {"filtros": {"desde": "2026-09-01", "hasta": "2026-09-30"}}, session)
    assert s["data"]["ventas"] == pytest.approx(_sum(), abs=0.05)
    d = copilot.run_tool(ds, "desglose_ventas", {"dimension": "canal"}, session)
    assert {g["clave"] for g in d["data"]["grupos"]} == {"sala", "domicilio", "autoking"}


def test_history_is_trimmed_and_old_tool_results_summarised():
    from app.services import copilot

    msgs = []
    for i in range(12):
        msgs += [{"role": "user", "content": f"q{i}"},
                 {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 5000},
                 {"role": "assistant", "content": f"a{i}"}]
    out = copilot._trim(msgs)
    assert out[0] == {"role": "user", "content": "q4"}  # últimos 8 turnos
    tools = [m for m in out if m["role"] == "tool"]
    assert all(len(m["content"]) < 1300 for m in tools[:-2]) and all(len(m["content"]) == 5000 for m in tools[-2:])


# ── Rendimiento: caché stale-while-revalidate, paralelismo y refresco sin bloqueo ──
def _wait(cond, t=5.0):
    import time
    end = time.monotonic() + t
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_stale_cache_is_served_instantly_and_refreshed_in_background(monkeypatch):
    import time

    calls = []
    monkeypatch.setattr(database, "_run", lambda sql, params: calls.append(1) or [{"v": len(calls)}])
    database.clear_cache()
    assert database.query("SELECT 1") == [{"v": 1}]
    key = ("SELECT 1", ())
    database._cache[key] = (time.monotonic() - 10_000, [{"v": "viejo"}])  # caducado, pero dentro de la ventana stale
    assert database.query("SELECT 1") == [{"v": "viejo"}]  # se sirve al instante
    assert _wait(lambda: database._cache[key][1] == [{"v": 2}])  # y se refresca detrás
    assert database.query("SELECT 1") == [{"v": 2}]


def test_parallel_runs_concurrently_and_propagates_errors():
    import time

    t = time.perf_counter()
    assert database.parallel(lambda: time.sleep(0.3) or 1, lambda: time.sleep(0.3) or 2) == [1, 2]
    assert time.perf_counter() - t < 0.55

    def boom():
        raise database.DataError("fallo")

    with pytest.raises(database.DataError):
        database.parallel(lambda: 1, boom)


def test_model_refresh_does_not_block_requests(client, monkeypatch):
    import time

    client.get("/api/meta")  # snapshot inicial
    dataset._state["sync"] = False
    dataset._state["checked"] = 0.0
    monkeypatch.setattr(repo, "anomaly_fingerprint", lambda: time.sleep(1.0) or {"alertas": 99})
    t = time.perf_counter()
    assert client.get("/api/meta").status_code == 200
    assert time.perf_counter() - t < 0.5  # la comprobación de la huella va en segundo plano
    assert _wait(lambda: not dataset._state["refreshing"], 5)
    dataset._state["sync"] = True
