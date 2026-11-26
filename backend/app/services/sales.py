"""Ventas reales (series_summary): KPIs, evolución, canales, ranking, geografía y comparativas.

Todas las agregaciones se ejecutan en Databricks (repository.sales_by / timeline_daily); aquí solo se
combinan filas ya agregadas. Las cifras son ventas y tickets registrados, no estimaciones.
Filtros que aplican: periodo, restaurante, canales, comunidad, provincia, tipología y ubicación
(tipo, score, impacto y prioridad son propios de las anomalías y no afectan a las ventas).
"""
from __future__ import annotations

from typing import Any

from . import repository as repo
from ..database import parallel
from .analytics import DIAS, add_days, daily_rows, day_diff, prev_range, r2, r4, sql_filter
from .dataset import Dataset
from .geo import ccaa_label, is_unknown, provincia_label

SIN_MAESTRO = "Sin dato de maestro"
SALES_SORTS = tuple(repo.SALES_SORT)


def _div(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def _var(cur: float | None, prev: float | None) -> float | None:
    """Variación relativa frente al periodo anterior (fracción), o None si no es comparable."""
    return r4(cur / prev - 1) if cur is not None and prev else None


def _from_daily(daily: list[dict], d_from: str, d_to: str, restaurantes: int) -> dict:
    daily = [d for d in daily if d_from <= d["fecha"] <= d_to]
    ventas = sum(d.get("ventas_totales") or 0 for d in daily)
    tickets = sum(d.get("tickets_totales") or 0 for d in daily)
    filas = sum(d.get("filas") or 0 for d in daily)
    base = sum(d.get("filas_con_baseline") or 0 for d in daily)
    best = max(daily, key=lambda d: d.get("ventas_totales") or 0, default=None)
    dias = len(daily)
    return {
        "from": d_from, "to": d_to,
        "ventas": r2(ventas), "tickets": int(tickets),
        "ticket_medio": r2(ventas / tickets) if tickets else None,
        "dias": dias, "dias_periodo": day_diff(d_from, d_to) + 1,
        "venta_media_dia": r2(ventas / dias) if dias else None,
        "restaurantes": restaurantes if dias else 0,
        "mejor_dia": {"fecha": best["fecha"], "ventas": r2(best.get("ventas_totales") or 0)} if best else None,
        "filas": filas, "filas_con_baseline": base, "_daily": daily,
    }


def _totals(ds: Dataset, f: dict, d_from: str, d_to: str) -> dict:
    """Totales de un periodo arbitrario (comparar periodos): 2 consultas en paralelo."""
    sf = sql_filter(ds, f)
    daily, counts = parallel(lambda: repo.timeline_daily(d_from, d_to, sf),
                             lambda: repo.sales_restaurant_counts(d_from, d_to, sf))
    return _from_daily(daily, d_from, d_to, counts["actual"])


def summary(ds: Dataset, f: dict) -> dict:
    """KPIs de ventas del periodo filtrado, periodo anterior y sparklines.

    2 consultas: la serie diaria de ambos periodos (compartida con el gráfico temporal) y el recuento
    de restaurantes de ambos periodos.
    """
    pr = prev_range(ds, f)
    daily = daily_rows(ds, f)
    counts = repo.sales_restaurant_counts(f["from"], f["to"], sql_filter(ds, f), prev_from=pr and pr["from"])
    cur = _from_daily(daily, f["from"], f["to"], counts["actual"])
    prev = None
    if pr:
        p = _from_daily(daily, pr["from"], pr["to"], counts["anterior"])
        p.pop("_daily")
        prev = p if p["dias"] else None
    daily = cur.pop("_daily")
    length = cur["dias_periodo"]
    nb = max(1, min(24, length))
    bw = length / nb
    sv, st = [0.0] * nb, [0] * nb
    for d in daily:
        k = min(nb - 1, int(day_diff(f["from"], d["fecha"]) / bw))
        sv[k] += d.get("ventas_totales") or 0
        st[k] += d.get("tickets_totales") or 0
    cur["variacion"] = {k: _var(cur.get(k), (prev or {}).get(k))
                        for k in ("ventas", "tickets", "ticket_medio", "venta_media_dia", "restaurantes")}
    cur["baseline"] = ("pendiente" if not cur["filas_con_baseline"]
                       else "completo" if cur["filas_con_baseline"] >= cur["filas"] else "parcial")
    return {**cur, "prev": prev, "prevRange": pr,
            "spark": {"ventas": [r2(v) for v in sv], "tickets": st,
                      "ticket_medio": [r2(v / t) if t else 0 for v, t in zip(sv, st)]}}


def _row(r: dict) -> dict:
    ventas, tickets, vp, tp = r.get("ventas"), r.get("tickets"), r.get("ventas_prev"), r.get("tickets_prev")
    return {
        "ventas": r2(ventas or 0), "tickets": int(tickets or 0), "ticket_medio": r2(_div(ventas, tickets)) if tickets else None,
        "restaurantes": int(r.get("restaurantes") or 0), "dias": int(r.get("dias") or 0),
        "ventas_prev": r2(vp) if vp is not None else None, "tickets_prev": int(tp) if tp is not None else None,
        "variacion": _var(ventas, vp), "variacion_tickets": _var(tickets, tp),
    }


def by_channel(ds: Dataset, f: dict) -> list[dict]:
    pr = prev_range(ds, f)
    rows = repo.sales_by("canal", f["from"], f["to"], sql_filter(ds, f), prev_from=pr and pr["from"], limit=10)
    total = sum(r.get("ventas") or 0 for r in rows)
    by = {r["clave"]: r for r in rows}
    out = []
    for c in repo.CHANNELS:
        if c in by:
            x = _row(by[c])
            out.append({"canal": c, **x, "share": r4((by[c].get("ventas") or 0) / total) if total else 0})
    return out


def ranking(ds: Dataset, f: dict, sort: str = "ventas", limit: int = 15) -> dict:
    """Ranking de restaurantes por ventas, tickets, ticket medio o variación frente al periodo anterior."""
    sort = sort if sort in SALES_SORTS else "ventas"
    pr = prev_range(ds, f)
    sf = sql_filter(ds, f)
    rows = repo.sales_by("restaurante", f["from"], f["to"], sf, prev_from=pr and pr["from"], sort=sort,
                         limit=max(1, min(200, int(limit))))
    out = []
    for r in rows:
        rest = ds.rest_by_id.get(r["clave"]) or {}
        out.append({"restaurante_id": r["clave"], "provincia": rest.get("provincia"), "ccaa": rest.get("ccaa"),
                    "tipologia": rest.get("tipologia"), **_row(r)})
    return {"total": int(rows[0].get("total_grupos") or 0) if rows else 0, "sort": sort, "prevRange": pr, "rows": out}


def _label(dimension: str, key: Any) -> str:
    if dimension == "provincia":
        return SIN_MAESTRO if is_unknown(key) else provincia_label(key)
    if dimension == "comunidad":
        return SIN_MAESTRO if is_unknown(key) else ccaa_label(key)
    if dimension == "dia_semana":
        try:
            return DIAS[int(key)]
        except (TypeError, ValueError, IndexError):
            return str(key)
    if dimension in ("tipologia", "ubicacion"):
        return "Sin dato" if is_unknown(key) else str(key)
    return str(key)


BREAKDOWN_DIMS = tuple(d for d in repo.SALES_DIMS if d != "restaurante")


def breakdown(ds: Dataset, f: dict, dimension: str, sort: str = "ventas", limit: int = 60) -> list[dict]:
    if dimension not in BREAKDOWN_DIMS:
        raise ValueError(f"Dimensión no soportada: {dimension}")
    pr = prev_range(ds, f)
    order = "clave" if dimension in ("mes", "dia_semana") else sort
    rows = repo.sales_by(dimension, f["from"], f["to"], sql_filter(ds, f), prev_from=pr and pr["from"], sort=order,
                         limit=limit)
    total = sum(r.get("ventas") or 0 for r in rows)
    return [{"clave": _label(dimension, r["clave"]), **_row(r),
             "share": r4((r.get("ventas") or 0) / total) if total else 0} for r in rows]


_SUM_KEYS = ("ventas", "tickets", "restaurantes", "dias", "filas", "ventas_prev", "tickets_prev", "dias_prev")


def geo_both(ds: Dataset, f: dict) -> dict:
    """Ventas por provincia y por comunidad con nombres legibles (los que usa el mapa), en UNA consulta."""
    pr = prev_range(ds, f)
    rows = repo.sales_geo(f["from"], f["to"], sql_filter(ds, f), prev_from=pr and pr["from"])

    def build(groups: dict) -> list[dict]:
        total = sum(r.get("ventas") or 0 for r in groups.values())
        out = [{"nombre": k, **_row(r), "share": r4((r.get("ventas") or 0) / total) if total else 0, **(
            {"ccaa": r["_ccaa"]} if "_ccaa" in r else {})} for k, r in groups.items()]
        return sorted(out, key=lambda g: -g["ventas"])

    def add(groups: dict, k: str, r: dict, extra: dict | None = None) -> None:
        g = groups.setdefault(k, {**{x: None for x in _SUM_KEYS}, **(extra or {})})
        for x in _SUM_KEYS:
            if r.get(x) is not None:
                g[x] = (g[x] or 0) + r[x]

    prov: dict = {}
    ccaa: dict = {}
    for r in rows:
        c = _label("comunidad", r["ccaa"])
        add(prov, _label("provincia", r["clave"]), r, {"_ccaa": None if c == SIN_MAESTRO else c})
        add(ccaa, c, r)
    return {"provincia": build(prov), "ccaa": build(ccaa)}


def by_geo(ds: Dataset, f: dict, level: str = "provincia") -> list[dict]:
    return geo_both(ds, f)["provincia" if level == "provincia" else "ccaa"]


def compare_restaurants(ds: Dataset, f: dict, ids: list[str]) -> dict:
    """Comparativa de 2 a 10 restaurantes en el mismo periodo, con su periodo anterior y anomalías."""
    from .analytics import filter_anomalies, stats

    ids = [i for i in dict.fromkeys(ids) if i in ds.rest_by_id][:10]
    if not ids:
        return {"rows": [], "prevRange": None}
    pr = prev_range(ds, f)
    sf = sql_filter(ds, {**f, "restaurante": ""})
    sf.restaurantes = ids
    rows = {r["clave"]: r for r in repo.sales_by("restaurante", f["from"], f["to"], sf, prev_from=pr and pr["from"],
                                                 limit=len(ids))}
    out = []
    for rid in ids:
        rest = ds.rest_by_id[rid]
        an = stats(filter_anomalies(ds, {**f, "restaurante": rid}))
        base = _row(rows[rid]) if rid in rows else None
        out.append({"restaurante_id": rid, "provincia": rest.get("provincia"), "ccaa": rest.get("ccaa"),
                    "tipologia": rest.get("tipologia"), "ventas_periodo": base,
                    "anomalias": {k: an[k] for k in ("total", "caidas", "subidas", "impacto")}})
    return {"rows": out, "prevRange": pr}


def compare_periods(ds: Dataset, f: dict, pa: dict, pb: dict) -> dict:
    def side(p: dict) -> dict:
        t = _totals(ds, f, p["from"], p["to"])
        t.pop("_daily")
        rows = repo.sales_by("canal", p["from"], p["to"], sql_filter(ds, f), limit=10)  # noqa: E501
        t["canales"] = [{"canal": r["clave"], "ventas": r2(r.get("ventas") or 0), "tickets": int(r.get("tickets") or 0)}
                        for r in rows]
        return t

    a, b = side(pa), side(pb)
    return {"a": a, "b": b, "variacion": {k: _var(b.get(k), a.get(k)) for k in ("ventas", "tickets", "ticket_medio",
                                                                                "venta_media_dia")}}
