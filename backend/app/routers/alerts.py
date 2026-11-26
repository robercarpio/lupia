"""Alertas diarias: tabla paginada y detalle completo."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from ..database import NotFound
from ..schemas import PageResponse
from ..services import analytics as Q
from .deps import DatasetDep, FiltersDep

router = APIRouter(prefix="/api", tags=["alertas"])


@router.get("/anomalies", response_model=PageResponse, summary="Alertas diarias filtradas, ordenadas y paginadas")
def anomalies(
    p: FiltersDep,
    q: str = "",
    sort: Literal["impacto", "score", "fecha", "desviacion_pct", "ventas", "restaurante", "prioridad"] = "impacto",
    dir: Literal["asc", "desc"] = "desc",
    page: int = 1,
    size: int = 25,
):
    return Q.table(p.ds, p.filters, q=q, sort=sort, direction=dir, page=page, size=size)


@router.get("/anomalies/{anomaly_id}", summary="Detalle de una alerta: hechos, contexto, serie ±8 semanas, comparativas")
def anomaly_detail(anomaly_id: str, ds: DatasetDep):
    d = Q.detail(ds, anomaly_id)
    if not d:
        raise NotFound("Anomalía no encontrada")
    return d
