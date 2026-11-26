"""Snapshot en memoria de la capa Gold: restaurantes, alertas de la ejecución activa y metadatos.

Las alertas son pocas (decenas de miles como mucho), así que se cargan una vez (caché TTL) y los
filtros/agregados del dashboard se calculan en memoria: la navegación es instantánea y todas
las vistas (KPIs, tabla, ranking, mapa, copiloto) usan exactamente las mismas cifras.
Las series diarias (1,4 M filas) se consultan siempre en el SQL Warehouse.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings
from ..database import DataError, background, clear_cache, parallel
from . import repository as repo
from .geo import ccaa_label, clean, is_unknown, provincia_label
from .narrative import short_explanation

log = logging.getLogger("rbe.dataset")

PRIORIDAD = {"P1": "alta", "P2": "media", "P3": "baja"}
PRIORIDAD_INV = {v: k for k, v in PRIORIDAD.items()}

# La configuración del detector la publica el modelo externo en model_runs.params_json. Aquí no se
# inventan umbrales: sin ejecución registrada solo se conoce el inicio del periodo con datos.
DEFAULT_CONFIG: dict[str, Any] = {"evalStart": None, "knownNeutralDays": []}

REASONS = ["normal", "festivo", "vispera_festivo", "fecha_especial", "navidad", "semana_santa", "apertura", "cierre",
           "reforma", "canal_pequeno", "dia_neutro", "sin_historico", "fuera_periodo"]


class NoData(DataError):
    status = 503
    code = "NO_DATA"


def frac(v: float | None) -> float | None:
    """Columna *_pct de la base de datos → fracción (−0,452) como usa el frontend."""
    if v is None:
        return None
    return round(v / 100.0, 6) if get_settings().pct_units == "percent" else v


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round(a / b - 1, 6)


def _camel(k: str) -> str:
    head, *rest = k.split("_")
    return head + "".join(w.capitalize() for w in rest)


def anomaly_id(restaurante_id: str, canal: str, fecha: str) -> str:
    return f"{restaurante_id}-{canal}-{fecha}"


@dataclass
class Dataset:
    restaurants: list[dict]
    rest_by_id: dict[str, dict]
    anomalies: list[dict]
    anom_by_id: dict[str, dict]
    run: dict | None
    date_min: str
    date_max: str
    filas: int
    filas_con_baseline: int
    config: dict
    reason_counts: dict[str, int]
    ccaas: list[str]
    provinces: list[dict]
    ccaa_slug: dict[str, str] = field(default_factory=dict)
    prov_slug: dict[str, str] = field(default_factory=dict)
    loaded_at: str = ""
    fingerprint: dict = field(default_factory=dict)

    @property
    def model_status(self) -> dict:
        """Estado de las tablas que rellena el modelo externo (para estados vacíos informativos)."""
        if self.anomalies:
            estado = "ok"
        elif self.run:
            estado = "sin_alertas"   # el modelo se ejecutó pero no hay alertas
        else:
            estado = "pendiente"     # el modelo todavía no ha escrito resultados
        baseline = ("completo" if self.filas and self.filas_con_baseline >= self.filas
                    else "parcial" if self.filas_con_baseline else "pendiente")
        return {"anomalias": estado, "alertas": len(self.anomalies), "ejecucion": (self.run or {}).get("run_id"),
                "baseline": baseline, "filas_con_baseline": self.filas_con_baseline, "filas": self.filas,
                "comprobado": self.loaded_at}


def _restaurant(r: dict) -> dict:
    prov = provincia_label(r.get("provincia")) or provincia_label(r.get("provincia_inferida"))
    maestro = not r.get("is_orphan_master") and not is_unknown(r.get("provincia"))
    autoking = "si" if r.get("tiene_autoking") else "no"
    return {
        "id": r["restaurante_id"],
        "ccaa": ccaa_label(r.get("comunidad_autonoma")),
        "provincia": prov,
        "provincia_inferida": not maestro and prov is not None,
        "tipologia": clean(r.get("tipologia")),
        "tamano": clean(r.get("tamano")),
        "ubicacion": clean(r.get("ubicacion")),
        "imagen": clean(r.get("imagen")),
        "terraza": clean(r.get("terraza")),
        "autoking": autoking,
        "domicilio": "si" if r.get("tiene_domicilio") else "no",
        "distancia_reparto": clean(r.get("distancia_reparto")),
        "estado": clean(r.get("estado")),
        "primera_venta": r.get("primera_venta"),
        "ultima_venta": r.get("ultima_venta"),
        "mediana_ventas_diaria_eur": r.get("mediana_ventas_diaria_eur"),
        "total_alertas": r.get("total_alertas") or 0,
        "marca": r.get("marca"),
        "maestro": maestro,
        "_ccaa_slug": None if is_unknown(r.get("comunidad_autonoma")) else r.get("comunidad_autonoma"),
        "_prov_slug": None if is_unknown(r.get("provincia")) else r.get("provincia"),
    }


def to_anomaly(a: dict, rest: dict | None) -> dict:
    """Fila de alerts_prioritized → objeto «anomalía» del contrato del frontend (+ campos extra)."""
    rid, canal, fecha = a["restaurante_id"], a["canal"], a["fecha"]
    ventas, esp = a.get("ventas_reales"), a.get("ventas_esperadas")
    tickets, tesp = a.get("tickets_reales"), a.get("tickets_esperados")
    impacto = a.get("impacto_estimado_eur")
    if impacto is None and ventas is not None and esp is not None:
        impacto = round(ventas - esp, 2)
    dpct = frac(a.get("desviacion_ventas_pct"))
    if dpct is None:
        dpct = _ratio(ventas, esp)
    dtpct = frac(a.get("desviacion_tickets_pct"))
    if dtpct is None:
        dtpct = _ratio(tickets, tesp)
    zs = [abs(x) for x in (a.get("z_ventas"), a.get("z_tickets"), a.get("z_ticket_medio")) if x is not None]
    z = abs(a["z_max"]) if a.get("z_max") is not None else (max(zs) if zs else None)
    evid = a.get("evidencia_json") if isinstance(a.get("evidencia_json"), dict) else {}
    otros = evid.get("otros_canales") if isinstance(evid.get("otros_canales"), list) else None
    prov = (rest or {}).get("provincia") or provincia_label(a.get("provincia"))
    ccaa = (rest or {}).get("ccaa") or ccaa_label(a.get("comunidad_autonoma"))
    out = {
        "id": anomaly_id(rid, canal, fecha),
        "alert_id": a.get("alert_id"),
        "restaurante_id": rid,
        "fecha": fecha,
        "canal": canal,
        "tipo": a.get("tipo"),
        "metrica": a.get("metrica_principal") or "ventas",
        "ventas": ventas,
        "ventas_esperadas": esp,
        "desviacion": round(ventas - esp, 2) if ventas is not None and esp is not None else impacto,
        "desviacion_pct": dpct,
        "tickets": tickets,
        "tickets_esperados": tesp,
        "desviacion_tickets_pct": dtpct,
        "ticket_medio": a.get("ticket_medio_real"),
        "ticket_medio_esperado": a.get("ticket_medio_esperado"),
        "impacto": impacto or 0.0,
        "impacto_abs": a.get("impacto_abs_eur") if a.get("impacto_abs_eur") is not None else abs(impacto or 0.0),
        "z": z,
        "z_ventas": a.get("z_ventas"),
        "z_tickets": a.get("z_tickets"),
        "z_ticket_medio": a.get("z_ticket_medio"),
        "z_peer_diff": a.get("z_peer_diff"),
        "z_cross_channel": a.get("z_cross_channel"),
        "score": a.get("score") or 0.0,
        "priority_score": a.get("priority_score"),
        "prioridad": PRIORIDAD.get(a.get("prioridad_nivel") or "", "baja"),
        "prioridad_nivel": a.get("prioridad_nivel"),
        "provincia": prov,
        "ccaa": ccaa,
        "tipologia": clean(a.get("tipologia")) or (rest or {}).get("tipologia"),
        "ubicacion": clean(a.get("ubicacion")) or (rest or {}).get("ubicacion"),
        "dia_semana": dt.date.fromisoformat(fecha).isoweekday(),
        "racha": int(a.get("episode_days") or 1),
        "episode_id": a.get("episode_id"),
        "episode_start_date": a.get("episode_start_date"),
        "episode_end_date": a.get("episode_end_date"),
        "episode_days": a.get("episode_days"),
        "episode_day_index": a.get("episode_day_index"),
        "episode_total_impact_eur": a.get("episode_total_impact_eur"),
        "contribucion_tickets_pct": frac(a.get("contribucion_tickets_pct")),
        "contribucion_tm_pct": frac(a.get("contribucion_tm_pct")),
        "cohorte": frac(a.get("peer_delta_pct")),
        "otros_canales_normales": a.get("otros_canales_normales"),
        "otros_canales": otros,
        "explicacion_negocio": a.get("explicacion_negocio"),
        "recomendacion_revision": a.get("recomendacion_revision"),
        "evidencia": evid,
        "llm_cached": a.get("llm_cached"),
        "run_id": a.get("run_id"),
    }
    out["explicacion"] = a.get("explicacion_negocio") or short_explanation(out)
    return out


def _run_and_alerts() -> tuple[dict | None, list[dict]]:
    run = repo.active_run()
    return run, repo.alerts(run.get("run_id") if run else None)


def _build(fingerprint: dict | None = None) -> Dataset:
    # Tres bloques independientes en paralelo: series (rango + motivos), maestro y ejecución + alertas.
    (bounds, reasons), rest_rows, (run, alert_rows) = parallel(repo.series_overview, repo.restaurants, _run_and_alerts)
    if not bounds or not bounds.get("date_max"):
        raise NoData(f"No hay datos en {repo.T('series_summary')}. Carga las series antes de usar el dashboard.")
    rests = [_restaurant(r) for r in rest_rows]
    rest_by_id = {r["id"]: r for r in rests}

    anomalies: dict[str, dict] = {}
    for a in alert_rows:
        if not a.get("restaurante_id") or not a.get("canal") or not a.get("fecha") or a.get("tipo") not in ("caida", "subida"):
            continue
        x = to_anomaly(a, rest_by_id.get(a["restaurante_id"]))
        prev = anomalies.get(x["id"])
        if prev is None or (x["score"], x["alert_id"] or 0) > (prev["score"], prev["alert_id"] or 0):
            anomalies[x["id"]] = x
    anoms = sorted(anomalies.values(), key=lambda x: (-x["score"], -abs(x["impacto"])))

    config = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_CONFIG.items()}
    params = (run or {}).get("params_json") or {}
    if isinstance(params, dict):
        for k, v in params.items():
            if v is not None and k not in ("raw", "valor"):
                config[_camel(k)] = v
        config["params"] = params
    config["evalStart"] = (run or {}).get("eval_start_date") or config.get("evalStart") or bounds["date_min"]

    rc = {k: 0 for k in REASONS}
    for k, v in reasons.items():
        rc[k] = rc.get(k, 0) + v

    ccaa_slug = {r["ccaa"]: r["_ccaa_slug"] for r in rests if r["ccaa"] and r["_ccaa_slug"]}
    prov_slug = {r["provincia"]: r["_prov_slug"] for r in rests if r["provincia"] and r["_prov_slug"]}
    provinces = sorted(
        {(r["provincia"], r["ccaa"]) for r in rests if r["provincia"] and r["_prov_slug"]},
        key=lambda p: p[0],
    )
    return Dataset(
        restaurants=rests,
        rest_by_id=rest_by_id,
        anomalies=anoms,
        anom_by_id={a["id"]: a for a in anoms},
        run=run,
        date_min=bounds["date_min"],
        date_max=bounds["date_max"],
        filas=int(bounds.get("filas") or 0),
        filas_con_baseline=int(bounds.get("filas_con_baseline") or 0),
        config=config,
        reason_counts=rc,
        ccaas=sorted(ccaa_slug),
        provinces=[{"provincia": p, "ccaa": c} for p, c in provinces],
        ccaa_slug=ccaa_slug,
        prov_slug=prov_slug,
        loaded_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        fingerprint=fingerprint or {},
    )


_lock = threading.Lock()
_state: dict[str, Any] = {"checked": 0.0, "fingerprint": None, "ds": None, "refreshing": False, "sync": False}


def _refresh() -> None:
    """Comprueba la huella del modelo y, si ha cambiado, reconstruye el snapshot e invalida las cachés."""
    try:
        fp = repo.anomaly_fingerprint()
        ds = _state["ds"]
        if ds is None or ds.fingerprint != fp:
            new = _build(fp)
            clear_cache()
            _state.update(ds=new, fingerprint=fp)
            log.info("Datos del modelo actualizados: %s alertas", len(new.anomalies))
            if not _state["sync"]:
                from .dashboard import warm  # import diferido: dashboard depende de este módulo

                background(lambda: warm(new))
    finally:
        _state.update(checked=time.monotonic(), refreshing=False)


def get_dataset() -> Dataset:
    """Snapshot vigente (restaurantes + alertas de la ejecución activa + metadatos).

    - La primera vez se construye en la petición (o en el arranque, ver warm_up).
    - Después nunca bloquea: cada ANOMALY_REFRESH_SECONDS, la siguiente petición lanza en segundo plano
      una comprobación de la huella de alerts_prioritized/model_runs. Si el modelo externo ha escrito
      datos, se reconstruye y se sustituye el snapshot (sin desplegar ni tocar código). Como solo se
      comprueba cuando hay peticiones, el warehouse puede apagarse cuando nadie usa la app.
    """
    ds = _state["ds"]
    if ds is None:
        with _lock:
            if _state["ds"] is None:
                fp = repo.anomaly_fingerprint()
                _state.update(ds=_build(fp), fingerprint=fp, checked=time.monotonic())
            return _state["ds"]
    if time.monotonic() - _state["checked"] >= get_settings().anomaly_refresh and not _state["refreshing"]:
        with _lock:
            if not _state["refreshing"]:
                _state["refreshing"] = True
                if _state["sync"]:
                    _refresh()
                else:
                    background(_refresh)
    return _state["ds"]


def warm_up() -> None:
    """Arranque: abre conexiones, carga el snapshot y precalcula el dashboard por defecto."""
    from ..database import warehouse_state
    from .dashboard import warm

    warm(get_dataset())
    warehouse_state()  # lo consulta /api/health al abrir la página


def reset_state() -> None:
    _state.update(checked=0.0, fingerprint=None, ds=None, refreshing=False)


def public_restaurant(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}
