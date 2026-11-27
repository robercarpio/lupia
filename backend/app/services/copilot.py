"""Copiloto de IA (Azure OpenAI o un modelo servido en Databricks) sobre las anomalías de la capa Gold.

Dos entradas:
- chat_turn(): POST /api/chat, streaming SSE con el protocolo del frontend
  (session · status · text · block · filters · focus · done · error). El modelo solo puede
  usar herramientas con parámetros validados (las mismas funciones del dashboard): no hay SQL
  libre y las cifras salen siempre de la base de datos.
- copilot_answer(): POST /api/copilot/chat, un turno JSON a partir de un episodio, una alerta o
  un restaurante y su evidencia_json.

Regla dura: el modelo no puede atribuir causas externas que no estén en la evidencia.
Si no hay modelo configurado o falla, se responde con un resumen determinista.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Callable, Iterator

from cachetools import TTLCache

from ..config import get_settings
from ..database import NotFound
from . import analytics as Q
from . import context_kb
from . import sales as S
from .dataset import Dataset, get_dataset
from .episodes import episode_detail
from .narrative import CHANNEL_LABEL, eur, num, pct

log = logging.getLogger("rbe.copilot")
Emit = Callable[[str, dict], None]

# ── Herramientas (function calling) ───────────────────────────────────
FILTROS = {
    "type": "object",
    "description": "Filtros de las anomalías. Omite los que no apliquen. Fechas en AAAA-MM-DD.",
    "properties": {
        "desde": {"type": "string", "description": "Fecha inicial AAAA-MM-DD"},
        "hasta": {"type": "string", "description": "Fecha final AAAA-MM-DD"},
        "restaurante": {"type": "string", "description": "Identificador, p. ej. R001"},
        "canales": {"type": "array", "items": {"type": "string", "enum": ["sala", "domicilio", "autoking"]}},
        "tipo": {"type": "string", "enum": ["caida", "subida"]},
        "comunidad": {"type": "string", "description": "Comunidad autónoma"},
        "provincia": {"type": "string"},
        "tipologia": {"type": "string", "description": "fs, il, dr o fc"},
        "score_min": {"type": "number", "description": "Score mínimo 0-1"},
        "impacto_min": {"type": "number", "description": "Impacto económico mínimo en valor absoluto (€)"},
        "prioridad": {"type": "string", "enum": ["alta", "media", "baja"]},
    },
    "additionalProperties": False,
}
MOSTRAR = {"type": "boolean", "description": "true si el resultado debe mostrarse al usuario como tabla, gráfico o tarjeta."}
SALES_SORTS = ("ventas", "ventas_asc", "tickets", "ticket_medio", "variacion", "variacion_asc")
SALES_BREAKDOWN = ("canal", "comunidad", "provincia", "tipologia", "ubicacion", "dia_semana", "mes")


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required or [], "additionalProperties": False}}}


TOOLS = [
    _tool("resumen_anomalias", "KPIs de las anomalías para unos filtros: total, impacto (pérdidas y subidas), caídas, "
          "subidas, restaurantes afectados, score medio y comparación con el periodo anterior de igual duración.",
          {"filtros": FILTROS, "mostrar": MOSTRAR}),
    _tool("listar_anomalias", "Lista alertas diarias concretas (restaurante, fecha, canal, tipo, ventas reales y esperadas, "
          "desviación, impacto, score, prioridad y explicación breve).",
          {"filtros": FILTROS, "orden": {"type": "string", "enum": ["impacto", "score", "fecha", "desviacion_pct", "prioridad"]},
           "direccion": {"type": "string", "enum": ["desc", "asc"]},
           "limite": {"type": "integer", "minimum": 1, "maximum": 25}, "mostrar": MOSTRAR}),
    _tool("listar_episodios", "Lista episodios (anomalías que duran varios días seguidos en el mismo restaurante y canal) "
          "con su duración, impacto total y prioridad.",
          {"filtros": FILTROS, "orden": {"type": "string", "enum": ["impacto", "prioridad", "score", "fecha", "duracion"]},
           "min_dias": {"type": "integer", "minimum": 1}, "limite": {"type": "integer", "minimum": 1, "maximum": 25}}),
    _tool("ranking_restaurantes", "Ranking de restaurantes por número de anomalías, impacto económico absoluto o score medio.",
          {"filtros": FILTROS, "orden": {"type": "string", "enum": ["anomalias", "impacto", "score"]},
           "limite": {"type": "integer", "minimum": 1, "maximum": 25}, "mostrar": MOSTRAR}),
    _tool("desglose", "Agrega las anomalías por una dimensión.",
          {"filtros": FILTROS, "dimension": {"type": "string", "enum": list(Q.BREAKDOWN_DIMS)}, "mostrar": MOSTRAR},
          ["dimension"]),
    _tool("detalle_anomalia", "Análisis completo de una alerta: hechos, calendario, otros canales, zona, histórico, "
          "evidencia, descomposición tickets vs ticket medio y recomendación. Por id o por restaurante + canal + fecha.",
          {"id": {"type": "string", "description": "p. ej. R001-domicilio-2025-03-11"}, "restaurante": {"type": "string"},
           "canal": {"type": "string", "enum": ["sala", "domicilio", "autoking"]}, "fecha": {"type": "string"},
           "mostrar": MOSTRAR}),
    _tool("comparar_periodos", "Compara dos periodos: KPIs de cada uno y reparto por canal.",
          {"filtros": FILTROS,
           "periodo_a": {"type": "object", "properties": {"desde": {"type": "string"}, "hasta": {"type": "string"}},
                         "required": ["desde", "hasta"], "additionalProperties": False},
           "periodo_b": {"type": "object", "properties": {"desde": {"type": "string"}, "hasta": {"type": "string"}},
                         "required": ["desde", "hasta"], "additionalProperties": False},
           "mostrar": MOSTRAR}, ["periodo_a", "periodo_b"]),
    _tool("serie_temporal", "Ventas reales frente a esperadas en el tiempo y anomalías por día (o semana si el rango "
          "supera 150 días).", {"filtros": FILTROS, "mostrar": MOSTRAR}),
    _tool("info_restaurante", "Ficha de un restaurante: maestro, canales activos reales y anomalías por canal.",
          {"restaurante": {"type": "string"}}, ["restaurante"]),
    _tool("get_restaurant_window",
          "OBLIGATORIA antes de diagnosticar cualquier incidencia de un restaurante. Devuelve la ficha del local "
          "(provincia, tipología y canales realmente activos) y la espina diaria continua SIN HUECOS entre fecha_inicio "
          "y fecha_fin (LEFT JOIN con series_summary y alerts_prioritized), indicando por cada día y canal: ventas, "
          "ventas_esperadas, tickets, is_anomaly, exclusion_reason o SIN_REGISTRO si no hubo ventas registradas, "
          "junto con todos los episodios/alertas de la ventana, eventos macro verificados y playbooks QSR relevantes.",
          {"restaurante_id": {"type": "string", "description": "ID del restaurante (p. ej. R057, R57 o 57; se normaliza automáticamente)"},
           "fecha_inicio": {"type": "string", "description": "Inicio de la ventana AAAA-MM-DD (incluye margen de -7 días)"},
           "fecha_fin": {"type": "string", "description": "Fin de la ventana AAAA-MM-DD (incluye margen de +7 días)"},
           "mostrar": MOSTRAR},
          ["restaurante_id", "fecha_inicio", "fecha_fin"]),
    _tool("contexto_operativo",
          "Consulta el contexto operativo curado local (sin búsqueda web): playbooks de revisión QSR y eventos macro "
          "verificados en España 2023-2026 (apagón 2025-04-28, DANA oct-nov 2024, incidencia domicilio 2023-11-11, etc.).",
          {"fecha_inicio": {"type": "string", "description": "Fecha inicial opcional AAAA-MM-DD"},
           "fecha_fin": {"type": "string", "description": "Fecha final opcional AAAA-MM-DD"},
           "patron": {"type": "string", "description": "Palabra clave opcional del patrón (cierre, domicilio, tickets, monocanal…)"},
           "comunidad": {"type": "string", "description": "Comunidad autónoma opcional"}}),
    _tool("contexto_dia", "Contexto de una fecha: cuántas filas se excluyeron y por qué (festivos, eventos generales, "
          "aperturas…) y alertas de ese día.", {"fecha": {"type": "string"}, "provincia": {"type": "string"}}, ["fecha"]),
    _tool("aplicar_filtros_dashboard", "Aplica filtros en el dashboard. Úsala cuando el usuario pida «muéstrame», "
          "«filtra» o «ábrelo en el dashboard».", {"filtros": FILTROS}, ["filtros"]),
    # ── Ventas reales (no dependen del modelo de anomalías) ──
    _tool("resumen_ventas", "Ventas reales del periodo: ventas totales (€), tickets, ticket medio, venta media diaria, "
          "restaurantes con ventas y mejor día, con la variación frente al periodo anterior de igual duración. "
          "Ignora los filtros propios de anomalías (tipo, score, impacto, prioridad).",
          {"filtros": FILTROS, "mostrar": MOSTRAR}),
    _tool("ranking_ventas", "Ranking de restaurantes por ventas reales del periodo (o tickets, ticket medio, o variación "
          "frente al periodo anterior: 'variacion_asc' = mayores caídas, 'variacion' = mayores crecimientos).",
          {"filtros": FILTROS, "orden": {"type": "string", "enum": list(SALES_SORTS)},
           "limite": {"type": "integer", "minimum": 1, "maximum": 25}, "mostrar": MOSTRAR}),
    _tool("desglose_ventas", "Ventas reales agregadas por canal, comunidad, provincia, tipología, ubicación, día de la "
          "semana o mes, con cuota y variación frente al periodo anterior.",
          {"filtros": FILTROS, "dimension": {"type": "string", "enum": list(SALES_BREAKDOWN)}, "mostrar": MOSTRAR},
          ["dimension"]),
    _tool("comparar_restaurantes", "Compara las ventas (y anomalías) de 2 a 10 restaurantes en el mismo periodo, cada uno "
          "con su variación frente al periodo anterior.",
          {"restaurantes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
           "filtros": FILTROS, "mostrar": MOSTRAR}, ["restaurantes"]),
]

STATUS = {
    "resumen_anomalias": "Calculando el resumen", "listar_anomalias": "Buscando anomalías",
    "listar_episodios": "Buscando episodios", "ranking_restaurantes": "Ordenando restaurantes",
    "desglose": "Agrupando resultados", "detalle_anomalia": "Analizando la anomalía",
    "comparar_periodos": "Comparando periodos", "serie_temporal": "Preparando la serie temporal",
    "info_restaurante": "Consultando el restaurante", "get_restaurant_window": "Analizando la ventana diaria del restaurante",
    "contexto_operativo": "Consultando playbooks y eventos verificados", "contexto_dia": "Revisando el calendario",
    "aplicar_filtros_dashboard": "Aplicando filtros", "resumen_ventas": "Sumando las ventas",
    "ranking_ventas": "Ordenando restaurantes por ventas", "desglose_ventas": "Agrupando las ventas",
    "comparar_restaurantes": "Comparando restaurantes",
}


def to_filters(ds: Dataset, fx: dict | None, *, strict_restaurant: bool = False,
               fallback_restaurant: str | None = None,
               extra_inp: dict | None = None) -> tuple[dict | None, list[str], str | None]:
    fx = dict(fx or {})
    if extra_inp:
        if not fx.get("restaurante"):
            top_r = extra_inp.get("restaurante") or extra_inp.get("restaurante_id")
            if top_r:
                fx["restaurante"] = top_r
    raw_rest = fx.get("restaurante")
    if raw_rest:
        rid = Q.normalize_restaurant_id(raw_rest)
        if strict_restaurant and rid not in ds.rest_by_id:
            return None, [], f"No existe el restaurante «{raw_rest}» (normalizado: {rid}). No se muestran datos agregados de la red."
        fx["restaurante"] = rid
    elif fallback_restaurant and not fx.get("comunidad") and not fx.get("provincia"):
        fx["restaurante"] = fallback_restaurant

    f, w = Q.normalize_filters({
        "from": fx.get("desde"), "to": fx.get("hasta"), "restaurante": fx.get("restaurante"),
        "canales": fx.get("canales"), "tipo": fx.get("tipo"), "ccaa": fx.get("comunidad"),
        "provincia": fx.get("provincia"), "tipologia": fx.get("tipologia"), "minScore": fx.get("score_min"),
        "minImpact": fx.get("impacto_min"), "prioridad": fx.get("prioridad"),
    }, ds)
    return f, w, None


def _extract_mentioned_restaurants(message: str, ds: Dataset) -> list[str]:
    """Detecta y normaliza IDs de restaurante mencionados en texto libre (R57, R057, R057/R57, tienda 57)."""
    found: list[str] = []
    for m in re.finditer(r"(?:\bR[A-Z]?[\s\-_]?(\d{1,3})\b|\b(?:tienda|local|restaurante)\s+#?(\d{1,3})\b)", message, re.I):
        digits = m.group(1) or m.group(2)
        if not digits:
            continue
        rid = f"R{digits.zfill(3)}"
        if rid in ds.rest_by_id and rid not in found:
            found.append(rid)
    return found


_NETWORK_SCOPE_RE = re.compile(
    r"\b(toda\s+la\s+red|red\s+completa|compar\w*|ranking|top\s+\d+|resto\s+de\s+(?:la\s+)?(?:red|locales|restaurantes)|espa[ñn]a)\b",
    re.I,
)


def describe(f: dict) -> str:
    p = [f"{f['from']} → {f['to']}"]
    if f.get("restaurante"):
        p.append(f["restaurante"])
    if f.get("canales"):
        p.append(" + ".join(CHANNEL_LABEL[c] for c in f["canales"]))
    if f.get("tipo"):
        p.append("Caídas" if f["tipo"] == "caida" else "Subidas")
    if f.get("provincia"):
        p.append(f["provincia"])
    elif f.get("ccaa"):
        p.append(f["ccaa"])
    if f.get("minScore"):
        p.append(f"score ≥ {f['minScore']}")
    if f.get("minImpact"):
        p.append(f"|impacto| ≥ {eur(f['minImpact'])}")
    if f.get("prioridad"):
        p.append(f"prioridad {f['prioridad']}")
    return " · ".join(p)


def _slim_detail(d: dict) -> dict:
    keep = {k: v for k, v in d.items() if k not in ("serie", "restaurante", "historial")}
    keep["restaurante"] = {
        k: d["restaurante"].get(k)
        for k in ("id", "provincia", "ccaa", "tipologia", "tamano", "ubicacion", "autoking", "domicilio")
    }
    keep["canales_activos_restaurante"] = d.get("canales_activos_restaurante")
    keep["tiene_otros_canales"] = d.get("tiene_otros_canales")
    keep["estado_otros_canales"] = d.get("estado_otros_canales")
    keep["historial_total"] = d["historial"]["total"]
    return keep


def _model_note(ds: Dataset) -> str | None:
    st = ds.model_status["anomalias"]
    if st == "pendiente":
        return ("Las tablas de anomalías están vacías: el modelo externo todavía no ha escrito resultados. "
                "Que no haya alertas NO significa que no existan anomalías.")
    if st == "sin_alertas":
        return "El modelo se ha ejecutado pero no ha registrado alertas."
    return None


def _remember(session: dict, ids: list[str] | None = None, period: dict | None = None) -> None:
    """Memoria corta para seguimientos («¿y respecto al mes anterior?»): últimos restaurantes y periodo."""
    if ids:
        session["restaurantes"] = [i for i in ids if i][:10]
    if period:
        session["periodo"] = {"desde": period["from"], "hasta": period["to"]}


def run_tool(ds: Dataset, name: str, inp: dict, session: dict) -> dict:
    """Ejecuta una herramienta → {data (para el modelo), block? (UI), filters?, focus?}."""
    show = bool(inp.get("mostrar"))
    turn_rest = session.get("_turn_target_restaurant")

    def _filters(allow_turn_fallback: bool = True) -> tuple[dict | None, list[str], dict | None]:
        f, w, err = to_filters(
            ds,
            inp.get("filtros"),
            strict_restaurant=True,
            fallback_restaurant=turn_rest if allow_turn_fallback else None,
            extra_inp=inp,
        )
        if err or f is None:
            return None, [], {"data": {"error": err or "Filtro de restaurante no válido."}}
        return f, w, None

    if name == "get_restaurant_window":
        raw_r = inp.get("restaurante_id") or inp.get("restaurante") or turn_rest
        if not raw_r:
            return {"data": {"error": "Falta el parámetro obligatorio restaurante_id."}}
        rid = Q.normalize_restaurant_id(raw_r)
        win = Q.restaurant_window(ds, rid, str(inp.get("fecha_inicio") or ""), str(inp.get("fecha_fin") or ""))
        if "error" in win:
            return {"data": win}
        per = win["periodo"]
        f_win, _ = Q.normalize_filters({"from": per["desde"], "to": per["hasta"], "restaurante": rid}, ds)
        _remember(session, [rid], f_win)
        block = None
        if show:
            t = Q.timeline(ds, f_win)
            block = {
                "type": "timeline",
                "title": f"{rid} · Ventas reales vs esperadas",
                "subtitle": describe(f_win),
                "data": t,
                "filters": f_win,
            }
        return {"data": win, "block": block}

    if name == "contexto_operativo":
        return {
            "data": context_kb.query_context(
                date_from=inp.get("fecha_inicio"),
                date_to=inp.get("fecha_fin"),
                patron=inp.get("patron"),
                ccaa=inp.get("comunidad"),
            )
        }

    if name == "resumen_anomalias":
        f, w, err_out = _filters(allow_turn_fallback=True)
        if err_out:
            return err_out
        assert f is not None
        s = Q.summary(ds, f)
        data = {k: v for k, v in s.items() if k != "spark"}
        return {"data": {"filtros": describe(f), "avisos": w, "estado_modelo": _model_note(ds), **data},
                "block": show and {"type": "kpis", "title": "Resumen de anomalías", "subtitle": describe(f), "data": s, "filters": f}}
    if name == "listar_anomalias":
        f, w, err_out = _filters(allow_turn_fallback=True)
        if err_out:
            return err_out
        assert f is not None
        t = Q.table(ds, f, sort=inp.get("orden") or "impacto", direction=inp.get("direccion") or "desc",
                    size=min(25, int(inp.get("limite") or 10)))
        _remember(session, list(dict.fromkeys(x["restaurante_id"] for x in t["rows"])), f)
        return {"data": {"filtros": describe(f), "avisos": w, "estado_modelo": _model_note(ds), "total": t["total"], "mostradas": len(t["rows"]), "anomalias": t["rows"]},
                "block": show and {"type": "anomalies", "title": f"Anomalías · {num(t['total'])} en total",
                                   "subtitle": describe(f), "rows": t["rows"], "total": t["total"], "filters": f}}
    if name == "listar_episodios":
        from .episodes import list_episodes

        f, w, err_out = _filters(allow_turn_fallback=True)
        if err_out:
            return err_out
        assert f is not None
        e = list_episodes(ds, f, sort=inp.get("orden") or "impacto", size=min(25, int(inp.get("limite") or 10)),
                          min_dias=int(inp.get("min_dias") or 1))
        rows = [{k: r.get(k) for k in ("episode_id", "restaurante_id", "canal", "tipo", "provincia", "ccaa", "fecha_inicio",
                                       "fecha_fin", "duracion_dias", "impacto_total_eur", "prioridad",
                                       "desviacion_media_ventas_pct", "explicacion_principal", "recomendacion_principal")}
                for r in e["rows"]]
        return {"data": {"filtros": describe(f), "avisos": w, "total": e["total"], "episodios": rows}}
    if name == "ranking_restaurantes":
        f, w, err_out = _filters(allow_turn_fallback=False)
        if err_out:
            return err_out
        assert f is not None
        r = Q.by_restaurant(ds, f, sort=inp.get("orden") or "anomalias", limit=min(25, int(inp.get("limite") or 10)))
        _remember(session, [x["restaurante_id"] for x in r["rows"]], f)
        return {"data": {"filtros": describe(f), "avisos": w, "restaurantes_con_anomalias": r["total"], "ranking": r["rows"]},
                "block": show and {"type": "ranking", "title": "Ranking de restaurantes", "subtitle": describe(f),
                                   "sort": inp.get("orden") or "anomalias", "rows": r["rows"], "filters": f}}
    if name == "desglose":
        f, w, err_out = _filters(allow_turn_fallback=True)
        if err_out:
            return err_out
        assert f is not None
        rows = Q.breakdown(ds, f, inp.get("dimension") or "canal")
        dim = inp.get("dimension") or "canal"
        return {"data": {"filtros": describe(f), "avisos": w, "dimension": dim, "grupos": rows[:30]},
                "block": show and {"type": "bars", "title": f"Anomalías por {dim.replace('_', ' ')}", "subtitle": describe(f),
                                   "dimension": dim, "rows": rows[:20], "filters": f}}
    if name == "detalle_anomalia":
        aid = Q.resolve_anomaly_id(ds, inp.get("id"), inp.get("restaurante"), inp.get("canal"), inp.get("fecha"))
        d = Q.detail(ds, aid) if aid else None
        if not d:
            return {"data": {"error": f"No hay ninguna alerta con id «{aid or '?'}». Puede que ese día no sea anomalía o no tenga ventas."}}
        session["focus"] = d["id"]
        return {"data": _slim_detail(d),
                "block": show and {"type": "anomaly", "id": d["id"], "anomaly": Q.compact(d), "detalle": d["detalle"], "serie": d["serie"]},
                "focus": d["id"]}
    if name == "comparar_periodos":
        f, w, err_out = _filters(allow_turn_fallback=True)
        if err_out:
            return err_out
        assert f is not None
        pa, _ = Q.normalize_filters({"from": (inp.get("periodo_a") or {}).get("desde"), "to": (inp.get("periodo_a") or {}).get("hasta")}, ds)
        pb, _ = Q.normalize_filters({"from": (inp.get("periodo_b") or {}).get("desde"), "to": (inp.get("periodo_b") or {}).get("hasta")}, ds)
        ra, rb = {"from": pa["from"], "to": pa["to"]}, {"from": pb["from"], "to": pb["to"]}
        c = Q.compare(ds, f, ra, rb)
        c["ventas"] = S.compare_periods(ds, f, ra, rb)
        sub = " · ".join(describe(f).split(" · ")[1:])
        return {"data": {"filtros": describe(f), "avisos": w, **c},
                "block": show and {"type": "compare", "title": "Comparación de periodos", "subtitle": sub, "data": c}}
    if name == "serie_temporal":
        f, w, err_out = _filters(allow_turn_fallback=True)
        if err_out:
            return err_out
        assert f is not None
        t = Q.timeline(ds, f)
        con_ref = t["baseline"] != "pendiente"
        res = [{"desde": b["from"], "hasta": b["to"], "ventas_totales": round(b["ventas_totales"]),
                "tickets": b["tickets_totales"], "caidas": b["caidas"], "subidas": b["subidas"],
                **({"ventas_con_referencia": round(b["ventas"]), "esperadas": round(b["esperadas"])} if con_ref else {})}
               for b in t["buckets"]]
        if len(res) > 60:  # resume a 60 puntos como máximo (agrupando periodos consecutivos)
            k = -(-len(res) // 60)
            res = [{"desde": g[0]["desde"], "hasta": g[-1]["hasta"],
                    "ventas_totales": sum(x["ventas_totales"] for x in g), "tickets": sum(x["tickets"] for x in g),
                    "caidas": sum(x["caidas"] for x in g), "subidas": sum(x["subidas"] for x in g)}
                   for g in (res[i:i + k] for i in range(0, len(res), k))]
        title = "Ventas reales vs esperadas" if con_ref else "Evolución de las ventas reales"
        _remember(session, [f["restaurante"]] if f.get("restaurante") else None, f)
        return {"data": {"filtros": describe(f), "avisos": w, "granularidad": "semanal" if t["step"] == 7 else "diaria",
                         "puntos": res, "referencia_modelo": t["baseline"],
                         "nota": None if con_ref else "El modelo aún no ha calculado ventas esperadas: solo hay ventas reales."},
                "block": show and {"type": "timeline", "title": title, "subtitle": describe(f), "data": t, "filters": f}}
    if name == "info_restaurante":
        rid = Q.normalize_restaurant_id(inp.get("restaurante"))
        info = Q.restaurant_info(ds, rid)
        if not info:
            return {"data": {"error": f"No existe el restaurante «{inp.get('restaurante')}» (normalizado: {rid})."}}
        f, w, _ = to_filters(ds, inp.get("filtros"))
        assert f is not None
        f = {**f, "restaurante": rid}
        _remember(session, [rid], f)
        v = S.summary(ds, f)
        return {"data": {**info, "periodo_ventas": describe(f), "avisos": w,
                         "ventas_periodo": {k: v.get(k) for k in ("ventas", "tickets", "ticket_medio", "venta_media_dia",
                                                                  "mejor_dia", "variacion", "prevRange")},
                         "ventas_por_canal": S.by_channel(ds, f)}}
    if name == "resumen_ventas":
        f, w, err_out = _filters(allow_turn_fallback=True)
        if err_out:
            return err_out
        assert f is not None
        s = S.summary(ds, f)
        _remember(session, [f["restaurante"]] if f["restaurante"] else None, f)
        data = {k: v for k, v in s.items() if k not in ("spark", "filas", "filas_con_baseline")}
        return {"data": {"filtros": describe(f), "avisos": w, **data},
                "block": show and {"type": "sales_kpis", "title": "Ventas del periodo", "subtitle": describe(f), "data": s,
                                   "filters": f}}
    if name == "ranking_ventas":
        f, w, err_out = _filters(allow_turn_fallback=False)
        if err_out:
            return err_out
        assert f is not None
        orden = inp.get("orden") if inp.get("orden") in SALES_SORTS else "ventas"
        r = S.ranking(ds, f, sort=orden, limit=min(25, int(inp.get("limite") or 10)))
        _remember(session, [x["restaurante_id"] for x in r["rows"]], f)
        return {"data": {"filtros": describe(f), "avisos": w, "orden": orden, "restaurantes_con_ventas": r["total"],
                         "periodo_anterior": r["prevRange"], "ranking": r["rows"]},
                "block": show and {"type": "sales_ranking", "title": "Ranking de restaurantes por ventas", "subtitle": describe(f),
                                   "sort": orden, "rows": r["rows"], "filters": f}}
    if name == "desglose_ventas":
        f, w, err_out = _filters(allow_turn_fallback=True)
        if err_out:
            return err_out
        assert f is not None
        dim = inp.get("dimension") if inp.get("dimension") in SALES_BREAKDOWN else "canal"
        rows = S.breakdown(ds, f, dim)
        _remember(session, period=f)
        return {"data": {"filtros": describe(f), "avisos": w, "dimension": dim, "grupos": rows[:30]},
                "block": show and {"type": "sales_bars", "title": f"Ventas por {dim.replace('_', ' ')}", "subtitle": describe(f),
                                   "dimension": dim, "rows": rows[:20], "filters": f}}
    if name == "comparar_restaurantes":
        f, w, err_out = _filters(allow_turn_fallback=False)
        if err_out:
            return err_out
        assert f is not None
        ids = [Q.normalize_restaurant_id(x) for x in (inp.get("restaurantes") or [])][:10]
        missing = [i for i in ids if i not in ds.rest_by_id]
        c = S.compare_restaurants(ds, f, ids)
        _remember(session, [x["restaurante_id"] for x in c["rows"]], f)
        return {"data": {"filtros": describe(f), "avisos": w + ([f"No existen: {', '.join(missing)}"] if missing else []),
                         "periodo_anterior": c["prevRange"], "restaurantes": c["rows"]},
                "block": show and c["rows"] and {"type": "sales_compare", "title": "Comparativa de restaurantes",
                                                 "subtitle": describe({**f, "restaurante": ""}), "rows": c["rows"]}}
    if name == "contexto_dia":
        if not Q.is_iso(inp.get("fecha")):
            return {"data": {"error": "Fecha no válida (AAAA-MM-DD)."}}
        from .geo import resolve

        prov = resolve(inp.get("provincia"), [p["provincia"] for p in ds.provinces]) if inp.get("provincia") else None
        return {"data": Q.day_context(ds, inp["fecha"], prov)}
    if name == "aplicar_filtros_dashboard":
        f, w, err_out = _filters(allow_turn_fallback=True)
        if err_out:
            return err_out
        assert f is not None
        s = Q.summary(ds, f)
        return {"data": {"aplicado": True, "filtros": describe(f), "avisos": w, "anomalias": s["total"]},
                "block": {"type": "filters", "title": "Filtros aplicados en el dashboard", "subtitle": describe(f),
                          "total": s["total"], "filters": f},
                "filters": f}
    return {"data": {"error": f"Herramienta desconocida: {name}"}}


# ── Prompt ────────────────────────────────────────────────────────────
GUARDRAILS = """## Reglas estrictas
1. Toda cifra que des debe salir de las herramientas o de la evidencia de esta conversación. Nunca inventes ni estimes números, restaurantes ni fechas. Si no hay datos, dilo.
2. Las causas reales de las desviaciones NO están en los datos. Está PROHIBIDO atribuir o sugerir causas externas (obras, averías, meteorología, huelgas, cortes de luz, eventos deportivos, conciertos, promociones, problemas de personal, de proveedores o de plataformas de reparto…), ni siquiera en forma de preguntas o hipótesis en las recomendaciones, salvo que aparezcan literalmente en la evidencia recibida o en la tabla de eventos macro verificados. Puedes describir lo que muestran los datos (qué métrica se movió, si fue solo un canal, si los restaurantes comparables se movieron, cuántos días duró, días SIN_REGISTRO) y recomendar verificar apertura/horario real, registro completo de ventas o posible duplicidad con el restaurante.
3. Distingue siempre **qué ha pasado** (hechos), **cuánto importa** (impacto en € y prioridad) y **por qué merece revisión** (lo que la evidencia descarta: festivo, víspera, fecha especial, movimiento de la zona, otros canales).
4. No hay previsión de ventas futuras: compara solo lo observado con lo esperado (baseline). No predigas el futuro.
5. Lenguaje de negocio, en español, breve y accionable. Traduce la jerga: z → «veces su variabilidad habitual»; baseline → «lo esperado»; P1/P2/P3 → prioridad alta/media/baja. Importes con formato español (1.234 €) y porcentajes redondeados.
6. Las métricas de evaluación del reto (F1, precisión, recall…) requieren la clave del jurado: no las inventes."""


def system_prompt(ds: Dataset) -> str:
    st = ds.model_status
    if st["anomalias"] == "pendiente":
        estado = ("Las tablas de anomalías (alerts_prioritized, model_runs) están VACÍAS: el modelo externo aún no ha escrito "
                  "resultados. Si preguntan por anomalías, dilo claramente (no significa que no existan) y ofrece datos de ventas.")
    elif st["anomalias"] == "sin_alertas":
        estado = "El modelo se ha ejecutado pero no ha registrado alertas."
    else:
        estado = f"{len(ds.anomalies)} alertas diarias en la ejecución activa del modelo ({st['ejecucion'] or 'sin id'})."
    ref = {"pendiente": "El modelo aún NO ha calculado ventas esperadas: no hables de «esperado» ni de desviaciones de ventas, solo de ventas reales y de su variación frente a otros periodos.",
           "parcial": "El modelo ha calculado ventas esperadas solo para una parte de los días.",
           "completo": "Hay ventas esperadas (referencia del modelo) para todos los días."}[st["baseline"]]
    return f"""Eres el Copiloto de Operaciones de ventas de {len(ds.restaurants)} restaurantes Burger King en España (reto IndesIAhack 2026 · RBE). Hablas con usuarios de negocio, en el idioma en que te escriban (por defecto español).

## Datos disponibles (Databricks, capa Gold)
- Ventas reales (€) y tickets diarios por restaurante y canal (sala, domicilio, autoking) del {ds.date_min} al {ds.date_max}. «Hoy» = {ds.date_max}; «esta semana» = los 7 días hasta {ds.date_max}; «el último mes» = los 30 días hasta {ds.date_max}; «el mes anterior» = los 30 días previos a ese mes. No hay datos posteriores a {ds.date_max}.
- Maestro de restaurantes: comunidad, provincia, tipología (fs, il, dr, fc), tamaño, ubicación. {sum(1 for r in ds.restaurants if not r['maestro'])} restaurantes no tienen provincia en el maestro. Comunidades: {', '.join(ds.ccaas)}.
- No hay datos de productos, categorías de producto, costes, márgenes ni clientes: si los piden, dilo.
- Importes escalados por un factor fijo por restaurante (el ticket medio se conserva).
- Anomalías: {estado} Una alerta = restaurante + canal + día cuyas ventas o tickets se desvían mucho de lo esperado sin explicación de calendario; si dura varios días es un episodio.
- Referencia del modelo: {ref}
- Las ventas y tickets son datos registrados; las ventas esperadas y el impacto de una anomalía son ESTIMACIONES del modelo: distínguelo al responder.

{GUARDRAILS}
7. Indica siempre el periodo exacto al que se refieren las cifras (fechas de inicio y fin). En comparaciones, di qué dos periodos comparas.
8. Si la pregunta es ambigua (qué restaurante, qué periodo, qué métrica) y no se resuelve con <contexto_app>, pide una aclaración breve en lugar de suponer.
9. PROHIBIDO responder sobre un restaurante concreto usando métricas agregadas de toda la red (ni emitir tablas/gráficos sin `filtros.restaurante` cuando el usuario pregunta por un solo local). Normaliza siempre los IDs (`R57` o `57` → `R057`).
10. REGLA OBLIGATORIA DE DIAGNÓSTICO DE RESTAURANTE: antes de diagnosticar cualquier incidencia o pregunta sobre un restaurante concreto, llama SIEMPRE primero a `get_restaurant_window(restaurante_id, fecha_inicio, fecha_fin)` ampliando el rango con un margen de **±7 días** alrededor del periodo o fechas consultadas (p. ej. para «marzo/abril de 2025», usa `2025-03-01` a `2025-04-30` o la ventana ±7 días alrededor de las fechas clave).
11. REPORTE COMPLETO EN EL PRIMER TURNO: cuando respondas sobre un restaurante, informa en el PRIMER turno el **episodio consolidado completo**: lista TODAS las fechas con alerta encontradas en la ventana (sin omitir días anteriores ni posteriores), indica explícitamente los días intermedios `SIN_REGISTRO` y los días excluidos por calendario (`festivo`, `vispera_festivo`, `dia_neutro`, etc.), comprueba los `canales_activos` reales del local en `restaurante.canales_activos` (si el restaurante solo opera `sala`, NUNCA digas que `domicilio` o `autoking` fallaron), y añade las comprobaciones operativas relevantes de los playbooks QSR.

{context_kb.prompt_summary()}

## Uso de herramientas
- Diagnóstico de un restaurante concreto → **SIEMPRE empieza por `get_restaurant_window`** (con margen ±7 días; puedes usar `mostrar=true` para mostrar la evolución solo de ese local) y, si necesitas una tabla de sus alertas, `listar_anomalias` pasando **siempre** `filtros.restaurante` (y `mostrar=true` solo filtrado a ese local).
- Ventas → resumen_ventas, ranking_ventas, desglose_ventas, comparar_restaurantes, serie_temporal, comparar_periodos. Anomalías → resumen_anomalias, listar_anomalias, listar_episodios, ranking_restaurantes, desglose, detalle_anomalia. Contexto curado → contexto_operativo.
- «Qué restaurante ha vendido más» → ranking_ventas orden=ventas. «Mayor caída de ventas» → ranking_ventas orden=variacion_asc (frente al periodo anterior) y, si hay anomalías, listar_anomalias tipo=caida.
- Si no se indica periodo, usa los filtros del dashboard que llegan en <contexto_app> e indica el periodo usado.
- Seguimientos: <contexto_app> trae los últimos restaurantes y periodo consultados (memoria_conversacion) y `restaurantes_mencionados_normalizados` si el mensaje actual cita algún restaurante.
- Usa mostrar=true cuando una tabla, ranking, gráfico o tarjeta ayude (siempre con el mismo filtro que estás respondiendo); entonces destaca las conclusiones clave sin inventar datos.
- Si el usuario pide ver o filtrar algo en el dashboard, usa aplicar_filtros_dashboard.
- Si te preguntan algo ajeno a estos datos, reconduce amablemente."""


VOICE_RULES = ("Modo voz: tu respuesta se leerá en voz alta. Responde en 1 a 3 frases naturales, sin Markdown, sin listas "
               "ni tablas, con cifras redondeadas. Si hay una tabla o gráfico útil, usa mostrar=true y di que lo muestras en pantalla.")


# ── Cliente del modelo (Azure OpenAI o Databricks Model Serving) ───────
_client = None


def ai_status() -> dict:
    s = get_settings()
    return {"configured": s.ai_configured, "model": s.ai_model, "provider": s.ai_provider or "none"}


def _llm():
    """Cliente OpenAI-compatible del proveedor activo."""
    global _client
    s = get_settings()
    if s.ai_provider == "azure_openai":
        if _client is None:
            from openai import AzureOpenAI

            _client = AzureOpenAI(azure_endpoint=s.azure_endpoint, api_key=s.azure_api_key, api_version=s.azure_api_version,
                                  timeout=60, max_retries=2)
        return _client
    # Databricks: el token OAuth caduca, así que se crea el cliente (ligero) con el token vigente en cada turno.
    from openai import OpenAI

    from ..database import llm_credentials

    base_url, token = llm_credentials()
    return OpenAI(base_url=base_url, api_key=token, timeout=90, max_retries=2)


# ── Sesiones ──────────────────────────────────────────────────────────
_sessions: TTLCache = TTLCache(maxsize=1000, ttl=3 * 3600)
MAX_HISTORY = 40          # mensajes como máximo que se envían al modelo
MAX_TURNS = 8             # turnos de usuario como máximo
FULL_TOOL_TURNS = 2       # en los últimos N turnos los resultados de herramientas van completos
TOOL_CHARS_FULL = 24000   # tamaño máximo de un resultado de herramienta (admite ventana diaria continua)
TOOL_CHARS_OLD = 1200     # resultados de turnos antiguos: resumidos


def _session(sid: str | None) -> dict:
    s = _sessions.get(sid) if sid else None
    if s is None:
        sid = str(uuid.uuid4())
        s = {"id": sid, "messages": [], "focus": None}
    _sessions[sid] = s  # renueva el TTL
    return s


def reset_session(sid: str | None) -> None:
    if sid:
        _sessions.pop(sid, None)


def _trim(messages: list[dict]) -> list[dict]:
    """Historial controlado que se envía al modelo.

    - Corta siempre al inicio de un turno de usuario (no separa tool_calls de sus resultados).
    - Como mucho MAX_TURNS turnos y MAX_HISTORY mensajes.
    - Los resultados de herramientas de turnos antiguos se resumen (la memoria de entidades y periodo
      viaja aparte en <contexto_app>), así el coste no crece con la conversación.
    """
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    start = users[-MAX_TURNS] if len(users) > MAX_TURNS else 0
    if len(messages) - start > MAX_HISTORY:
        cut = len(messages) - MAX_HISTORY
        while cut < len(messages) and messages[cut].get("role") != "user":
            cut += 1
        start = max(start, cut)
    recent = users[-FULL_TOOL_TURNS] if len(users) >= FULL_TOOL_TURNS else 0
    out = []
    for i, m in enumerate(messages[start:], start):
        if m.get("role") == "tool" and i < recent and len(m.get("content") or "") > TOOL_CHARS_OLD:
            m = {**m, "content": m["content"][:TOOL_CHARS_OLD] + "… [resultado anterior resumido]"}
        out.append(m)
    return out


def _tool_content(data: Any) -> str:
    s = _jsonable(data)
    return s if len(s) <= TOOL_CHARS_FULL else s[:TOOL_CHARS_FULL] + "… [recortado: pide menos filas]"


def _jsonable(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


# ── Turno de chat con streaming ───────────────────────────────────────
def chat_turn(body: dict) -> Iterator[str]:
    """Generador de eventos SSE para POST /api/chat."""
    events: list[str] = []

    def emit(event: str, data: dict) -> None:
        events.append(f"event: {event}\ndata: {_jsonable(data)}\n\n")

    def flush() -> Iterator[str]:
        while events:
            yield events.pop(0)

    session = _session(body.get("conversationId"))
    emit("session", {"id": session["id"]})
    yield from flush()
    try:
        ds = get_dataset()
    except Exception as e:  # noqa: BLE001
        emit("error", {"message": str(e), "code": getattr(e, "code", None)})
        emit("done", {})
        yield from flush()
        return

    dash, _ = Q.normalize_filters(body.get("dashboardFilters") or {}, ds)
    focus = body.get("focusId")
    if focus and focus in ds.anom_by_id:
        session["focus"] = focus
    message = str(body.get("message") or "")[:4000]
    voice = bool(body.get("voice"))
    mentioned_rests = _extract_mentioned_restaurants(message, ds)
    turn_target_rest = (
        mentioned_rests[0]
        if len(mentioned_rests) == 1 and not _NETWORK_SCOPE_RE.search(message)
        else None
    )
    session["_turn_target_restaurant"] = turn_target_rest
    ctx = {
        "filtros_dashboard": describe(dash),
        "filtros_dashboard_json": {k: v for k, v in {
            "desde": dash["from"], "hasta": dash["to"], "restaurante": dash["restaurante"] or None,
            "canales": dash["canales"] or None, "tipo": dash["tipo"] or None, "comunidad": dash["ccaa"] or None,
            "provincia": dash["provincia"] or None, "prioridad": dash["prioridad"] or None}.items() if v},
        "restaurantes_mencionados_normalizados": mentioned_rests or None,
        "anomalia_en_foco": session.get("focus"),
        "memoria_conversacion": {k: v for k, v in {"ultimos_restaurantes": session.get("restaurantes"),
                                                   "ultimo_periodo": session.get("periodo")}.items() if v} or None,
        "ultimo_dia_con_datos": ds.date_max,
        "estado_anomalias": ds.model_status["anomalias"],
        "modo": "voz" if voice else "texto",
    }
    user_content = f"<contexto_app>{_jsonable(ctx)}</contexto_app>\n" + (f"{VOICE_RULES}\n" if voice else "") + message

    if not get_settings().ai_configured:
        _fallback_turn(ds, session, dash, message, emit, note=True)
        emit("done", {})
        yield from flush()
        return

    start = len(session["messages"])
    session["messages"].append({"role": "user", "content": user_content})
    try:
        client = _llm()
        deployment = get_settings().ai_model
        for _ in range(8):
            stream = client.chat.completions.create(
                model=deployment,
                messages=[{"role": "system", "content": system_prompt(ds)}, *_trim(session["messages"])],
                tools=TOOLS, tool_choice="auto", stream=True,
            )
            text, calls = "", {}
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    text += delta.content
                    emit("text", {"delta": delta.content})
                for tc in delta.tool_calls or []:
                    c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        c["id"] = tc.id
                    if tc.function and tc.function.name:
                        if not c["name"]:
                            emit("status", {"text": STATUS.get(tc.function.name, "Consultando datos"), "tool": tc.function.name})
                        c["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        c["args"] += tc.function.arguments
                yield from flush()
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                                     for _, c in sorted(calls.items())]
            session["messages"].append(msg)
            if not calls:
                break
            for c in msg["tool_calls"]:
                fn = c["function"]
                try:
                    args = json.loads(fn["arguments"] or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("argumentos no válidos")
                    out = run_tool(ds, fn["name"], args, session)
                except Exception as e:  # noqa: BLE001
                    log.warning("Herramienta %s falló: %s", fn["name"], e)
                    out = {"data": {"error": str(e)[:300]}}
                if out.get("block"):
                    emit("block", {"block": {**out["block"], "key": c["id"]}})
                if out.get("filters"):
                    emit("filters", {"filters": out["filters"]})
                if out.get("focus"):
                    emit("focus", {"id": out["focus"]})
                session["messages"].append({"role": "tool", "tool_call_id": c["id"], "content": _tool_content(out["data"])})
                yield from flush()
            emit("text", {"delta": "\n\n", "soft": True})
            yield from flush()
        emit("done", {})
    except GeneratorExit:
        # El cliente cortó la conexión (barge-in de voz): se deja el historial coherente.
        del session["messages"][start:]
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("El modelo de IA (%s) falló", get_settings().ai_provider)
        del session["messages"][start:]
        emit("error", {"message": _error_message(e), "code": getattr(e, "status_code", None)})
        _fallback_turn(ds, session, dash, message, emit, note=False)
        emit("done", {})
    finally:
        session.pop("_turn_target_restaurant", None)
    yield from flush()


def _error_message(e: Exception) -> str:
    name = type(e).__name__
    dbx = get_settings().ai_provider == "databricks"
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return ("Sin permiso para usar el endpoint de Databricks (DATABRICKS_LLM_ENDPOINT): la app necesita «Can query»."
                if dbx else "Credenciales de Azure OpenAI no válidas. Revisa AZURE_OPENAI_API_KEY y AZURE_OPENAI_ENDPOINT.")
    if name == "NotFoundError":
        return ("No existe el endpoint de Databricks indicado en DATABRICKS_LLM_ENDPOINT."
                if dbx else "No existe el despliegue de Azure OpenAI indicado en AZURE_OPENAI_DEPLOYMENT.")
    if name == "RateLimitError":
        return "Se ha alcanzado el límite de peticiones del modelo. Inténtalo en unos segundos."
    if name in ("APIConnectionError", "APITimeoutError"):
        return "No se puede conectar con el modelo de IA."
    return f"Error del servicio de IA: {str(e)[:300]}"


# ── Modo determinista (sin IA) ────────────────────────────────────────
ID_IN_TEXT = re.compile(r"\b(R\d{3})-(sala|domicilio|autoking)-(\d{4}-\d{2}-\d{2})\b", re.I)
REST_IN_TEXT = re.compile(r"\bR[A-Z]?[\s\-_]?(\d{1,3})\b", re.I)
SALES_IN_TEXT = re.compile(r"vent|vend|factur|ingres|ticket|evoluci", re.I)
ANOM_IN_TEXT = re.compile(r"anomal|alerta|ca[ií]da|subida|desviaci|episodio|incidencia", re.I)
CAIDA_IN_TEXT = re.compile(r"ca[ií]da|baj[aó]n|bajada|p[eé]rdida|desplom", re.I)
SUBIDA_IN_TEXT = re.compile(r"subida|pico|aument|increment|disparad", re.I)
CANAL_IN_TEXT = {"sala": re.compile(r"\bsala\b", re.I), "domicilio": re.compile(r"domicilio|delivery|reparto", re.I),
                 "autoking": re.compile(r"autoking|auto ?king|drive", re.I)}


def _geo_terms(ds: Dataset) -> list[tuple[str, dict]]:
    """Términos normalizados (nombres, slugs y alias) → filtro geográfico, del más largo al más corto.
    Una provincia fija también su comunidad; un nombre que solo es comunidad deja la provincia vacía."""
    from .geo import ALIASES, CCAAS, PROVINCIAS, norm

    prov_ccaa = {p["provincia"]: p["ccaa"] for p in ds.provinces}
    terms: dict[str, dict] = {}
    for c in ds.ccaas:
        terms[norm(c)] = {"ccaa": c, "provincia": ""}
    for slug, name in CCAAS.items():
        if name in ds.ccaas:
            terms.setdefault(norm(slug), {"ccaa": name, "provincia": ""})
    for alias, target in ALIASES.items():
        hit = next((c for c in ds.ccaas if norm(c) == target), None)
        if hit:
            terms.setdefault(alias, {"ccaa": hit, "provincia": ""})
    # Las provincias van después y pisan: «Madrid», «Murcia», «Valencia» → provincia (y su comunidad).
    for prov, ccaa in prov_ccaa.items():
        terms[norm(prov)] = {"ccaa": ccaa, "provincia": prov}
    for slug, name in PROVINCIAS.items():
        if name in prov_ccaa:
            terms[norm(slug)] = {"ccaa": prov_ccaa[name], "provincia": name}
    for alias, target in ALIASES.items():
        hit = next((p for p in prov_ccaa if norm(p) == target), None)
        if hit:
            terms[alias] = {"ccaa": prov_ccaa[hit], "provincia": hit}
    return sorted(terms.items(), key=lambda kv: -len(kv[0]))


def _filters_in_text(ds: Dataset, message: str) -> dict:
    """Filtros que se pueden leer directamente en la pregunta (modo básico, sin IA):
    provincia o comunidad, tipo de anomalía y canal. Lo que no aparece no se toca."""
    from .geo import norm

    out: dict = {}
    text = f" {norm(message)} "
    for term, geo in _geo_terms(ds):
        if term and f" {term} " in text:
            out.update(geo)
            break
    caida, subida = bool(CAIDA_IN_TEXT.search(message)), bool(SUBIDA_IN_TEXT.search(message))
    if caida != subida:
        out["tipo"] = "caida" if caida else "subida"
    canales = [c for c, rx in CANAL_IN_TEXT.items() if rx.search(message)]
    if canales:
        out["canales"] = canales
    return out


def _where(f: dict) -> str:
    return f["provincia"] or f["ccaa"] or ""


def _fallback_sales(ds: Dataset, session: dict, f: dict, emit: Emit) -> None:
    """Resumen de ventas reales sin IA: KPIs + ranking, con el periodo explícito."""
    s = S.summary(ds, f)
    one = bool(f.get("restaurante"))
    r = {"rows": []} if one else S.ranking(ds, f, sort="ventas", limit=5)
    _remember(session, [f["restaurante"]] if one else [x["restaurante_id"] for x in r["rows"]], f)
    emit("block", {"block": {"type": "sales_kpis", "title": f"Ventas de {f['restaurante']}" if one else "Ventas del periodo",
                             "subtitle": describe(f), "data": s,
                             "filters": f, "key": "fb-sales"}})
    if r["rows"]:
        emit("block", {"block": {"type": "sales_ranking", "title": "Restaurantes con más ventas", "subtitle": describe(f),
                                 "sort": "ventas", "rows": r["rows"], "filters": f, "key": "fb-sales-rank"}})
    if not s["dias"]:
        emit("text", {"delta": f"No hay ventas registradas entre el {f['from']} y el {f['to']} con estos filtros."})
        return
    var = s["variacion"]["ventas"]
    cmp_txt = (f" ({pct(var)} frente al periodo anterior, {s['prevRange']['from']} → {s['prevRange']['to']})"
               if var is not None else " (sin periodo anterior comparable)")
    quien = f"las ventas reales de **{f['restaurante']}**" if one else "las ventas reales"
    donde = "" if one else f", en {num(s['restaurantes'])} restaurantes"
    txt = (f"Entre el {f['from']} y el {f['to']} {quien} suman **{eur(s['ventas'])}**{cmp_txt}, con "
           f"{num(s['tickets'])} tickets y un ticket medio de {num(s['ticket_medio'], 2)} €{donde}. "
           f"El mejor día fue el {s['mejor_dia']['fecha']} ({eur(s['mejor_dia']['ventas'])}).")
    if r["rows"]:
        top = r["rows"][0]
        txt += f"\n\nEl restaurante con más ventas es **{top['restaurante_id']}** ({top.get('provincia') or 'sin provincia'}) con {eur(top['ventas'])}."
    emit("text", {"delta": txt})


def _anomaly_text(d: dict) -> str:
    det = d["detalle"]
    lines = [det["resumen"], "", "**Qué ha pasado**", *[f"- {h}" for h in det["hechos"]], "",
             "**Por qué merece revisión**", *[f"- {c}" for c in det["contexto"]], "",
             f"**Recomendación:** {det['recomendacion']}"]
    return "\n".join(lines)


def _fallback_turn(ds: Dataset, session: dict, dash: dict, message: str, emit: Emit, note: bool) -> None:
    emit("status", {"text": "Consultando datos"})
    if note:
        emit("text", {"delta": "_Asistente en modo básico (no hay modelo de IA configurado): respuesta generada directamente de los datos._\n\n"})
    # Lo que la pregunta pide explícitamente (p. ej. «anomalías de Madrid») manda sobre los filtros del dashboard.
    asked = _filters_in_text(ds, message)
    if asked:
        dash = {**dash, **asked}
    m = ID_IN_TEXT.search(message)
    aid = None
    if m:
        aid = f"{m.group(1).upper()}-{m.group(2).lower()}-{m.group(3)}"
    elif session.get("focus") and re.search(r"\b(esta|esa|la anomal|explica|por qu)", message, re.I):
        aid = session["focus"]
    if aid:
        out = run_tool(ds, "detalle_anomalia", {"id": aid, "mostrar": True}, session)
        if out.get("block"):
            emit("block", {"block": {**out["block"], "key": f"fb-{aid}"}})
            emit("focus", {"id": aid})
            emit("text", {"delta": _anomaly_text(Q.detail(ds, aid))})
            return
        emit("text", {"delta": out["data"].get("error", "No encuentro esa anomalía.")})
        return
    r = REST_IN_TEXT.search(message)
    if r:
        rid = f"R{r.group(1).zfill(3)}"
        info = Q.restaurant_info(ds, rid)
        if not info:
            emit("text", {"delta": f"No existe el restaurante {rid}."})
            return
        f = {**dash, "restaurante": rid}
        if SALES_IN_TEXT.search(message) or not ds.anomalies:
            _fallback_sales(ds, session, f, emit)
            return
        out = run_tool(ds, "listar_anomalias", {"filtros": {"desde": f["from"], "hasta": f["to"], "restaurante": rid},
                                                "limite": 10, "mostrar": True}, session)
        s = Q.stats(Q.filter_anomalies(ds, f))
        emit("text", {"delta": (
            f"**{rid}** ({info.get('provincia') or 'sin provincia en el maestro'}) tiene **{num(s['total'])} alertas** "
            f"entre el {f['from']} y el {f['to']}: {num(s['caidas'])} caídas y {num(s['subidas'])} subidas, "
            f"con un impacto neto de {eur(s['impacto'], sign=True)}. En todo el histórico suma "
            f"{num(info['anomalias']['total'])} alertas.\n\n")})
        if out.get("block") and s["total"]:
            emit("block", {"block": {**out["block"], "key": f"fb-{rid}"}})
        return
    wants_anomalies = bool(ANOM_IN_TEXT.search(message))
    if SALES_IN_TEXT.search(message) or (not ds.anomalies and not wants_anomalies):
        _fallback_sales(ds, session, dash, emit)
        if not ds.anomalies:
            emit("text", {"delta": "\n\n_" + (_model_note(ds) or "") + "_"})
        return
    if not ds.anomalies:
        emit("text", {"delta": (f"{_model_note(ds)} Mientras tanto puedo responder sobre las ventas reales: "
                                "por ejemplo, «¿qué restaurante ha vendido más este mes?».")})
        return
    s = Q.summary(ds, dash)
    where = _where(dash)
    if where and not s["total"]:
        emit("text", {"delta": f"No hay alertas en **{where}** entre el {dash['from']} y el {dash['to']} con estos filtros."})
        return
    emit("block", {"block": {"type": "kpis", "title": "Resumen de anomalías", "subtitle": describe(dash), "data": s,
                             "filters": dash, "key": "fb-kpis"}})
    t = Q.table(ds, dash, sort="impacto", size=5)
    if t["rows"]:
        emit("block", {"block": {"type": "anomalies", "title": "Las 5 alertas con más impacto", "subtitle": describe(dash),
                                 "rows": t["rows"], "total": t["total"], "filters": dash, "key": "fb-top"}})
    texto = (f"{f'En **{where}**, en' if where else 'En'} el periodo {dash['from']} → {dash['to']} hay **{num(s['total'])} alertas** en "
             f"{num(s['restaurantes'])} restaurantes ({num(s['caidas'])} caídas y {num(s['subidas'])} subidas), "
             f"de las que {num(s['altas'])} son de prioridad alta. Pérdidas estimadas: {eur(s['perdidas'])}; "
             f"ventas por encima de lo esperado: {eur(s['ganancias'], sign=True)}.")
    if t["rows"]:
        top = t["rows"][0]
        texto += (f"\n\nLa de mayor impacto es **{top['restaurante_id']} · {CHANNEL_LABEL[top['canal']]}** el {top['fecha']} "
                  f"({pct(top['desviacion_pct'])}, {eur(top['impacto'], sign=True)}).")
    elif not ds.anomalies:
        texto += "\n\nTodavía no hay alertas cargadas en la ejecución activa del modelo."
    emit("text", {"delta": texto})


# ── POST /api/copilot/chat (JSON) ─────────────────────────────────────
def _context_for(ds: Dataset, req: dict) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    if req.get("episode_id"):
        ep = episode_detail(ds, req["episode_id"])
        if not ep:
            raise NotFound(f"No existe el episodio «{req['episode_id']}».")
        return {"tipo": "episodio", "episodio": {k: v for k, v in ep.items() if k not in ("serie",)}}, warnings
    if req.get("anomaly_id"):
        d = Q.detail(ds, req["anomaly_id"])
        if not d:
            raise NotFound(f"No existe la alerta «{req['anomaly_id']}».")
        return {"tipo": "alerta", "alerta": _slim_detail(d)}, warnings
    f, w = Q.normalize_filters(req.get("filters") or {}, ds)
    warnings += w
    if req.get("restaurante_id"):
        rid = Q.normalize_restaurant_id(req["restaurante_id"])
        info = Q.restaurant_info(ds, rid)
        if not info:
            raise NotFound(f"No existe el restaurante «{req['restaurante_id']}».")
        f = {**f, "restaurante": rid}
        top = Q.table(ds, f, sort="impacto", size=10)["rows"]
        evid = [{**x, "evidencia": ds.anom_by_id[x["id"]].get("evidencia")} for x in top]
        return {"tipo": "restaurante", "restaurante": info, "periodo": describe(f), "alertas_periodo": Q.stats(Q.filter_anomalies(ds, f)),
                "top_alertas": evid, "ventas": _slim_sales(S.summary(ds, f)), "estado_modelo": _model_note(ds)}, warnings
    s = Q.summary(ds, f)
    return {"tipo": "resumen", "periodo": describe(f), "kpis": {k: v for k, v in s.items() if k != "spark"},
            "top_alertas": Q.table(ds, f, sort="impacto", size=10)["rows"],
            "por_canal": Q.by_channel(ds, f), "ventas": _slim_sales(S.summary(ds, f)),
            "ventas_por_canal": S.by_channel(ds, f), "estado_modelo": _model_note(ds)}, warnings


def _slim_sales(s: dict) -> dict:
    return {k: s.get(k) for k in ("from", "to", "ventas", "tickets", "ticket_medio", "venta_media_dia", "restaurantes",
                                  "mejor_dia", "variacion", "prevRange")}


def _fallback_answer(ctx: dict) -> str:
    t = ctx["tipo"]
    if t == "alerta":
        return _anomaly_text(ctx["alerta"])
    if t == "episodio":
        e = ctx["episodio"]
        lineas = [
            f"**Episodio {e['episode_id']}** · {e['restaurante_id']} · {CHANNEL_LABEL.get(e['canal'], e['canal'])}: "
            f"{'caída' if e['tipo'] == 'caida' else 'subida'} de {e['duracion_dias']} día(s), del {e['fecha_inicio']} al {e['fecha_fin']}.",
            f"- Impacto total estimado: {eur(e.get('impacto_total_eur'), sign=True)} · prioridad {e['prioridad']}.",
            f"- Desviación media de ventas: {pct(e.get('desviacion_media_ventas_pct'))}; de tickets: {pct(e.get('desviacion_media_tickets_pct'))}.",
        ]
        d = e.get("descomposicion") or {}
        if d.get("contribucion_tickets_pct") is not None:
            lineas.append(f"- La variación viene en un {pct(d['contribucion_tickets_pct'], sign=False)} del número de tickets y en un "
                          f"{pct(d['contribucion_tm_pct'], sign=False)} del ticket medio.")
        if e.get("explicacion_principal"):
            lineas.append(f"\n{e['explicacion_principal']}")
        if e.get("recomendacion_principal"):
            lineas.append(f"\n**Recomendación:** {e['recomendacion_principal']}")
        return "\n".join(lineas)
    if t == "restaurante":
        r, s, v = ctx["restaurante"], ctx["alertas_periodo"], ctx.get("ventas") or {}
        ventas = (f"**{r['id']}** vendió {eur(v['ventas'])} entre el {v['from']} y el {v['to']} ({num(v['tickets'])} tickets"
                  + (f", {pct(v['variacion']['ventas'])} frente al periodo anterior" if (v.get("variacion") or {}).get("ventas") is not None else "")
                  + "). ") if v.get("ventas") is not None else ""
        if not s["total"] and ctx.get("estado_modelo"):
            return ventas + ctx["estado_modelo"]
        txt = ventas + (f"**{r['id']}** ({r.get('provincia') or 'sin provincia en el maestro'}): {num(s['total'])} alertas en {ctx['periodo']} "
               f"({num(s['caidas'])} caídas, {num(s['subidas'])} subidas), impacto neto {eur(s['impacto'], sign=True)}.")
        if ctx["top_alertas"]:
            a = ctx["top_alertas"][0]
            txt += f"\n\nLa más relevante: {a['fecha']} en {CHANNEL_LABEL[a['canal']].lower()}, {pct(a['desviacion_pct'])} ({eur(a['impacto'], sign=True)}). {a.get('explicacion') or ''}"
        return txt
    k = ctx["kpis"]
    v = ctx.get("ventas") or {}
    ventas = (f"Ventas reales entre el {v['from']} y el {v['to']}: {eur(v['ventas'])} ({num(v['tickets'])} tickets). "
              if v.get("ventas") is not None else "")
    if ctx.get("estado_modelo") and not k["total"]:
        return ventas + ctx["estado_modelo"]
    return ventas + (f"En {ctx['periodo']} hay {num(k['total'])} alertas en {num(k['restaurantes'])} restaurantes "
                     f"({num(k['caidas'])} caídas y {num(k['subidas'])} subidas; {num(k['altas'])} de prioridad alta). "
                     f"Pérdidas estimadas {eur(k['perdidas'])}.")


def copilot_answer(req: dict) -> dict:
    ds = get_dataset()
    ctx, warnings = _context_for(ds, req)
    s = get_settings()
    if not s.ai_configured:
        return {"answer": _fallback_answer(ctx), "source": "fallback", "model": None, "context": ctx,
                "warnings": warnings + ["No hay modelo de IA configurado: respuesta determinista generada a partir de los datos."]}
    session = _session(req.get("conversation_id"))
    prompt = (f"{system_prompt(ds)}\n\nResponde SOLO con la EVIDENCIA que se adjunta en el mensaje del usuario. Si la pregunta no "
              "puede responderse con ella, dilo y sugiere qué consultar.")
    user = f"EVIDENCIA (datos cerrados de la base de datos):\n{_jsonable(ctx)}\n\nPREGUNTA: {req['question']}"
    hist = [m for m in session["messages"] if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)][-6:]
    try:
        r = _llm().chat.completions.create(model=s.ai_model,
                                           messages=[{"role": "system", "content": prompt}, *hist, {"role": "user", "content": user}])
        answer = (r.choices[0].message.content or "").strip() or _fallback_answer(ctx)
        session["messages"] += [{"role": "user", "content": req["question"]}, {"role": "assistant", "content": answer}]
        return {"answer": answer, "source": s.ai_provider, "model": s.ai_model, "context": ctx, "warnings": warnings}
    except Exception as e:  # noqa: BLE001
        log.exception("El modelo de IA falló en /api/copilot/chat")
        return {"answer": _fallback_answer(ctx), "source": "fallback", "model": None, "context": ctx,
                "warnings": warnings + [_error_message(e)]}


__all__ = ["chat_turn", "copilot_answer", "reset_session", "ai_status", "TOOLS", "run_tool", "system_prompt"]
