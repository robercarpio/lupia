"""Todas las consultas SQL del backend, en un solo sitio.

Cada valor que llega del usuario va como parámetro (:nombre). Los fragmentos dinámicos
(listas IN, filtros opcionales) se construyen solo con nombres de parámetro generados aquí.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings
from ..database import query, query_one

CHANNELS = ("sala", "domicilio", "autoking")


def T(name: str) -> str:
    return get_settings().table(name)


# Motivo por el que una fila no se evalúa (claves que entiende el frontend).
MOTIVO_SQL = """
CASE
  WHEN s.is_systemic_day THEN 'dia_neutro'
  WHEN s.is_eligible_for_alert AND (s.exclusion_reason IS NULL OR s.exclusion_reason = '') THEN 'normal'
  WHEN s.exclusion_reason IS NULL OR s.exclusion_reason = '' THEN 'sin_historico'
  ELSE lower(s.exclusion_reason)
END
"""


@dataclass
class SqlFilter:
    """Filtro de series (restaurante / geografía / canal) traducido a SQL parametrizado."""

    restaurante: str = ""
    restaurantes: list[str] = field(default_factory=list)
    ccaa_slug: str = ""
    provincia_slug: str = ""
    canales: list[str] = field(default_factory=list)
    tipologia: str = ""
    ubicacion: str = ""

    def where(self, params: dict[str, Any]) -> str:
        parts = []
        if self.restaurante:
            params["f_rid"] = self.restaurante
            parts.append("s.restaurante_id = :f_rid")
        if self.restaurantes:
            names = []
            for i, rid in enumerate(self.restaurantes[:20]):
                params[f"f_r{i}"] = rid
                names.append(f":f_r{i}")
            parts.append(f"s.restaurante_id IN ({', '.join(names)})")
        if self.ccaa_slug:
            params["f_ccaa"] = self.ccaa_slug
            parts.append("r.comunidad_autonoma = :f_ccaa")
        if self.provincia_slug:
            params["f_prov"] = self.provincia_slug
            parts.append("r.provincia = :f_prov")
        if self.tipologia:
            params["f_tip"] = self.tipologia
            parts.append("r.tipologia = :f_tip")
        if self.ubicacion:
            params["f_ubi"] = self.ubicacion
            parts.append("r.ubicacion = :f_ubi")
        canales = [c for c in self.canales if c in CHANNELS]
        if canales and len(canales) < len(CHANNELS):
            names = []
            for i, c in enumerate(canales):
                params[f"f_c{i}"] = c
                names.append(f":f_c{i}")
            parts.append(f"s.canal IN ({', '.join(names)})")
        return "".join(f" AND {p}" for p in parts)

    @property
    def needs_join(self) -> bool:
        return bool(self.ccaa_slug or self.provincia_slug or self.tipologia or self.ubicacion)


# ── model_runs ────────────────────────────────────────────────────────
RUN_COLS = """run_id, model_version, is_active, created_at, eval_start_date, eval_end_date, total_candidate_rows,
total_alerts, total_caidas, total_subidas, f1_synthetic, precision_synthetic, recall_synthetic,
precision_at_100_synthetic, params_json"""


def active_run() -> dict | None:
    return query_one(
        f"SELECT {RUN_COLS} FROM {T('model_runs')} ORDER BY is_active DESC, created_at DESC NULLS LAST LIMIT 1",
        cache=False,
    )


def list_runs(limit: int = 20) -> list[dict]:
    return query(f"SELECT {RUN_COLS} FROM {T('model_runs')} ORDER BY created_at DESC NULLS LAST LIMIT :lim", {"lim": int(limit)})


# ── restaurants_dim ───────────────────────────────────────────────────
def restaurants() -> list[dict]:
    return query(
        f"""SELECT restaurante_id, marca, pais, comunidad_autonoma, provincia, provincia_inferida, is_orphan_master,
        tipologia, tamano, ubicacion, imagen, terraza, tiene_autoking, tiene_domicilio, distancia_reparto, estado,
        primera_venta, ultima_venta, mediana_ventas_diaria_eur, total_alertas
        FROM {T('restaurants_dim')} ORDER BY restaurante_id""",
        cache=False,
    )


# ── alerts_prioritized ────────────────────────────────────────────────
ALERT_COLS = """alert_id, restaurante_id, canal, fecha, tipo, score, episode_id, episode_start_date, episode_end_date,
episode_days, episode_day_index, episode_total_impact_eur, comunidad_autonoma, provincia, tipologia, ubicacion,
priority_score, prioridad_nivel, metrica_principal, ventas_reales, ventas_esperadas, impacto_estimado_eur,
impacto_abs_eur, desviacion_ventas_pct, tickets_reales, tickets_esperados, desviacion_tickets_pct, ticket_medio_real,
ticket_medio_esperado, contribucion_tickets_pct, contribucion_tm_pct, z_ventas, z_tickets, z_ticket_medio, z_max,
z_peer_diff, peer_delta_pct, z_cross_channel, otros_canales_normales, explicacion_negocio, recomendacion_revision,
evidencia_json, llm_cached, run_id, created_at"""


def alerts(run_id: str | None) -> list[dict]:
    if run_id:
        rows = query(f"SELECT {ALERT_COLS} FROM {T('alerts_prioritized')} WHERE run_id = :run", {"run": run_id}, cache=False)
        if rows:
            return rows
    # Sin ejecución registrada (o sin alertas de esa ejecución): todas las alertas.
    return query(f"SELECT {ALERT_COLS} FROM {T('alerts_prioritized')}", cache=False)


def alerts_by_episode(episode_id: str) -> list[dict]:
    return query(
        f"SELECT {ALERT_COLS} FROM {T('alerts_prioritized')} WHERE episode_id = :ep ORDER BY fecha",
        {"ep": episode_id},
    )


# ── series_summary ────────────────────────────────────────────────────
def series_overview() -> tuple[dict | None, dict[str, int]]:
    """Rango de fechas, filas, filas con referencia del modelo y motivos de exclusión en UNA pasada."""
    rows = query(
        f"""SELECT {MOTIVO_SQL} AS motivo, count(*) AS n, min(s.fecha) AS date_min, max(s.fecha) AS date_max,
        count(s.ventas_esperadas) AS filas_con_baseline FROM {T('series_summary')} s GROUP BY 1""",
        cache=False,
    )
    if not rows:
        return None, {}
    bounds = {"date_min": min(r["date_min"] for r in rows), "date_max": max(r["date_max"] for r in rows),
              "filas": sum(int(r["n"]) for r in rows), "filas_con_baseline": sum(int(r["filas_con_baseline"]) for r in rows)}
    return bounds, {r["motivo"]: int(r["n"]) for r in rows}


def timeline_daily(date_from: str, date_to: str, f: SqlFilter) -> list[dict]:
    """Ventas agregadas por día. `ventas`/`esperadas` solo cuentan los días con baseline."""
    params: dict[str, Any] = {"d_from": date_from, "d_to": date_to}
    where = f.where(params)
    join = f"JOIN {T('restaurants_dim')} r ON r.restaurante_id = s.restaurante_id" if f.needs_join else ""
    return query(
        f"""SELECT s.fecha,
          sum(s.ventas) AS ventas_totales,
          sum(s.tickets) AS tickets_totales,
          sum(CASE WHEN s.ventas_esperadas IS NOT NULL THEN s.ventas END) AS ventas,
          sum(s.ventas_esperadas) AS esperadas,
          count(*) AS filas,
          count(s.ventas_esperadas) AS filas_con_baseline
        FROM {T('series_summary')} s {join}
        WHERE s.fecha BETWEEN :d_from AND :d_to{where}
        GROUP BY s.fecha ORDER BY s.fecha""",
        params,
    )


SERIES_COLS = f"""s.restaurante_id, s.canal, s.fecha, s.ventas, s.tickets, s.ticket_medio, s.ventas_esperadas, s.ventas_p05,
s.ventas_p95, s.tickets_esperados, s.tickets_p05, s.tickets_p95, s.ticket_medio_esperado, s.impacto_estimado_eur,
s.desviacion_ventas_pct, s.desviacion_tickets_pct, s.z_ventas, s.z_tickets, s.z_ticket_medio, s.z_peer_diff,
s.z_cross_channel, s.dia_semana, s.tipo_festivo, s.vispera_festivo, s.fecha_especial, s.navidad, s.semana_santa,
s.is_eligible_for_alert, s.exclusion_reason, s.is_systemic_day, s.is_anomaly, s.anomaly_tipo, {MOTIVO_SQL} AS motivo"""


def series_rows(restaurante: str, canal: str | None, date_from: str, date_to: str) -> list[dict]:
    params: dict[str, Any] = {"rid": restaurante, "d_from": date_from, "d_to": date_to}
    canal_sql = ""
    if canal:
        params["canal"] = canal
        canal_sql = " AND s.canal = :canal"
    return query(
        f"""SELECT {SERIES_COLS} FROM {T('series_summary')} s
        WHERE s.restaurante_id = :rid AND s.fecha BETWEEN :d_from AND :d_to{canal_sql}
        ORDER BY s.canal, s.fecha""",
        params,
    )


def series_day(restaurante: str, fecha: str) -> list[dict]:
    """Todos los canales de un restaurante en un día (comparativa entre canales)."""
    return query(
        f"SELECT {SERIES_COLS} FROM {T('series_summary')} s WHERE s.restaurante_id = :rid AND s.fecha = :d ORDER BY s.canal",
        {"rid": restaurante, "d": fecha},
    )


def peers_day(fecha: str, canal: str, ccaa_slug: str | None, exclude: str) -> dict | None:
    """Movimiento agregado del resto de restaurantes (misma comunidad o España) ese día y canal."""
    params: dict[str, Any] = {"d": fecha, "canal": canal, "rid": exclude}
    geo = ""
    if ccaa_slug:
        params["ccaa"] = ccaa_slug
        geo = " AND r.comunidad_autonoma = :ccaa"
    return query_one(
        f"""SELECT count(*) AS restaurantes, sum(s.ventas) AS ventas, sum(s.ventas_esperadas) AS esperadas,
          count_if(s.is_anomaly) AS con_anomalia
        FROM {T('series_summary')} s JOIN {T('restaurants_dim')} r ON r.restaurante_id = s.restaurante_id
        WHERE s.fecha = :d AND s.canal = :canal AND s.restaurante_id <> :rid AND s.ventas_esperadas IS NOT NULL{geo}""",
        params,
    )


def day_reason_counts(fecha: str) -> dict[str, int]:
    rows = query(
        f"SELECT {MOTIVO_SQL} AS motivo, count(*) AS n FROM {T('series_summary')} s WHERE s.fecha = :d GROUP BY 1",
        {"d": fecha},
    )
    return {r["motivo"]: int(r["n"]) for r in rows}


# ── Ventas reales (series_summary: ventas/tickets diarios por restaurante y canal) ──
# Todas las agregaciones se calculan en Databricks; al backend solo llegan filas agregadas.
SALES_DIMS = {
    "canal": ("s.canal", False),
    "restaurante": ("s.restaurante_id", False),
    "comunidad": ("r.comunidad_autonoma", True),
    "provincia": ("r.provincia", True),
    "tipologia": ("r.tipologia", True),
    "ubicacion": ("r.ubicacion", True),
    "dia_semana": ("s.dia_semana", False),
    "mes": ("date_format(s.fecha, 'yyyy-MM')", False),
}
SALES_SORT = {
    "ventas": "ventas DESC",
    "ventas_asc": "ventas ASC",
    "tickets": "tickets DESC",
    "ticket_medio": "ventas / nullif(tickets, 0) DESC",
    "variacion": "ventas / nullif(ventas_prev, 0) DESC",
    "variacion_asc": "ventas / nullif(ventas_prev, 0) ASC",
    "clave": "clave ASC",
}


def sales_by(dimension: str, date_from: str, date_to: str, f: SqlFilter, *, prev_from: str | None = None,
             sort: str = "ventas", limit: int = 50) -> list[dict]:
    """Ventas y tickets agrupados por una dimensión, con el periodo anterior (si se pide) en la misma consulta.

    Cada fila: clave, ventas, tickets, restaurantes, dias, filas, ventas_prev, tickets_prev, dias_prev y
    total_grupos (nº de grupos con ventas antes del LIMIT: evita una consulta de recuento aparte).
    """
    col, needs_r = SALES_DIMS[dimension]
    params: dict[str, Any] = {"d_from": date_from, "d_to": date_to, "lo": prev_from or date_from, "lim": int(limit)}
    where = f.where(params)
    join = (f"JOIN {T('restaurants_dim')} r ON r.restaurante_id = s.restaurante_id"
            if needs_r or f.needs_join else "")
    cur = "s.fecha >= :d_from"
    prev = "s.fecha < :d_from"
    return query(
        f"""SELECT *, count(*) OVER () AS total_grupos FROM (
          SELECT {col} AS clave,
            sum(CASE WHEN {cur} THEN s.ventas END) AS ventas,
            sum(CASE WHEN {cur} THEN s.tickets END) AS tickets,
            count(DISTINCT CASE WHEN {cur} THEN s.restaurante_id END) AS restaurantes,
            count(DISTINCT CASE WHEN {cur} THEN s.fecha END) AS dias,
            count(CASE WHEN {cur} THEN 1 END) AS filas,
            sum(CASE WHEN {prev} THEN s.ventas END) AS ventas_prev,
            sum(CASE WHEN {prev} THEN s.tickets END) AS tickets_prev,
            count(DISTINCT CASE WHEN {prev} THEN s.fecha END) AS dias_prev
          FROM {T('series_summary')} s {join}
          WHERE s.fecha BETWEEN :lo AND :d_to{where}
          GROUP BY 1
        ) WHERE ventas IS NOT NULL
        ORDER BY {SALES_SORT.get(sort, SALES_SORT['ventas'])} NULLS LAST, clave
        LIMIT :lim""",
        params,
    )


def sales_restaurant_counts(date_from: str, date_to: str, f: SqlFilter, prev_from: str | None = None) -> dict:
    """Restaurantes con ventas en el periodo y en el anterior, en una sola consulta."""
    params: dict[str, Any] = {"d_from": date_from, "d_to": date_to, "lo": prev_from or date_from}
    where = f.where(params)
    join = f"JOIN {T('restaurants_dim')} r ON r.restaurante_id = s.restaurante_id" if f.needs_join else ""
    row = query_one(
        f"""SELECT count(DISTINCT CASE WHEN s.fecha >= :d_from THEN s.restaurante_id END) AS actual,
          count(DISTINCT CASE WHEN s.fecha < :d_from THEN s.restaurante_id END) AS anterior
        FROM {T('series_summary')} s {join}
        WHERE s.fecha BETWEEN :lo AND :d_to{where}""",
        params,
    ) or {}
    return {"actual": int(row.get("actual") or 0), "anterior": int(row.get("anterior") or 0)}


def sales_geo(date_from: str, date_to: str, f: SqlFilter, prev_from: str | None = None) -> list[dict]:
    """Ventas por comunidad y provincia a la vez (el mapa y la lista de comunidades salen de aquí)."""
    params: dict[str, Any] = {"d_from": date_from, "d_to": date_to, "lo": prev_from or date_from}
    where = f.where(params)
    return query(
        f"""SELECT * FROM (
          SELECT r.comunidad_autonoma AS ccaa, r.provincia AS clave,
            sum(CASE WHEN s.fecha >= :d_from THEN s.ventas END) AS ventas,
            sum(CASE WHEN s.fecha >= :d_from THEN s.tickets END) AS tickets,
            count(DISTINCT CASE WHEN s.fecha >= :d_from THEN s.restaurante_id END) AS restaurantes,
            count(DISTINCT CASE WHEN s.fecha >= :d_from THEN s.fecha END) AS dias,
            count(CASE WHEN s.fecha >= :d_from THEN 1 END) AS filas,
            sum(CASE WHEN s.fecha < :d_from THEN s.ventas END) AS ventas_prev,
            sum(CASE WHEN s.fecha < :d_from THEN s.tickets END) AS tickets_prev,
            count(DISTINCT CASE WHEN s.fecha < :d_from THEN s.fecha END) AS dias_prev
          FROM {T('series_summary')} s JOIN {T('restaurants_dim')} r ON r.restaurante_id = s.restaurante_id
          WHERE s.fecha BETWEEN :lo AND :d_to{where}
          GROUP BY 1, 2
        ) WHERE ventas IS NOT NULL""",
        params,
    )


def sales_groups(dimension: str, date_from: str, date_to: str, f: SqlFilter) -> int:
    """Número de grupos con ventas (p. ej. restaurantes con ventas en el periodo)."""
    col, needs_r = SALES_DIMS[dimension]
    params: dict[str, Any] = {"d_from": date_from, "d_to": date_to}
    where = f.where(params)
    join = f"JOIN {T('restaurants_dim')} r ON r.restaurante_id = s.restaurante_id" if needs_r or f.needs_join else ""
    row = query_one(
        f"""SELECT count(DISTINCT {col}) AS n FROM {T('series_summary')} s {join}
        WHERE s.fecha BETWEEN :d_from AND :d_to{where}""",
        params,
    )
    return int((row or {}).get("n") or 0)


# ── Huella de las tablas del modelo (detección de datos nuevos) ───────
def anomaly_fingerprint() -> dict:
    """Consulta ligera (metadatos de Delta) para saber si el modelo externo ha escrito resultados nuevos."""
    return query_one(
        f"""SELECT (SELECT count(*) FROM {T('alerts_prioritized')}) AS alertas,
          (SELECT max(created_at) FROM {T('alerts_prioritized')}) AS ultima_alerta,
          (SELECT count(*) FROM {T('model_runs')}) AS ejecuciones,
          (SELECT max(created_at) FROM {T('model_runs')}) AS ultima_ejecucion""",
        cache=False,
    ) or {}


# ── Vistas ────────────────────────────────────────────────────────────
def kpi_global() -> dict | None:
    return query_one(f"SELECT * FROM {T('v_kpi_global')}")


EPISODE_SORT = {
    "impacto": "impacto_abs_total_eur",
    "prioridad": "max_priority_score",
    "score": "max_score",
    "fecha": "fecha_inicio",
    "duracion": "duracion_dias",
}


def episodes(
    *,
    date_from: str,
    date_to: str,
    restaurante: str = "",
    canales: list[str] | None = None,
    tipo: str = "",
    prioridad_nivel: str = "",
    ccaa_slug: str = "",
    provincia_slug: str = "",
    tipologia: str = "",
    ubicacion: str = "",
    min_dias: int = 1,
    sort: str = "impacto",
    direction: str = "desc",
    limit: int = 25,
    offset: int = 0,
) -> tuple[int, list[dict]]:
    params: dict[str, Any] = {"d_from": date_from, "d_to": date_to, "min_dias": int(min_dias)}
    w = ["fecha_fin >= :d_from", "fecha_inicio <= :d_to", "duracion_dias >= :min_dias"]
    for col, key, val in (
        ("restaurante_id", "rid", restaurante),
        ("tipo", "tipo", tipo),
        ("prioridad_nivel", "prio", prioridad_nivel),
        ("comunidad_autonoma", "ccaa", ccaa_slug),
        ("provincia", "prov", provincia_slug),
        ("tipologia", "tip", tipologia),
        ("ubicacion", "ubi", ubicacion),
    ):
        if val:
            params[key] = val
            w.append(f"{col} = :{key}")
    chans = [c for c in (canales or []) if c in CHANNELS]
    if chans and len(chans) < len(CHANNELS):
        names = []
        for i, c in enumerate(chans):
            params[f"c{i}"] = c
            names.append(f":c{i}")
        w.append(f"canal IN ({', '.join(names)})")
    where = " AND ".join(w)
    order = EPISODE_SORT.get(sort, "impacto_abs_total_eur")
    direction = "ASC" if direction == "asc" else "DESC"
    total = query_one(f"SELECT count(*) AS n FROM {T('v_alert_episodes')} WHERE {where}", params)
    params_page = {**params, "lim": int(limit), "off": int(offset)}
    rows = query(
        f"""SELECT * FROM {T('v_alert_episodes')} WHERE {where}
        ORDER BY {order} {direction} NULLS LAST, max_score DESC, episode_id LIMIT :lim OFFSET :off""",
        params_page,
    )
    return int((total or {}).get("n") or 0), rows


def episode(episode_id: str) -> dict | None:
    return query_one(f"SELECT * FROM {T('v_alert_episodes')} WHERE episode_id = :ep", {"ep": episode_id})


def anomalias_export() -> list[dict]:
    return query(
        f"""SELECT restaurante_id, canal, fecha, tipo, score, impacto_estimado_eur, explicacion
        FROM {T('v_anomalias_export')}
        ORDER BY score DESC, abs(impacto_estimado_eur) DESC, restaurante_id, canal, fecha""",
        cache=False,
    )
