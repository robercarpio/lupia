"""Salud, metadatos, opciones de filtros, ejecuciones del modelo y caché."""
from __future__ import annotations

from fastapi import APIRouter

from ..database import DataError, clear_cache, warehouse_state
from ..schemas import CacheClearResponse, FilterOptions, HealthResponse, ModelRunsResponse
from ..services import analytics as Q
from ..services import repository as repo
from ..services.copilot import ai_status
from ..config import get_settings
from ..services.dataset import get_dataset, reset_state
from .deps import DatasetDep

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health", response_model=HealthResponse, summary="Estado de la app, del warehouse y de la IA")
def health():
    state = warehouse_state()
    try:
        ds = get_dataset()
        data = {"source": "real", "dateMax": ds.date_max, "alertas": len(ds.anomalies),
                "run_id": (ds.run or {}).get("run_id"), "loadedAt": ds.loaded_at}
        ok = True
    except DataError as e:
        data = {"error": str(e), "code": e.code}
        ok = False
    return {"ok": ok, "data": data, "ai": ai_status(), "warehouse": {"state": state}}


@router.get("/meta", summary="Dimensiones, rango de fechas, configuración del detector y motivos de exclusión")
def meta(ds: DatasetDep):
    return Q.dimensions(ds)


@router.get("/filters", response_model=FilterOptions, summary="Opciones para los selectores de filtros")
def filter_options(ds: DatasetDep):
    d = Q.dimensions(ds)
    return {
        "dateMin": ds.date_min, "dateMax": ds.date_max, "evalStart": ds.config["evalStart"],
        "defaultRange": d["defaultRange"], "canales": Q.CHANNELS, "tipos": ["caida", "subida"],
        "prioridades": [{"value": "alta", "nivel": "P1"}, {"value": "media", "nivel": "P2"}, {"value": "baja", "nivel": "P3"}],
        "ccaas": ds.ccaas, "provincias": ds.provinces, "tipologias": d["tipologias"], "ubicaciones": d["ubicaciones"],
        "restaurantes": d["restaurantIds"],
    }


@router.get("/model-runs", response_model=ModelRunsResponse, summary="Ejecuciones del modelo (trazabilidad)")
def model_runs(limit: int = 20):
    runs = repo.list_runs(max(1, min(200, limit)))
    active = next((r for r in runs if r.get("is_active")), None) or repo.active_run()
    return {"active": active, "runs": runs}


@router.get("/model-runs/active", summary="Ejecución activa del modelo")
def model_run_active():
    return repo.active_run()


@router.get("/status", summary="Versión de los datos del modelo: el frontend la consulta para refrescarse solo")
def data_status(ds: DatasetDep):
    """Barato: usa la huella de alerts_prioritized/model_runs (consultada como mucho cada ANOMALY_REFRESH_SECONDS)."""
    fp = ds.fingerprint or {}
    version = "|".join(str(fp.get(k)) for k in ("alertas", "ultima_alerta", "ejecuciones", "ultima_ejecucion"))
    return {"version": f"{version}|{ds.loaded_at}", "modelStatus": ds.model_status,
            "refreshSeconds": get_settings().anomaly_refresh}


@router.post("/cache/clear", response_model=CacheClearResponse, summary="Invalida la caché tras ejecutar el modelo")
def cache_clear():
    reset_state()
    return {"ok": True, "cleared": clear_cache()}
