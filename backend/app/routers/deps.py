"""Dependencias comunes: dataset y filtros.

Los filtros se aceptan de dos formas (combinables; los parámetros sueltos tienen prioridad):
- ?f={"from":"2026-07-01","to":"2026-09-30","canales":["sala"],...}  ← contrato del frontend
- ?from=2026-07-01&to=2026-09-30&canal=sala&canal=domicilio&tipo=caida&prioridad=alta&ccaa=...&provincia=...
  &tipologia=fs&ubicacion=mall&restaurante=R001&min_score=0.8&min_impact=500
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import Depends, Query

from ..services.analytics import normalize_filters
from ..services.dataset import Dataset, get_dataset


def dataset() -> Dataset:
    return get_dataset()


DatasetDep = Annotated[Dataset, Depends(dataset)]


class ParsedFilters:
    def __init__(self, filters: dict, warnings: list[str], ds: Dataset):
        self.filters = filters
        self.warnings = warnings
        self.ds = ds


def filters(
    ds: DatasetDep,
    f: Annotated[str | None, Query(description="Filtros en JSON (contrato del frontend)")] = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    canal: Annotated[list[str] | None, Query()] = None,
    tipo: str | None = None,
    prioridad: str | None = None,
    ccaa: str | None = None,
    provincia: str | None = None,
    tipologia: str | None = None,
    ubicacion: str | None = None,
    restaurante: str | None = None,
    min_score: float | None = None,
    min_impact: float | None = None,
) -> ParsedFilters:
    raw: dict = {}
    if f:
        try:
            parsed = json.loads(f)
            if isinstance(parsed, dict):
                raw = parsed
        except ValueError:
            raw = {}
    extra = {"from": from_, "to": to, "canales": canal, "tipo": tipo, "prioridad": prioridad, "ccaa": ccaa,
             "provincia": provincia, "tipologia": tipologia, "ubicacion": ubicacion, "restaurante": restaurante,
             "minScore": min_score, "minImpact": min_impact}
    raw.update({k: v for k, v in extra.items() if v not in (None, [], "")})
    flt, warnings = normalize_filters(raw, ds)
    return ParsedFilters(flt, warnings, ds)


FiltersDep = Annotated[ParsedFilters, Depends(filters)]
