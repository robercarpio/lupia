"""Capa de consultas compartida por el dashboard (API REST) y el copiloto.

Port fiel de frontend/server/lib/query.mjs: mismas claves JSON y mismas reglas, de modo que el
frontend existente funciona sin cambios. Filtros siempre validados contra listas cerradas.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter, defaultdict
from typing import Any

from . import repository as repo
from .dataset import Dataset, get_dataset, public_restaurant
from .geo import resolve
from .narrative import build_detail, merge_evidence_detail

CHANNELS = list(repo.CHANNELS)
DIAS = ["", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^(R\d{3})-(sala|domicilio|autoking)-(\d{4}-\d{2}-\d{2})$")


# ── Utilidades ────────────────────────────────────────────────────────
def is_iso(s: Any) -> bool:
    if not isinstance(s, str) or not ISO_RE.match(s):
        return False
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def add_days(iso: str, n: int) -> str:
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=n)).isoformat()


def day_diff(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def r2(v: float) -> float:
    return round(v + 0.0, 2)


def r4(v: float) -> float:
    return round(v + 0.0, 4)


def clamp(v: float, a: float, b: float) -> float:
    return min(b, max(a, v))


def _num(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def normalize_restaurant_id(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    m = re.match(r"^R?(\d{1,3})$", s)
    return f"R{m.group(1).zfill(3)}" if m else s


# ── Filtros ───────────────────────────────────────────────────────────
def default_range(ds: Dataset) -> dict:
    return {"from": add_days(ds.date_max, -89), "to": ds.date_max}


def normalize_filters(raw: dict | None, ds: Dataset) -> tuple[dict, list[str]]:
    raw = dict(raw or {})
    warnings: list[str] = []
    eval_start = ds.config["evalStart"]
    d = default_range(ds)
    f_from = raw.get("from") if is_iso(raw.get("from")) else d["from"]
    f_to = raw.get("to") if is_iso(raw.get("to")) else d["to"]
    if raw.get("from") == "all" or raw.get("preset") == "all":
        f_from, f_to = eval_start, ds.date_max
    if f_from < ds.date_min:
        f_from = ds.date_min
    if f_to > ds.date_max:
        if raw.get("to"):
            warnings.append(f"Los datos terminan el {ds.date_max}.")
        f_to = ds.date_max
    if f_from > f_to:
        f_from, f_to = f_to, f_from

    restaurante = ""
    if raw.get("restaurante"):
        rid = normalize_restaurant_id(raw["restaurante"])
        if rid in ds.rest_by_id:
            restaurante = rid
        else:
            warnings.append(f"No existe el restaurante «{raw['restaurante']}».")

    canales_in = raw.get("canales") if isinstance(raw.get("canales"), list) else ([raw["canal"]] if raw.get("canal") else [])
    canales: list[str] = []
    for c in canales_in:
        c = str(c).lower().strip()
        if c in CHANNELS and c not in canales:
            canales.append(c)
    if len(canales) == len(CHANNELS):
        canales = []
    tipo = raw.get("tipo") if raw.get("tipo") in ("caida", "subida") else ""

    prov_names = [p["provincia"] for p in ds.provinces]
    ccaa = ""
    if raw.get("ccaa"):
        ccaa = resolve(raw["ccaa"], ds.ccaas) or ""
        if not ccaa:
            p = resolve(raw["ccaa"], prov_names)
            if p and not raw.get("provincia"):
                raw["provincia"] = p
            else:
                warnings.append(f"No reconozco la comunidad «{raw['ccaa']}».")
    provincia = ""
    if raw.get("provincia"):
        provincia = resolve(raw["provincia"], prov_names) or ""
        if not provincia:
            warnings.append(f"No reconozco la provincia «{raw['provincia']}».")

    prioridad = str(raw.get("prioridad") or "").lower()
    prioridad = {"p1": "alta", "p2": "media", "p3": "baja"}.get(prioridad, prioridad)
    if prioridad not in ("alta", "media", "baja"):
        prioridad = ""
    tipologias = sorted({r["tipologia"] for r in ds.restaurants if r["tipologia"]})
    ubicaciones = sorted({r["ubicacion"] for r in ds.restaurants if r["ubicacion"]})
    tipologia = raw.get("tipologia") if raw.get("tipologia") in tipologias else ""
    ubicacion = raw.get("ubicacion") if raw.get("ubicacion") in ubicaciones else ""
    return {
        "from": f_from, "to": f_to, "restaurante": restaurante, "canales": canales, "tipo": tipo,
        "ccaa": ccaa, "provincia": provincia, "minScore": clamp(_num(raw.get("minScore")), 0, 1),
        "minImpact": max(0.0, _num(raw.get("minImpact"))), "prioridad": prioridad,
        "tipologia": tipologia, "ubicacion": ubicacion,
    }, warnings


def _rest_matches(r: dict, f: dict) -> bool:
    if f.get("restaurante") and r["id"] != f["restaurante"]:
        return False
    if f.get("ccaa") and r["ccaa"] != f["ccaa"]:
        return False
    if f.get("provincia") and r["provincia"] != f["provincia"]:
        return False
    if f.get("tipologia") and r["tipologia"] != f["tipologia"]:
        return False
    if f.get("ubicacion") and r["ubicacion"] != f["ubicacion"]:
        return False
    return True


def _anom_matches(a: dict, f: dict, rng: dict | None = None) -> bool:
    rng = rng or f
    if a["fecha"] < rng["from"] or a["fecha"] > rng["to"]:
        return False
    if f["restaurante"] and a["restaurante_id"] != f["restaurante"]:
        return False
    if f["canales"] and a["canal"] not in f["canales"]:
        return False
    if f["tipo"] and a["tipo"] != f["tipo"]:
        return False
    if f["ccaa"] and a["ccaa"] != f["ccaa"]:
        return False
    if f["provincia"] and a["provincia"] != f["provincia"]:
        return False
    if f.get("tipologia") and a.get("tipologia") != f["tipologia"]:
        return False
    if f.get("ubicacion") and a.get("ubicacion") != f["ubicacion"]:
        return False
    if a["score"] < f["minScore"]:
        return False
    if abs(a["impacto"]) < f["minImpact"]:
        return False
    if f["prioridad"] and a["prioridad"] != f["prioridad"]:
        return False
    return True


def filter_anomalies(ds: Dataset, f: dict, rng: dict | None = None) -> list[dict]:
    return [a for a in ds.anomalies if _anom_matches(a, f, rng)]


def sql_filter(ds: Dataset, f: dict) -> repo.SqlFilter:
    return repo.SqlFilter(
        restaurante=f.get("restaurante") or "",
        ccaa_slug=ds.ccaa_slug.get(f.get("ccaa") or "", ""),
        provincia_slug=ds.prov_slug.get(f.get("provincia") or "", ""),
        canales=list(f.get("canales") or []),
        tipologia=f.get("tipologia") or "",
        ubicacion=f.get("ubicacion") or "",
    )


def prev_range(ds: Dataset, f: dict) -> dict | None:
    """Periodo anterior de igual duración, solo si hay datos completos para él."""
    length = day_diff(f["from"], f["to"]) + 1
    p_from = add_days(f["from"], -length)
    if p_from < ds.date_min:
        return None
    return {"from": p_from, "to": add_days(f["from"], -1)}


def daily_rows(ds: Dataset, f: dict) -> list[dict]:
    """Serie diaria agregada del periodo filtrado Y de su periodo anterior, en una sola consulta (cacheada).

    La comparten los KPIs de ventas (actual vs anterior), las sparklines y el gráfico temporal.
    """
    pr = prev_range(ds, f)
    return repo.timeline_daily(pr["from"] if pr else f["from"], f["to"], sql_filter(ds, f))


COMPACT_KEYS = ("id", "restaurante_id", "fecha", "canal", "tipo", "metrica", "ventas", "ventas_esperadas", "desviacion",
                "desviacion_pct", "tickets", "tickets_esperados", "desviacion_tickets_pct", "ticket_medio", "impacto",
                "score", "z", "prioridad", "provincia", "ccaa", "explicacion", "racha", "episode_id", "priority_score")


def compact(a: dict) -> dict:
    return {k: a.get(k) for k in COMPACT_KEYS}


def stats(lst: list[dict]) -> dict:
    impacto = perdidas = ganancias = score = 0.0
    caidas = subidas = altas = 0
    rest = set()
    for a in lst:
        impacto += a["impacto"]
        if a["impacto"] < 0:
            perdidas += a["impacto"]
        else:
            ganancias += a["impacto"]
        if a["tipo"] == "caida":
            caidas += 1
        else:
            subidas += 1
        if a["prioridad"] == "alta":
            altas += 1
        score += a["score"]
        rest.add(a["restaurante_id"])
    return {
        "total": len(lst), "impacto": r2(impacto), "perdidas": r2(perdidas), "ganancias": r2(ganancias),
        "caidas": caidas, "subidas": subidas, "restaurantes": len(rest),
        "score_medio": r4(score / len(lst)) if lst else 0, "altas": altas,
    }


# ── Dashboard ─────────────────────────────────────────────────────────
def summary(ds: Dataset, f: dict) -> dict:
    lst = filter_anomalies(ds, f)
    cur = stats(lst)
    length = day_diff(f["from"], f["to"]) + 1
    prev = prev_range = None
    prev_from = add_days(f["from"], -length)
    if prev_from >= ds.config["evalStart"]:
        prev_range = {"from": prev_from, "to": add_days(f["from"], -1)}
        prev = stats(filter_anomalies(ds, f, prev_range))
    nb = max(1, min(24, length))
    bw = length / nb
    sp_total, sp_imp, sp_c, sp_s, sp_score = [0] * nb, [0.0] * nb, [0] * nb, [0] * nb, [0.0] * nb
    sp_rest: list[set] = [set() for _ in range(nb)]
    for x in lst:
        k = min(nb - 1, int(day_diff(f["from"], x["fecha"]) / bw))
        sp_total[k] += 1
        sp_imp[k] += abs(x["impacto"])
        (sp_c if x["tipo"] == "caida" else sp_s)[k] += 1
        sp_rest[k].add(x["restaurante_id"])
        sp_score[k] += x["score"]
    spark = {
        "total": sp_total, "impacto": [r2(v) for v in sp_imp], "caidas": sp_c, "subidas": sp_s,
        "restaurantes": [len(s) for s in sp_rest],
        "score": [r4(sp_score[i] / sp_total[i]) if sp_total[i] else 0 for i in range(nb)],
    }
    return {**cur, "prev": prev, "prevRange": prev_range, "spark": spark, "dias": length}


def timeline(ds: Dataset, f: dict) -> dict:
    span = day_diff(f["from"], f["to"]) + 1
    step = 7 if span > 150 else 1
    nb = math.ceil(span / step)
    real, exp, real_all, tick = [0.0] * nb, [0.0] * nb, [0.0] * nb, [0] * nb
    filas = con_base = 0
    for row in daily_rows(ds, f):
        if row["fecha"] < f["from"]:
            continue
        k = day_diff(f["from"], row["fecha"]) // step
        if 0 <= k < nb:
            real_all[k] += row.get("ventas_totales") or 0
            tick[k] += row.get("tickets_totales") or 0
            real[k] += row.get("ventas") or 0
            exp[k] += row.get("esperadas") or 0
            filas += row.get("filas") or 0
            con_base += row.get("filas_con_baseline") or 0
    buckets = []
    for k in range(nb):
        b_from = add_days(f["from"], k * step)
        b_to = min(f["to"], add_days(f["from"], k * step + step - 1))
        buckets.append({"from": b_from, "to": b_to, "ventas": r2(real[k]), "esperadas": r2(exp[k]),
                        "ventas_totales": r2(real_all[k]), "tickets_totales": tick[k],
                        "caidas": 0, "subidas": 0, "impacto": 0.0, "top": []})
    for x in filter_anomalies(ds, f):
        k = day_diff(f["from"], x["fecha"]) // step
        bk = buckets[k]
        bk["caidas" if x["tipo"] == "caida" else "subidas"] += 1
        bk["impacto"] += x["impacto"]
        bk["top"].append(x)
    for bk in buckets:
        bk["impacto"] = r2(bk["impacto"])
        bk["top"] = [
            {k: x[k] for k in ("id", "restaurante_id", "canal", "tipo", "fecha", "impacto", "score", "desviacion_pct")}
            for x in sorted(bk["top"], key=lambda x: -x["score"])[:6]
        ]
    # «ventas»/«esperadas» solo cuentan los días con referencia del modelo; «ventas_totales», todos.
    baseline = "pendiente" if not con_base else "completo" if con_base >= filas else "parcial"
    return {"step": step, "buckets": buckets, "baseline": baseline,
            "cobertura_baseline": r4(con_base / filas) if filas else 0}


def by_channel(ds: Dataset, f: dict) -> list[dict]:
    lst = filter_anomalies(ds, f)
    out = []
    for c in CHANNELS:
        s = stats([a for a in lst if a["canal"] == c])
        out.append({"canal": c, **s, "share": r4(s["total"] / len(lst)) if lst else 0})
    return out


def by_restaurant(ds: Dataset, f: dict, sort: str = "anomalias", limit: int = 15) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in filter_anomalies(ds, f):
        groups[a["restaurante_id"]].append(a)
    out = []
    for rid, lst in groups.items():
        s = stats(lst)
        ch = Counter(a["canal"] for a in lst)
        r = ds.rest_by_id.get(rid) or {}
        out.append({
            "restaurante_id": rid, "provincia": r.get("provincia"), "ccaa": r.get("ccaa"),
            "anomalias": s["total"], "impacto": s["impacto"], "impacto_abs": r2(sum(abs(a["impacto"]) for a in lst)),
            "caidas": s["caidas"], "subidas": s["subidas"], "score_medio": s["score_medio"],
            "canal_principal": ch.most_common(1)[0][0],
            "tipo_predominante": "caida" if s["caidas"] >= s["subidas"] else "subida",
            "ultima": max(a["fecha"] for a in lst),
        })
    keys = {
        "anomalias": lambda x: (x["anomalias"], x["impacto_abs"]),
        "impacto": lambda x: x["impacto_abs"],
        "score": lambda x: x["score_medio"],
    }
    out.sort(key=keys.get(sort, keys["anomalias"]), reverse=True)
    return {"total": len(out), "rows": out[: int(clamp(limit, 1, 200))]}


def by_geo(ds: Dataset, f: dict, level: str = "provincia") -> list[dict]:
    sin = "Sin dato de maestro"
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in filter_anomalies(ds, f):
        groups[(a["ccaa"] if level == "ccaa" else a["provincia"]) or sin].append(a)
    rest_count: Counter = Counter()
    for r in ds.restaurants:
        if not _rest_matches(r, {**f, "restaurante": ""}):
            continue
        rest_count[(r["ccaa"] if level == "ccaa" else r["provincia"]) or sin] += 1
    out = []
    for k, lst in groups.items():
        s = stats(lst)
        out.append({"nombre": k, "ccaa": lst[0]["ccaa"] if level == "provincia" else k, "anomalias": s["total"],
                    "impacto": s["impacto"], "caidas": s["caidas"], "subidas": s["subidas"],
                    "restaurantes_afectados": s["restaurantes"], "restaurantes": rest_count.get(k, 0)})
    return sorted(out, key=lambda x: -x["anomalias"])


BREAKDOWN_DIMS = ("canal", "provincia", "comunidad", "tipo", "dia_semana", "mes", "prioridad", "tipologia", "ubicacion")


def breakdown(ds: Dataset, f: dict, dimension: str) -> list[dict]:
    key_of = {
        "canal": lambda a: a["canal"], "tipo": lambda a: a["tipo"], "provincia": lambda a: a["provincia"] or "Sin dato",
        "comunidad": lambda a: a["ccaa"] or "Sin dato", "dia_semana": lambda a: DIAS[a["dia_semana"]],
        "mes": lambda a: a["fecha"][:7], "prioridad": lambda a: a["prioridad"],
        "tipologia": lambda a: a.get("tipologia") or "Sin dato", "ubicacion": lambda a: a.get("ubicacion") or "Sin dato",
    }.get(dimension)
    if key_of is None:
        raise ValueError(f"Dimensión no soportada: {dimension}")
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in filter_anomalies(ds, f):
        groups[key_of(a)].append(a)
    rows = [{"clave": k, **stats(v)} for k, v in groups.items()]
    if dimension == "mes":
        rows.sort(key=lambda x: x["clave"])
    elif dimension == "dia_semana":
        rows.sort(key=lambda x: DIAS.index(x["clave"]))
    else:
        rows.sort(key=lambda x: -x["total"])
    return rows


TABLE_SORT = {
    "impacto": lambda a: abs(a["impacto"]), "score": lambda a: a["score"],
    "fecha": lambda a: a["fecha"], "desviacion_pct": lambda a: abs(a["desviacion_pct"] or 0),
    "ventas": lambda a: a["ventas"] or 0, "restaurante": lambda a: int(a["restaurante_id"][1:]),
    "prioridad": lambda a: a["priority_score"] or 0,
}


def table(ds: Dataset, f: dict, q: str = "", sort: str = "impacto", direction: str = "desc", page: Any = 1,
          size: Any = 25) -> dict:
    lst = filter_anomalies(ds, f)
    qq = str(q or "").strip().upper()
    if qq:
        lst = [a for a in lst if qq in a["restaurante_id"] or qq in (a["provincia"] or "").upper()]
    key = TABLE_SORT.get(sort, TABLE_SORT["impacto"])
    lst = sorted(lst, key=lambda a: -a["score"])  # desempate estable: mayor score primero
    lst.sort(key=key, reverse=(direction != "asc"))
    size_i = int(clamp(_num(size, 25) or 25, 1, 200))
    pages = max(1, math.ceil(len(lst) / size_i))
    page_i = int(clamp(_num(page, 1) or 1, 1, pages))
    return {"total": len(lst), "page": page_i, "pages": pages, "size": size_i,
            "rows": [compact(a) for a in lst[(page_i - 1) * size_i: page_i * size_i]]}


# ── Detalle ───────────────────────────────────────────────────────────
def _serie_point(row: dict, ds: Dataset) -> dict:
    p = {
        "fecha": row["fecha"], "ventas": row.get("ventas"), "tickets": row.get("tickets"),
        "esperadas": row.get("ventas_esperadas"), "tickets_esperados": row.get("tickets_esperados"),
        "ventas_p05": row.get("ventas_p05"), "ventas_p95": row.get("ventas_p95"),
        "tickets_p05": row.get("tickets_p05"), "tickets_p95": row.get("tickets_p95"),
        "motivo": row.get("motivo") or "normal",
        "festivo": row.get("tipo_festivo") or None, "fecha_especial": row.get("fecha_especial") or None,
        "vispera_festivo": bool(row.get("vispera_festivo")),
    }
    an = ds.anom_by_id.get(f"{row['restaurante_id']}-{row['canal']}-{row['fecha']}")
    if an:
        p["anomalia"], p["anomalia_id"] = an["tipo"], an["id"]
    elif row.get("is_anomaly") and row.get("anomaly_tipo") in ("caida", "subida"):
        p["anomalia"], p["anomalia_id"] = row["anomaly_tipo"], f"{row['restaurante_id']}-{row['canal']}-{row['fecha']}"
    return p


def _other_channels(rows: list[dict], canal: str) -> list[dict]:
    out = []
    for r in rows:
        if r["canal"] == canal or r.get("ventas") is None or not r.get("ventas_esperadas"):
            continue
        out.append({"canal": r["canal"], "desv": r4(r["ventas"] / r["ventas_esperadas"] - 1),
                    "ventas": r["ventas"], "ventas_esperadas": r["ventas_esperadas"],
                    "z_ventas": r.get("z_ventas"), "es_anomalia": bool(r.get("is_anomaly"))})
    return out


def detail(ds: Dataset, anomaly_id: str) -> dict | None:
    a = ds.anom_by_id.get(anomaly_id)
    if not a:
        return None
    r = ds.rest_by_id.get(a["restaurante_id"]) or {"id": a["restaurante_id"], "provincia": a["provincia"], "ccaa": a["ccaa"]}
    rows = repo.series_rows(a["restaurante_id"], a["canal"], add_days(a["fecha"], -56), add_days(a["fecha"], 21))
    serie = [_serie_point(x, ds) for x in rows]
    referencia = [x for x in serie if x["fecha"] < a["fecha"] and day_diff(x["fecha"], a["fecha"]) % 7 == 0][-8:]
    day_rows = repo.series_day(a["restaurante_id"], a["fecha"])
    cal = next((x for x in day_rows if x["canal"] == a["canal"]), None)
    otros = a.get("otros_canales") or _other_channels(day_rows, a["canal"])
    cohorte = a.get("cohorte")
    if cohorte is None:
        peers = repo.peers_day(a["fecha"], a["canal"], r.get("_ccaa_slug"), a["restaurante_id"])
        if peers and peers.get("esperadas"):
            cohorte = r4(peers["ventas"] / peers["esperadas"] - 1)
    historial = [x for x in ds.anomalies if x["restaurante_id"] == a["restaurante_id"] and x["canal"] == a["canal"]
                 and x["id"] != a["id"]]
    zona = [x for x in ds.anomalies if x["fecha"] == a["fecha"] and x["ccaa"] == a["ccaa"] and x["id"] != a["id"]]
    # Variabilidad habitual (σ relativa) = desviación relativa / z, ambas del propio detector.
    var_v = abs(a["desviacion_pct"]) / abs(a["z_ventas"]) if a.get("z_ventas") and a.get("desviacion_pct") is not None else None
    var_t = abs(a["desviacion_tickets_pct"]) / abs(a["z_tickets"]) if a.get("z_tickets") and a.get("desviacion_tickets_pct") is not None else None
    ctx = {"calendario": cal, "otros_canales": otros, "cohorte": cohorte}
    det = merge_evidence_detail(build_detail(a, ctx), a.get("evidencia"))
    return {
        **a,
        "otros_canales": otros,
        "cohorte": cohorte,
        "calendario": {k: cal.get(k) for k in ("dia_semana", "tipo_festivo", "vispera_festivo", "fecha_especial", "navidad",
                                               "semana_santa", "is_eligible_for_alert", "exclusion_reason",
                                               "is_systemic_day", "motivo")} if cal else None,
        "detalle": det,
        "restaurante": public_restaurant(r),
        "serie": serie,
        "referencia": referencia,
        "variabilidad_habitual": {"ventas": r4(var_v) if var_v is not None else None,
                                  "tickets": r4(var_t) if var_t is not None else None},
        "historial": {"total": len(historial),
                      "recientes": [compact(x) for x in sorted(historial, key=lambda x: x["fecha"], reverse=True)[:6]]},
        "zona_mismo_dia": {"total": len(zona), "restaurantes": len({x["restaurante_id"] for x in zona})},
        "descomposicion": decomposition(a),
    }


def decomposition(a: dict) -> dict:
    """Descomposición del impacto en € entre volumen (tickets) y ticket medio."""
    t, te, tm, tme = a.get("tickets"), a.get("tickets_esperados"), a.get("ticket_medio"), a.get("ticket_medio_esperado")
    efecto_tickets = efecto_tm = None
    if None not in (t, te, tm, tme):
        efecto_tickets = r2((t - te) * tme)
        efecto_tm = r2((tm - tme) * t)
    return {
        "contribucion_tickets_pct": a.get("contribucion_tickets_pct"),
        "contribucion_tm_pct": a.get("contribucion_tm_pct"),
        "efecto_tickets_eur": efecto_tickets,
        "efecto_ticket_medio_eur": efecto_tm,
        "tickets": t, "tickets_esperados": te, "ticket_medio": tm, "ticket_medio_esperado": tme,
        "metrica_principal": a.get("metrica"),
    }


def compare(ds: Dataset, f: dict, pa: dict, pb: dict) -> dict:
    fa, fb = {**f, **pa}, {**f, **pb}

    def side(rng: dict, ff: dict) -> dict:
        return {**rng, **stats(filter_anomalies(ds, ff)),
                "canales": [{"canal": c["canal"], "total": c["total"], "impacto": c["impacto"]} for c in by_channel(ds, ff)]}

    return {"a": side(pa, fa), "b": side(pb, fb)}


def restaurant_info(ds: Dataset, rid: str) -> dict | None:
    r = ds.rest_by_id.get(rid)
    if not r:
        return None
    lst = [a for a in ds.anomalies if a["restaurante_id"] == rid]
    canales = [c for c in CHANNELS if (c == "sala") or (c == "autoking" and r["autoking"] == "si")
               or (c == "domicilio" and r["domicilio"] == "si") or any(a["canal"] == c for a in lst)]
    return {**public_restaurant(r), "canales": canales, "anomalias": stats(lst),
            "por_canal": [{"canal": c, **stats([a for a in lst if a["canal"] == c])} for c in canales]}


def day_context(ds: Dataset, fecha: str, provincia: str | None = None) -> dict:
    counts = repo.day_reason_counts(fecha)
    known = [x for x in ds.config.get("knownNeutralDays", []) if x.get("fecha") == fecha]
    lst = [a for a in ds.anomalies if a["fecha"] == fecha and (not provincia or a["provincia"] == provincia)]
    return {"fecha": fecha, "dia_semana": DIAS[dt.date.fromisoformat(fecha).isoweekday()],
            "filas_con_ventas": sum(counts.values()), "motivos_de_exclusion": counts, "evento_conocido": known,
            "alertas": stats(lst), "top": [compact(a) for a in lst[:8]]}


def dimensions(ds: Dataset) -> dict:
    run = ds.run or {}
    return {
        "source": "real", "dateMin": ds.date_min, "dateMax": ds.date_max, "evalStart": ds.config["evalStart"],
        "restaurantes": len(ds.restaurants), "sin_maestro": sum(1 for r in ds.restaurants if not r["maestro"]),
        "ccaas": ds.ccaas, "provinces": ds.provinces, "channels": CHANNELS, "totalAnomalias": len(ds.anomalies),
        "filas": ds.filas, "filasConBaseline": ds.filas_con_baseline,
        "generatedAt": run.get("created_at") or ds.loaded_at, "config": ds.config, "reasonCounts": ds.reason_counts,
        "restaurantIds": [r["id"] for r in ds.restaurants],
        "tipologias": sorted({r["tipologia"] for r in ds.restaurants if r["tipologia"]}),
        "ubicaciones": sorted({r["ubicacion"] for r in ds.restaurants if r["ubicacion"]}),
        "modelRun": {k: v for k, v in run.items() if k != "params_json"} if run else None,
        "modelStatus": ds.model_status,
        "defaultRange": default_range(ds),
    }


def resolve_anomaly_id(ds: Dataset, raw: str | None = None, restaurante: str | None = None, canal: str | None = None,
                       fecha: str | None = None) -> str | None:
    if raw and raw in ds.anom_by_id:
        return raw
    if raw:
        m = ID_RE.match(raw.strip())
        if m:
            return raw.strip()
    if restaurante and canal in CHANNELS and is_iso(fecha):
        return f"{normalize_restaurant_id(restaurante)}-{canal}-{fecha}"
    return None


__all__ = [n for n in dir() if not n.startswith("_")] + ["get_dataset"]
