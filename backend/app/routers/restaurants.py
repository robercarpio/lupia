"""Restaurantes: ranking por anomalías, catálogo y ficha."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from ..database import NotFound
from ..schemas import RankingResponse
from ..services import analytics as Q
from ..services import sales as S
from ..services.dataset import public_restaurant
from .deps import DatasetDep, FiltersDep

router = APIRouter(prefix="/api", tags=["restaurantes"])


@router.get("/restaurants", response_model=RankingResponse, summary="Ranking de restaurantes con anomalías")
def ranking(p: FiltersDep, sort: Literal["anomalias", "impacto", "score"] = "anomalias", limit: int = 15):
    return Q.by_restaurant(p.ds, p.filters, sort=sort, limit=limit)


@router.get("/restaurants/catalog", summary="Catálogo completo de restaurantes (maestro)")
def catalog(ds: DatasetDep, ccaa: str | None = None, provincia: str | None = None, q: str = ""):
    rows = [public_restaurant(r) for r in ds.restaurants]
    if ccaa:
        rows = [r for r in rows if r["ccaa"] == ccaa]
    if provincia:
        rows = [r for r in rows if r["provincia"] == provincia]
    if q.strip():
        qq = q.strip().upper()
        rows = [r for r in rows if qq in r["id"] or qq in (r["provincia"] or "").upper()]
    return {"total": len(rows), "rows": rows}


@router.get("/restaurants/{restaurante_id}", summary="Ficha de un restaurante: maestro, anomalías y ventas del periodo")
def restaurant(restaurante_id: str, p: FiltersDep):
    rid = Q.normalize_restaurant_id(restaurante_id)
    info = Q.restaurant_info(p.ds, rid)
    if not info:
        raise NotFound("Restaurante no encontrado")
    f = {**p.filters, "restaurante": rid}
    return {**info, "periodo": {"from": f["from"], "to": f["to"]},
            "ventas": S.summary(p.ds, f), "ventas_por_canal": S.by_channel(p.ds, f)}
