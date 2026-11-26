"""Payload completo del dashboard y precalentado de cachés.

El dashboard necesita 4 consultas a Databricks (serie diaria de ambos periodos, recuento de restaurantes,
ventas por canal y ventas por comunidad/provincia). Se lanzan a la vez y después se monta la respuesta
con los resultados ya cacheados; lo relativo a anomalías se calcula en memoria.
"""
from __future__ import annotations

import logging
import time

from ..database import parallel
from . import analytics as Q
from . import repository as repo
from . import sales as S
from .dataset import Dataset

log = logging.getLogger("rbe.dashboard")


def payload(ds: Dataset, f: dict, warnings: list[str] | None = None) -> dict:
    pr = Q.prev_range(ds, f)
    sf = Q.sql_filter(ds, f)
    # 1) Consultas a Databricks en paralelo (quedan en caché).
    _, counts, channels, geo = parallel(
        lambda: Q.daily_rows(ds, f),
        lambda: repo.sales_restaurant_counts(f["from"], f["to"], sf, prev_from=pr and pr["from"]),
        lambda: S.by_channel(ds, f),
        lambda: S.geo_both(ds, f),
    )
    # 2) Montaje (aciertos de caché + cálculos en memoria).
    return {
        "filters": f,
        "warnings": warnings or [],
        "kpis": Q.summary(ds, f),
        "timeline": Q.timeline(ds, f),
        "channels": Q.by_channel(ds, f),
        "geo": {"provincia": Q.by_geo(ds, f, "provincia"), "ccaa": Q.by_geo(ds, f, "ccaa")},
        "sales": {"kpis": S.summary(ds, f), "channels": channels, "geo": geo},
        "modelStatus": ds.model_status,
    }


def warm(ds: Dataset) -> None:
    """Precalcula lo que pide la página al abrirse (filtros por defecto: últimos 90 días)."""
    t = time.perf_counter()
    f, _ = Q.normalize_filters({}, ds)
    payload(ds, f)
    parallel(lambda: S.ranking(ds, f, sort="ventas", limit=10), lambda: S.ranking(ds, f, sort="ventas", limit=30),
             lambda: S.ranking(ds, f, sort="variacion_asc", limit=10))
    log.info("Caché precalentada en %.1f s", time.perf_counter() - t)
