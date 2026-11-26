"""Descargas: CSV (entrega oficial y alertas filtradas) e informe PDF."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter
from fastapi.responses import Response

from ..services import analytics as Q
from ..services import repository as repo
from ..services.report_pdf import build_report
from .deps import FiltersDep

router = APIRouter(prefix="/api", tags=["export"])


def _csv(rows: list[dict], cols: list[str], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])
    return Response(buf.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@router.get("/anomalias.csv", summary="Entrega oficial anomalias.csv (v_anomalias_export), lista para validar_entrega.py")
def anomalias_oficial():
    rows = repo.anomalias_export()
    for r in rows:
        r["score"] = f"{float(r['score']):.6f}" if r.get("score") is not None else ""
        r["impacto_estimado_eur"] = f"{float(r['impacto_estimado_eur']):.2f}" if r.get("impacto_estimado_eur") is not None else ""
    return _csv(rows, ["restaurante_id", "canal", "fecha", "tipo", "score", "impacto_estimado_eur", "explicacion"], "anomalias.csv")


@router.get("/export.csv", summary="Alertas filtradas del dashboard (columnas extendidas)")
def export_filtered(p: FiltersDep):
    rows = []
    for a in Q.filter_anomalies(p.ds, p.filters):
        rows.append({**a, "score": f"{a['score']:.4f}"})
    cols = ["restaurante_id", "canal", "fecha", "tipo", "score", "impacto", "ventas", "ventas_esperadas", "tickets",
            "tickets_esperados", "prioridad", "explicacion"]
    return _csv(rows, cols, "anomalias.csv")


@router.get("/informe.pdf", summary="Informe PDF de las alertas filtradas (resumen, desgloses y listado)")
def informe_pdf(p: FiltersDep):
    pdf = build_report(p.ds, p.filters)
    name = f"informe-anomalias-{p.filters['from']}_{p.filters['to']}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
