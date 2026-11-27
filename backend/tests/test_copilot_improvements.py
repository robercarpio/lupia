"""Tests para normalización de IDs, get_restaurant_window, canales activos y contexto curado."""
from __future__ import annotations

from app.services import analytics as Q
from app.services import context_kb
from app.services import copilot
from app.services.dataset import get_dataset


def test_normalize_restaurant_id_variants():
    for raw in ("57", "R57", "r57", "R057", "R-57", "R 57", "R057/R57", "tienda 57", "tienda R57", "RS57"):
        assert Q.normalize_restaurant_id(raw) == "R057", raw
    assert Q.normalize_restaurant_id("R999") == "R999"
    assert Q.normalize_restaurant_id("2") == "R002"


def test_invalid_restaurant_never_leaks_network_data(client):
    ds = get_dataset()
    out = copilot.run_tool(ds, "listar_anomalias", {"filtros": {"restaurante": "R999"}, "mostrar": True}, {})
    assert "error" in out["data"]
    assert not out.get("block")


def test_turn_target_restaurant_prevents_network_widget_leak(client):
    ds = get_dataset()
    session = {"_turn_target_restaurant": "R002"}
    out = copilot.run_tool(
        ds,
        "listar_anomalias",
        {"filtros": {"desde": "2026-06-01", "hasta": "2026-09-30"}, "mostrar": True},
        session,
    )
    assert out["data"]["total"] == 1
    assert all(r["restaurante_id"] == "R002" for r in out["data"]["anomalias"])
    assert out["block"]["filters"]["restaurante"] == "R002"


def test_get_restaurant_window_spine_sin_registro_and_single_channel(client, monkeypatch):
    from app.services import repository as repo

    ds = get_dataset()
    orig_series_rows = repo.series_rows

    # R004 en conftest tiene tiene_autoking=False, tiene_domicilio=False (monocanal sala);
    # simulamos que 2026-09-01 no tuvo registro de ventas (SIN_REGISTRO).
    monkeypatch.setattr(
        repo,
        "series_rows",
        lambda rid, canal, d_from, d_to: [
            r for r in orig_series_rows(rid, canal, d_from, d_to)
            if not (rid == "R004" and r["fecha"] == "2026-09-01")
        ],
    )
    win = Q.restaurant_window(ds, "R4", "2026-08-30", "2026-09-04")
    assert win["restaurante"]["id"] == "R004"
    assert win["restaurante"]["canales_activos"] == ["sala"]
    assert win["restaurante"]["tiene_otros_canales"] is False
    assert len(win["ventana_diaria"]) == 6
    gap_row = next(r for r in win["ventana_diaria"] if r["fecha"] == "2026-09-01")
    assert gap_row["estado"] == "SIN_REGISTRO" and gap_row["exclusion_reason"] == "SIN_REGISTRO"
    assert [d["fecha"] for d in win["dias_sin_registro"]] == ["2026-09-01"]
    assert any(p["id"] == "tienda_monocanal_interpretacion" for p in win["playbooks_relevantes"])

    # R001 con alertas en 2026-09-01..03 a través de run_tool
    out = copilot.run_tool(
        ds,
        "get_restaurant_window",
        {"restaurante_id": "1", "fecha_inicio": "2026-08-29", "fecha_fin": "2026-09-06", "mostrar": True},
        {},
    )
    assert out["data"]["restaurante"]["id"] == "R001"
    assert out["data"]["resumen_ventana"]["total_alertas"] == 3
    assert out["block"]["type"] == "timeline"
    assert out["block"]["filters"]["restaurante"] == "R001"


def test_single_channel_store_never_reports_other_channels_failed(client):
    from app.services.dataset import to_anomaly

    rest_mono = {"id": "R057", "provincia": "Barcelona", "ccaa": "Cataluña", "autoking": "no", "domicilio": "no"}
    anom = to_anomaly(
        {
            "restaurante_id": "R057",
            "canal": "sala",
            "fecha": "2025-03-28",
            "tipo": "caida",
            "score": 0.9999,
            "ventas_reales": 7.5,
            "ventas_esperadas": 10380.84,
            "tickets_reales": 1,
            "tickets_esperados": 953.55,
            "otros_canales_normales": False,
        },
        rest_mono,
    )
    assert anom["tiene_otros_canales"] is False
    assert anom["otros_canales_normales"] is None
    assert anom["estado_otros_canales"] == "sin_otros_canales_activos"
    assert "también se desvían otros canales" not in anom["explicacion"]


def test_curated_context_kb_offline():
    res = context_kb.query_context(date_from="2025-04-20", date_to="2025-05-02")
    assert any(e["id"] == "apagon_peninsular_2025_04_28" for e in res["eventos_macro_espana"])
    assert len(res["playbooks_qsr"]) >= 6

    dana = context_kb.macro_events_in_window("2024-10-25", "2024-11-05", ccaa="Comunidad Valenciana")
    assert any(e["id"] == "dana_valencia_2024" for e in dana)
