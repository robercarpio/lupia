"""Series temporales: real vs esperado, bandas P05/P95 y marcadores."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ..database import BadRequest, NotFound
from ..services import analytics as Q
from ..services.episodes import series as series_of
from ..services.geo import resolve
from .deps import DatasetDep, FiltersDep

router = APIRouter(prefix="/api", tags=["series"])


@router.get("/series", summary="Serie diaria de un restaurante (y canal): real, esperado, P05/P95, anomalías y festivos")
def series(
    ds: DatasetDep,
    restaurante: str,
    canal: str | None = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
):
    rid = Q.normalize_restaurant_id(restaurante)
    if rid not in ds.rest_by_id:
        raise NotFound(f"No existe el restaurante «{restaurante}».")
    if canal and canal not in Q.CHANNELS:
        raise BadRequest(f"Canal no válido: {canal}")
    d_to = to if Q.is_iso(to) else ds.date_max
    d_from = from_ if Q.is_iso(from_) else Q.add_days(d_to, -364)
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    return series_of(ds, rid, canal, max(d_from, ds.date_min), min(d_to, ds.date_max))


@router.get("/timeline", summary="Ventas reales vs esperadas agregadas para unos filtros (diaria o semanal)")
def timeline(p: FiltersDep):
    return {"filters": p.filters, **Q.timeline(p.ds, p.filters)}


@router.get("/day-context/{fecha}", summary="Contexto de un día: exclusiones por motivo y alertas")
def day_context(fecha: str, ds: DatasetDep, provincia: str | None = None):
    if not Q.is_iso(fecha):
        raise BadRequest("Fecha no válida (AAAA-MM-DD).")
    prov = resolve(provincia, [p["provincia"] for p in ds.provinces]) if provincia else None
    return Q.day_context(ds, fecha, prov)
