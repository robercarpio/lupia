"""Episodios multi-día (v_alert_episodes)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from ..database import NotFound
from ..schemas import PageResponse
from ..services.episodes import episode_detail, list_episodes
from .deps import DatasetDep, FiltersDep

router = APIRouter(prefix="/api", tags=["episodios"])


@router.get("/episodes", response_model=PageResponse, summary="Ranking de episodios con filtros dinámicos")
def episodes(
    p: FiltersDep,
    sort: Literal["impacto", "prioridad", "score", "fecha", "duracion"] = "impacto",
    dir: Literal["asc", "desc"] = "desc",
    page: int = 1,
    size: int = 25,
    min_dias: int = 1,
):
    return list_episodes(p.ds, p.filters, sort=sort, direction=dir, page=page, size=size, min_dias=max(1, min_dias))


@router.get("/episodes/{episode_id}", summary="Detalle de un episodio: días, descomposición, pares, canales y serie")
def episode(episode_id: str, ds: DatasetDep):
    d = episode_detail(ds, episode_id)
    if not d:
        raise NotFound("Episodio no encontrado")
    return d
