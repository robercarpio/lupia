"""Contexto operativo curado (playbooks QSR y eventos macro verificados 2023-2026).

Fuente estática local (JSON): latencia cero, cero búsqueda web en vivo y cero riesgo de alucinación.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_ASSET_PATH = Path(__file__).resolve().parent.parent / "assets" / "qsr_context.json"


@lru_cache(maxsize=1)
def load_context() -> dict[str, Any]:
    with _ASSET_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def macro_events_in_window(
    date_from: str | None = None,
    date_to: str | None = None,
    ccaa: str | None = None,
) -> list[dict[str, Any]]:
    """Devuelve los eventos macro verificados que solapan con [date_from, date_to]."""
    ctx = load_context()
    out: list[dict[str, Any]] = []
    ccaa_l = (ccaa or "").strip().lower()
    for ev in ctx.get("eventos_macro_espana", []):
        if ev["id"] == "fechas_especiales_navidad_reyes":
            # Solo se incluye si la ventana toca diciembre o enero.
            if date_from and date_to:
                m1, m2 = int(date_from[5:7]), int(date_to[5:7])
                if not (m1 in (12, 1) or m2 in (12, 1) or (date_from[:4] != date_to[:4])):
                    continue
        else:
            if date_to and ev["fecha_inicio"] > date_to:
                continue
            if date_from and ev["fecha_fin"] < date_from:
                continue
        if ccaa_l:
            afectadas = [x.lower() for x in ev.get("ccaa_afectadas", [])]
            if "todas" not in afectadas and ccaa_l not in afectadas:
                continue
        out.append(ev)
    return out


def match_playbooks(
    *,
    alerts: list[dict[str, Any]] | None = None,
    active_channels: list[str] | None = None,
    missing_days_count: int = 0,
    excluded_days_count: int = 0,
) -> list[dict[str, Any]]:
    """Selecciona los playbooks QSR relevantes a partir de los patrones observados en la ventana."""
    ctx = load_context()
    by_id = {p["id"]: p for p in ctx.get("playbooks_qsr", [])}
    picked: list[str] = []
    alerts = alerts or []
    active_channels = active_channels or []

    if len(active_channels) == 1:
        picked.append("tienda_monocanal_interpretacion")

    if any(
        a.get("tipo") == "caida"
        and ((a.get("tickets") is not None and a["tickets"] <= 5) or (a.get("desviacion_pct") is not None and a["desviacion_pct"] <= -0.92))
        for a in alerts
    ) and (missing_days_count > 0 or any((a.get("tickets") or 999) <= 4 for a in alerts)):
        picked.append("cierre_temporal_tickets_residuales")

    if len(alerts) >= 2 and (missing_days_count > 0 or excluded_days_count > 0):
        picked.append("episodio_sostenido_con_huecos")

    alert_channels = {a.get("canal") for a in alerts if a.get("canal")}
    if len(alert_channels) > 1 or any((a.get("evidencia") or {}).get("incident_multicanal") for a in alerts):
        picked.append("incidente_multicanal_local")

    if any(a.get("canal") == "domicilio" and a.get("tipo") == "caida" and a.get("otros_canales_normales") is True for a in alerts):
        picked.append("caida_aislada_domicilio_agregadores")

    if any(a.get("canal") == "autoking" and a.get("tipo") == "caida" and a.get("otros_canales_normales") is True for a in alerts):
        picked.append("caida_aislada_autoking")

    if any(a.get("metrica") == "tickets" for a in alerts):
        picked.append("anomalia_pura_tickets_tpv")

    if any(a.get("tipo") == "subida" for a in alerts):
        picked.append("subida_evento_local_no_calendario")

    return [by_id[pid] for pid in dict.fromkeys(picked) if pid in by_id]


def query_context(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    patron: str | None = None,
    ccaa: str | None = None,
) -> dict[str, Any]:
    """Consulta el conocimiento operativo curado (sin búsqueda web)."""
    ctx = load_context()
    playbooks = ctx.get("playbooks_qsr", [])
    if patron:
        q = patron.strip().lower()
        filtered = [
            p for p in playbooks
            if q in p["id"].lower() or q in p["titulo"].lower() or q in p["patron_datos"].lower()
        ]
        if filtered:
            playbooks = filtered
    return {
        "nota_uso": ctx.get("nota_uso"),
        "playbooks_qsr": playbooks,
        "eventos_macro_espana": macro_events_in_window(date_from, date_to, ccaa=ccaa),
    }


def prompt_summary() -> str:
    """Resumen breve inyectable en el system prompt."""
    return (
        "## Contexto operativo curado (sin búsqueda web)\n"
        "- Eventos macro verificados en España 2023-2026: incidencia generalizada de domicilio el 2023-11-11; "
        "DANA en Comunidad Valenciana y zonas limítrofes (2024-10-29 a 2024-11-04); apagón eléctrico peninsular "
        "el 2025-04-28; San Juan (24/06) en Cataluña y C. Valenciana; fechas especiales Navidad/Reyes (24-25/12, 31/12, 01/01, 05-06/01).\n"
        "- Playbooks QSR de revisión (siempre como hipótesis/comprobaciones a confirmar con el local, nunca como causa demostrada): "
        "1) cierre temporal / incidencia de registro si hay 1-4 tickets residuales y días SIN_REGISTRO adyacentes; "
        "2) caída aislada de domicilio/agregadores si solo cae domicilio y los otros canales activos están normales; "
        "3) anomalía pura de tickets (revisión de cupones/división/registro) si tickets se disparan o hunden sin mover igual ventas; "
        "4) evento local o duplicidad si hay subida aislada frente a locales comparables; "
        "5) local monocanal: si el restaurante solo opera sala, NO afirmes nunca que domicilio o autoking fallaron."
    )
