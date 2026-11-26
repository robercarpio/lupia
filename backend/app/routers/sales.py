"""Ventas reales: KPIs, ranking, desgloses y comparativas (series_summary)."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from ..database import BadRequest, NotFound
from ..services import analytics as Q
from ..services import sales as S
from .deps import FiltersDep

router = APIRouter(prefix="/api/sales", tags=["ventas"])

SortSales = Literal["ventas", "ventas_asc", "tickets", "ticket_medio", "variacion", "variacion_asc"]


@router.get("/summary", summary="KPIs de ventas del periodo filtrado frente al periodo anterior de igual duración")
def sales_summary(p: FiltersDep):
    return {"filters": p.filters, "warnings": p.warnings, **S.summary(p.ds, p.filters)}


@router.get("/channels", summary="Ventas, tickets y cuota por canal")
def sales_channels(p: FiltersDep):
    return {"filters": p.filters, "rows": S.by_channel(p.ds, p.filters)}


@router.get("/ranking", summary="Ranking de restaurantes por ventas, tickets, ticket medio o variación")
def sales_ranking(p: FiltersDep, sort: SortSales = "ventas", limit: Annotated[int, Query(ge=1, le=200)] = 15):
    return {"filters": p.filters, **S.ranking(p.ds, p.filters, sort=sort, limit=limit)}


@router.get("/breakdown/{dimension}", summary="Ventas agregadas por canal, comunidad, provincia, tipología, ubicación, día o mes")
def sales_breakdown(dimension: str, p: FiltersDep, sort: SortSales = "ventas"):
    if dimension not in S.BREAKDOWN_DIMS:
        raise BadRequest(f"Dimensión no soportada: {dimension}. Usa una de: {', '.join(S.BREAKDOWN_DIMS)}")
    return {"filters": p.filters, "dimension": dimension, "rows": S.breakdown(p.ds, p.filters, dimension, sort=sort)}


@router.get("/compare", summary="Compara de 2 a 10 restaurantes en el periodo filtrado")
def sales_compare(p: FiltersDep, ids: Annotated[list[str], Query(min_length=1, max_length=10)]):
    norm = [Q.normalize_restaurant_id(i) for i in ids]
    missing = [i for i in norm if i not in p.ds.rest_by_id]
    if missing:
        raise NotFound(f"No existen los restaurantes: {', '.join(missing)}")
    return {"filters": p.filters, **S.compare_restaurants(p.ds, p.filters, norm)}


@router.get("/compare-periods", summary="Compara las ventas de dos periodos con los mismos filtros")
def sales_compare_periods(p: FiltersDep, a_from: str, a_to: str, b_from: str, b_to: str):
    for v in (a_from, a_to, b_from, b_to):
        if not Q.is_iso(v):
            raise BadRequest("Fechas no válidas (AAAA-MM-DD).")
    clamp = lambda d: min(max(d, p.ds.date_min), p.ds.date_max)  # noqa: E731
    pa = {"from": clamp(min(a_from, a_to)), "to": clamp(max(a_from, a_to))}
    pb = {"from": clamp(min(b_from, b_to)), "to": clamp(max(b_from, b_to))}
    return {"filters": p.filters, **S.compare_periods(p.ds, p.filters, pa, pb)}
