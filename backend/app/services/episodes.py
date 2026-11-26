"""Episodios multi-día (v_alert_episodes) y series temporales completas (series_summary)."""
from __future__ import annotations

from . import repository as repo
from .analytics import add_days, compact, decomposition, r2, r4, _other_channels, _serie_point
from .dataset import PRIORIDAD, PRIORIDAD_INV, Dataset, frac, public_restaurant
from .geo import ccaa_label, provincia_label


def _episode_row(row: dict) -> dict:
    return {
        **row,
        "id": row["episode_id"],
        "provincia": provincia_label(row.get("provincia")),
        "ccaa": ccaa_label(row.get("comunidad_autonoma")),
        "prioridad": PRIORIDAD.get(row.get("prioridad_nivel") or "", "baja"),
        "desviacion_media_ventas_pct": frac(row.get("desviacion_media_ventas_pct")),
        "desviacion_media_tickets_pct": frac(row.get("desviacion_media_tickets_pct")),
    }


def list_episodes(ds: Dataset, f: dict, *, sort: str = "impacto", direction: str = "desc", page: int = 1,
                  size: int = 25, min_dias: int = 1) -> dict:
    size = max(1, min(200, int(size or 25)))
    page = max(1, int(page or 1))
    total, rows = repo.episodes(
        date_from=f["from"], date_to=f["to"], restaurante=f["restaurante"], canales=f["canales"], tipo=f["tipo"],
        prioridad_nivel=PRIORIDAD_INV.get(f["prioridad"], ""), ccaa_slug=ds.ccaa_slug.get(f["ccaa"], ""),
        provincia_slug=ds.prov_slug.get(f["provincia"], ""), tipologia=f.get("tipologia", ""),
        ubicacion=f.get("ubicacion", ""), min_dias=min_dias, sort=sort, direction=direction, limit=size,
        offset=(page - 1) * size,
    )
    pages = max(1, -(-total // size))
    return {"total": total, "page": page, "pages": pages, "size": size, "rows": [_episode_row(r) for r in rows]}


def episode_detail(ds: Dataset, episode_id: str) -> dict | None:
    row = repo.episode(episode_id)
    if not row:
        return None
    ep = _episode_row(row)
    days = sorted((a for a in ds.anomalies if a.get("episode_id") == episode_id), key=lambda a: a["fecha"])
    if not days:
        # La ejecución activa no contiene el episodio: se leen sus filas directamente.
        from .dataset import to_anomaly

        days = [to_anomaly(a, ds.rest_by_id.get(a["restaurante_id"])) for a in repo.alerts_by_episode(episode_id)]
    rid, canal = ep["restaurante_id"], ep["canal"]
    rest = ds.rest_by_id.get(rid) or {"id": rid}
    serie_rows = repo.series_rows(rid, canal, add_days(ep["fecha_inicio"], -56), add_days(ep["fecha_fin"], 28))
    serie = [_serie_point(x, ds) for x in serie_rows]

    # Descomposición agregada del episodio (volumen de tickets vs ticket medio), en €.
    per_day = [{"fecha": a["fecha"], **decomposition(a)} for a in days]
    eff_t = sum(d["efecto_tickets_eur"] or 0 for d in per_day)
    eff_tm = sum(d["efecto_ticket_medio_eur"] or 0 for d in per_day)
    tot = abs(eff_t) + abs(eff_tm)
    desc = {
        "efecto_tickets_eur": r2(eff_t), "efecto_ticket_medio_eur": r2(eff_tm),
        "contribucion_tickets_pct": r4(abs(eff_t) / tot) if tot else None,
        "contribucion_tm_pct": r4(abs(eff_tm) / tot) if tot else None,
        "por_dia": per_day,
    }
    # Pares y canales: lo que hizo el resto de la zona y los otros canales cada día del episodio.
    pares, canales = [], []
    for a in days:
        peer = repo.peers_day(a["fecha"], canal, rest.get("_ccaa_slug"), rid)
        pares.append({
            "fecha": a["fecha"], "z_peer_diff": a.get("z_peer_diff"),
            "peer_delta_pct": a.get("cohorte"),
            "restaurantes_comparables": (peer or {}).get("restaurantes"),
            "movimiento_zona_pct": r4(peer["ventas"] / peer["esperadas"] - 1) if peer and peer.get("esperadas") else None,
            "comparables_con_anomalia": (peer or {}).get("con_anomalia"),
        })
        canales.append({"fecha": a["fecha"], "z_cross_channel": a.get("z_cross_channel"),
                        "otros_canales_normales": a.get("otros_canales_normales"),
                        "otros_canales": a.get("otros_canales") or _other_channels(repo.series_day(rid, a["fecha"]), canal)})
    return {
        **ep,
        "restaurante": public_restaurant(rest) if "maestro" in rest else rest,
        "dias": [compact(a) | {"evidencia": a.get("evidencia"), "recomendacion_revision": a.get("recomendacion_revision"),
                               "explicacion_negocio": a.get("explicacion_negocio")} for a in days],
        "descomposicion": desc,
        "pares": pares,
        "canales": canales,
        "serie": serie,
    }


def series(ds: Dataset, rid: str, canal: str | None, date_from: str, date_to: str) -> dict:
    rows = repo.series_rows(rid, canal, date_from, date_to)
    by_canal: dict[str, list[dict]] = {}
    for x in rows:
        by_canal.setdefault(x["canal"], []).append(_serie_point(x, ds))
    out = {"restaurante_id": rid, "from": date_from, "to": date_to, "canales": list(by_canal), "series": by_canal}
    if not canal:
        # Total del restaurante (suma de canales). Las bandas P05/P95 no son sumables: no se incluyen.
        tot: dict[str, dict] = {}
        for pts in by_canal.values():
            for p in pts:
                t = tot.setdefault(p["fecha"], {"fecha": p["fecha"], "ventas": 0.0, "tickets": 0, "esperadas": 0.0,
                                                "esperadas_completas": True, "anomalias": []})
                t["ventas"] += p["ventas"] or 0
                t["tickets"] += p["tickets"] or 0
                if p["esperadas"] is None:
                    t["esperadas_completas"] = False
                else:
                    t["esperadas"] += p["esperadas"]
                if p.get("anomalia"):
                    t["anomalias"].append({"id": p["anomalia_id"], "tipo": p["anomalia"]})
        out["total"] = [{**t, "ventas": r2(t["ventas"]), "esperadas": r2(t["esperadas"]) if t["esperadas_completas"] else None}
                        for t in sorted(tot.values(), key=lambda t: t["fecha"])]
    return out
