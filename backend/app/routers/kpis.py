"""KPIs ejecutivos y payload completo del dashboard."""
from __future__ import annotations

from fastapi import APIRouter

from ..database import BadRequest
from ..schemas import DashboardResponse, KpisResponse
from ..services import analytics as Q
from ..services import repository as repo
from ..services import dashboard as D
from .deps import DatasetDep, FiltersDep

router = APIRouter(prefix="/api", tags=["kpis"])


@router.get("/dashboard", response_model=DashboardResponse, summary="KPIs, evolución, canales y mapa para unos filtros")
def dashboard(p: FiltersDep):
    return D.payload(p.ds, p.filters, p.warnings)


@router.get("/kpis", response_model=KpisResponse, summary="KPIs globales (v_kpi_global) + ejecución activa")
def kpis(ds: DatasetDep):
    return {"global_": repo.kpi_global() or {}, "model_run": ds.run}


@router.get("/kpis/summary", summary="KPIs filtrados con comparación frente al periodo anterior")
def kpis_summary(p: FiltersDep):
    return {"filters": p.filters, "warnings": p.warnings, **Q.summary(p.ds, p.filters)}


@router.get("/breakdown/{dimension}", summary="Anomalías agregadas por canal, provincia, comunidad, tipo, día, mes, prioridad…")
def breakdown(dimension: str, p: FiltersDep):
    if dimension not in Q.BREAKDOWN_DIMS:
        raise BadRequest(f"Dimensión no soportada: {dimension}. Usa una de: {', '.join(Q.BREAKDOWN_DIMS)}")
    return {"filters": p.filters, "dimension": dimension, "rows": Q.breakdown(p.ds, p.filters, dimension)}
