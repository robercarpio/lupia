"""Contrato de la API con el frontend y reglas del copiloto."""
from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

import pytest

ALL = quote(json.dumps({"from": "2026-06-01", "to": "2026-09-30"}))


def f(**kw):
    return quote(json.dumps({"from": "2026-06-01", "to": "2026-09-30", **kw}))


def test_meta_contract(client):
    m = client.get("/api/meta").json()
    for k in ("source", "dateMin", "dateMax", "evalStart", "restaurantes", "sin_maestro", "ccaas", "provinces", "channels",
              "totalAnomalias", "filas", "config", "reasonCounts", "restaurantIds", "defaultRange"):
        assert k in m, k
    assert m["dateMax"] == "2026-09-30"
    assert m["defaultRange"] == {"from": "2026-07-03", "to": "2026-09-30"}
    assert m["sin_maestro"] == 1
    assert "País Vasco" in m["ccaas"] and {"provincia": "Bizkaia", "ccaa": "País Vasco"} in m["provinces"]
    assert m["config"]["zThreshold"] == 4.0  # params_json del run activo (snake_case → camelCase)
    assert m["totalAnomalias"] == 4


def test_health(client):
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["data"]["dateMax"] == "2026-09-30"
    assert h["ai"]["configured"] is False


def test_dashboard_contract(client):
    d = client.get(f"/api/dashboard?f={ALL}").json()
    k = d["kpis"]
    assert k["total"] == 4 and k["caidas"] == 3 and k["subidas"] == 1 and k["restaurantes"] == 2 and k["altas"] == 3
    assert len(k["spark"]["total"]) == 24 and sum(k["spark"]["total"]) == 4
    assert d["timeline"]["step"] == 1 and len(d["timeline"]["buckets"]) == 122
    b = next(b for b in d["timeline"]["buckets"] if b["from"] == "2026-09-01")
    assert b["caidas"] == 1 and b["top"][0]["id"] == "R001-sala-2026-09-01" and b["esperadas"] > b["ventas"] * 0
    assert [c["canal"] for c in d["channels"]] == ["sala", "domicilio", "autoking"]
    provs = {g["nombre"] for g in d["geo"]["provincia"]}
    assert provs == {"Sevilla", "Bizkaia"}  # nombres legibles, no slugs


def test_filters_by_display_name_and_slug(client):
    a = client.get(f"/api/anomalies?f={f(ccaa='País Vasco')}").json()
    b = client.get("/api/anomalies?from=2026-06-01&to=2026-09-30&ccaa=pais-vasco").json()
    assert a["total"] == b["total"] == 1 and a["rows"][0]["restaurante_id"] == "R002"
    assert client.get(f"/api/anomalies?f={f(prioridad='alta')}").json()["total"] == 3
    assert client.get("/api/anomalies?from=2026-06-01&to=2026-09-30&prioridad=P3").json()["total"] == 1
    assert client.get("/api/anomalies?from=2026-06-01&to=2026-09-30&canal=domicilio&canal=sala").json()["total"] == 4
    assert client.get("/api/anomalies?from=2026-06-01&to=2026-09-30&tipologia=fs").json()["total"] == 3
    assert client.get(f"/api/anomalies?f={f(restaurante='2')}").json()["total"] == 1  # «2» → R002


def test_table_sort_and_pagination(client):
    t = client.get(f"/api/anomalies?f={ALL}&sort=fecha&dir=asc&size=2&page=2").json()
    assert t["total"] == 4 and t["pages"] == 2 and t["page"] == 2
    assert [r["fecha"] for r in t["rows"]] == ["2026-09-03", "2026-09-10"]
    r = t["rows"][0]
    assert -0.6 < r["desviacion_pct"] < -0.5  # puntos porcentuales → fracción
    assert r["prioridad"] == "alta" and r["racha"] == 3


def test_anomaly_detail(client):
    d = client.get("/api/anomalies/R001-sala-2026-09-02").json()
    assert d["id"] == "R001-sala-2026-09-02" and d["restaurante"]["provincia"] == "Sevilla"
    assert d["serie"] and any(p.get("anomalia") == "caida" for p in d["serie"])
    assert all("ventas_p05" in p and "motivo" in p for p in d["serie"])
    assert len(d["referencia"]) == 8
    assert {o["canal"] for o in d["otros_canales"]} == {"autoking", "domicilio"}
    for k in ("resumen", "hechos", "contexto", "hipotesis", "recomendacion"):
        assert d["detalle"][k]
    assert d["historial"]["total"] == 2 and d["variabilidad_habitual"]["ventas"] > 0
    assert d["descomposicion"]["contribucion_tickets_pct"] == 0.96
    assert client.get("/api/anomalies/R001-sala-2026-01-01").status_code == 404


def test_no_external_causes_in_narrative(client):
    txt = json.dumps(client.get("/api/anomalies/R001-sala-2026-09-02").json()["detalle"], ensure_ascii=False).lower()
    for palabra in ("obra", "avería", "averia", "meteorolog", "lluvia", "huelga", "concierto", "partido", "repartidor"):
        assert palabra not in txt, palabra


def test_ranking_and_restaurants(client):
    r = client.get(f"/api/restaurants?f={ALL}&sort=impacto&limit=5").json()
    assert r["total"] == 2 and r["rows"][0]["restaurante_id"] == "R001" and r["rows"][0]["canal_principal"] == "sala"
    cat = client.get("/api/restaurants/catalog").json()
    assert cat["total"] == 4 and next(x for x in cat["rows"] if x["id"] == "R004")["provincia"] is None
    info = client.get("/api/restaurants/r1").json()
    assert info["id"] == "R001" and info["anomalias"]["total"] == 3
    assert client.get("/api/restaurants/R999").status_code == 404


def test_episodes(client):
    e = client.get(f"/api/episodes?f={ALL}").json()
    assert e["total"] == 2 and e["rows"][0]["prioridad"] in ("alta", "baja")
    d = client.get("/api/episodes/EP-R001-sala-20260901").json()
    assert d["duracion_dias"] == 3 and len(d["dias"]) == 3 and len(d["pares"]) == 3
    assert d["descomposicion"]["efecto_tickets_eur"] < 0 and d["serie"]
    assert client.get("/api/episodes/NOPE").status_code == 404


def test_series(client):
    s = client.get("/api/series?restaurante=R001&canal=sala&from=2026-08-01&to=2026-09-30").json()
    pts = s["series"]["sala"]
    assert len(pts) == 61 and any(p.get("festivo") for p in pts) and any(p.get("anomalia") for p in pts)
    tot = client.get("/api/series?restaurante=R001&from=2026-09-01&to=2026-09-05").json()["total"]
    assert len(tot) == 5 and tot[0]["anomalias"]
    assert client.get("/api/series?restaurante=R001&canal=xx").status_code == 400


def test_kpis_and_runs(client):
    k = client.get("/api/kpis").json()
    assert k["global"]["total_episodios"] == 2 and k["model_run"]["run_id"] == "run-1"
    r = client.get("/api/model-runs").json()
    assert r["active"]["params_json"]["z_threshold"] == 4.0


def test_filter_options(client):
    o = client.get("/api/filters").json()
    assert o["canales"] == ["sala", "domicilio", "autoking"] and "fs" in o["tipologias"] and "R004" in o["restaurantes"]


def test_csv_exports(client):
    r = client.get("/api/anomalias.csv")
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert list(rows[0].keys())[:5] == ["restaurante_id", "canal", "fecha", "tipo", "score"]
    assert rows[0]["score"] == "0.970000" and len(rows) == 4
    r2 = client.get(f"/api/export.csv?f={f(tipo='subida')}")
    assert len(list(csv.DictReader(io.StringIO(r2.text)))) == 1


def test_cache_clear(client):
    assert client.post("/api/cache/clear").json()["ok"] is True


def test_errors_are_json(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]
    r = client.get("/api/breakdown/zzz")
    assert r.status_code == 400 and "Dimensión" in r.json()["error"]
    r = client.post("/api/chat", json={})
    assert r.status_code == 400 and r.json()["error"]


def test_warehouse_starting_is_503(client, monkeypatch):
    from app.database import WarehouseUnavailable, clear_cache
    from app.services import repository as repo

    def boom():
        raise WarehouseUnavailable("El SQL Warehouse está arrancando (estado: STARTING).")

    from app.services import dataset

    clear_cache()
    dataset.reset_state()
    monkeypatch.setattr(repo, "series_overview", boom)
    r = client.get("/api/meta")
    assert r.status_code == 503 and r.json()["code"] == "WAREHOUSE_STARTING"


def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for chunk in text.strip().split("\n\n"):
        ev = next(l[7:] for l in chunk.split("\n") if l.startswith("event: "))
        data = json.loads(next(l[6:] for l in chunk.split("\n") if l.startswith("data: ")))
        out.append((ev, data))
    return out


def test_chat_sse_fallback_summary(client):
    r = client.post("/api/chat", json={"message": "¿Qué ha pasado?", "dashboardFilters": {"from": "2026-06-01", "to": "2026-09-30"}})
    assert r.headers["content-type"].startswith("text/event-stream")
    ev = _sse(r.text)
    names = [e for e, _ in ev]
    assert names[0] == "session" and names[-1] == "done" and "block" in names and "text" in names
    blocks = [d["block"]["type"] for e, d in ev if e == "block"]
    assert blocks == ["kpis", "anomalies"]


def test_chat_sse_fallback_anomaly(client):
    ev = _sse(client.post("/api/chat", json={"message": "Explícame la anomalía R001-sala-2026-09-02"}).text)
    assert ("focus", {"id": "R001-sala-2026-09-02"}) in ev
    blk = next(d["block"] for e, d in ev if e == "block")
    assert blk["type"] == "anomaly" and blk["serie"] and blk["detalle"]["hechos"]


def test_copilot_json_fallback(client):
    r = client.post("/api/copilot/chat", json={"question": "¿Qué pasó?", "episode_id": "EP-R001-sala-20260901"}).json()
    assert r["source"] == "fallback" and "EP-R001-sala-20260901" in r["answer"] and r["context"]["tipo"] == "episodio"
    r = client.post("/api/copilot/chat", json={"question": "Resumen", "restaurante_id": "R002"}).json()
    assert r["context"]["tipo"] == "restaurante" and "R002" in r["answer"]
    r = client.post("/api/copilot/chat", json={"question": "x", "anomaly_id": "R001-sala-2026-09-01"}).json()
    assert r["context"]["tipo"] == "alerta"
    assert client.post("/api/copilot/chat", json={"question": "x", "episode_id": "NOPE"}).status_code == 404


def test_copilot_tools_cover_azure_loop(monkeypatch, client):
    """Simula Azure OpenAI con streaming + tool calling para validar el bucle completo."""
    from types import SimpleNamespace as NS

    from app.config import get_settings
    from app.services import copilot

    get_settings.cache_clear()
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-test")
    calls = {"n": 0}

    def chunk(content=None, tool=None):
        tcs = [NS(index=0, id="call_1", function=NS(name=tool[0], arguments=tool[1]))] if tool else None
        return NS(choices=[NS(delta=NS(content=content, tool_calls=tcs), finish_reason=None)])

    class Completions:
        def create(self, **kw):
            calls["n"] += 1
            assert kw["messages"][0]["role"] == "system" and "PROHIBIDO" in kw["messages"][0]["content"]
            if calls["n"] == 1:
                return iter([chunk(tool=("listar_anomalias", json.dumps({"filtros": {"desde": "2026-06-01"}, "mostrar": True})))])
            assert kw["messages"][-1]["role"] == "tool"
            return iter([chunk("Hay 4 alertas."), chunk(" Revisa R001.")])

    monkeypatch.setattr(copilot, "_client", NS(chat=NS(completions=Completions())))
    try:
        ev = _sse(client.post("/api/chat", json={"message": "lista anomalías"}).text)
    finally:
        get_settings.cache_clear()
    text = "".join(d["delta"] for e, d in ev if e == "text" and not d.get("soft"))
    assert text == "Hay 4 alertas. Revisa R001."
    assert any(e == "block" and d["block"]["type"] == "anomalies" for e, d in ev)
    assert any(e == "status" for e, _ in ev) and ev[-1][0] == "done"


@pytest.mark.parametrize("path", ["/api/dashboard", "/api/anomalies", "/api/restaurants", "/api/episodes", "/api/timeline"])
def test_bad_filters_never_crash(client, path):
    r = client.get(f"{path}?f=%7Bnot-json&from=2099-01-01&canal=zzz&prioridad=xx&ccaa=Atlantis")
    assert r.status_code == 200
