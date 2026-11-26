"""Informe PDF de anomalías (GET /api/informe.pdf).

Mismos filtros y mismas cifras que el dashboard: todo sale de analytics (Q). El PDF incluye
resumen, reparto por canal, comunidades y restaurantes con más impacto, las alertas principales
con su explicación y el listado completo (hasta MAX_ROWS) de las alertas filtradas.
"""
from __future__ import annotations

import datetime as dt
import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from . import analytics as Q
from .dataset import Dataset
from .narrative import CHANNEL_LABEL, eur, num, pct

ASSETS = Path(__file__).resolve().parent.parent / "assets"
MAX_ROWS = 1000          # alertas en el listado completo
TOP_EXPLAINED = 15       # alertas principales con su explicación

# Paleta Lupia (la misma del frontend)
NAVY = colors.HexColor("#0a1030")
INK2 = colors.HexColor("#414a6b")
MUTED = colors.HexColor("#828aa6")
LINE = colors.HexColor("#e0e5f2")
FILL = colors.HexColor("#f2f5fc")
BLUE = colors.HexColor("#0079ff")
VIOLET = colors.HexColor("#6f12fe")
DOWN = colors.HexColor("#d63b40")
UP = colors.HexColor("#12a66f")

_S = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=NAVY, spaceAfter=3),
    "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK2, spaceAfter=2),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=NAVY, spaceBefore=12, spaceAfter=6),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=12.5, textColor=NAVY),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=NAVY),
    "kpi_l": ParagraphStyle("kpi_l", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=MUTED),
    "kpi_v": ParagraphStyle("kpi_v", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=NAVY),
    "kpi_s": ParagraphStyle("kpi_s", fontName="Helvetica", fontSize=7.5, leading=10, textColor=INK2),
}
_S["right"] = ParagraphStyle("right", parent=_S["small"], alignment=TA_RIGHT)

# Las fuentes base del PDF (Helvetica) no tienen algunos símbolos: se sustituyen por equivalentes.
_SUBS = {"→": "–", "≥": ">=", "≤": "<=", "−": "-", "…": "...", "✓": "", " ": " ", " ": " "}


def _t(s) -> str:
    s = "" if s is None else str(s)
    for a, b in _SUBS.items():
        s = s.replace(a, b)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(s, style="body") -> Paragraph:
    return Paragraph(_t(s), _S[style])


def _fecha(iso: str | None) -> str:
    if not iso:
        return "—"
    d = dt.date.fromisoformat(iso[:10])
    return d.strftime("%d/%m/%Y")


def _filters_text(f: dict) -> str:
    parts = [f"{_fecha(f['from'])} – {_fecha(f['to'])}"]
    if f.get("restaurante"):
        parts.append(f"Restaurante {f['restaurante']}")
    if f.get("provincia"):
        parts.append(f["provincia"])
    elif f.get("ccaa"):
        parts.append(f["ccaa"])
    if f.get("canales"):
        parts.append(" + ".join(CHANNEL_LABEL[c] for c in f["canales"]))
    if f.get("tipo"):
        parts.append("Solo caídas" if f["tipo"] == "caida" else "Solo subidas")
    if f.get("prioridad"):
        parts.append(f"Prioridad {f['prioridad']}")
    if f.get("tipologia"):
        parts.append(f"Tipología {f['tipologia']}")
    if f.get("ubicacion"):
        parts.append(f"Ubicación {f['ubicacion']}")
    if f.get("minScore"):
        parts.append(f"Score >= {f['minScore']}")
    if f.get("minImpact"):
        parts.append(f"Impacto >= {eur(f['minImpact'])}")
    return " · ".join(parts)


def _table(data: list[list], widths: list[float], num_cols: tuple[int, ...] = (), zebra: bool = True,
           extra: list | None = None) -> Table:
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), NAVY),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for c in num_cols:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    if zebra:
        for r in range(2, len(data), 2):
            style.append(("BACKGROUND", (0, r), (-1, r), FILL))
    t.setStyle(TableStyle(style + (extra or [])))
    return t


def _tone(rows: list[dict], col: int, key: str, offset: int = 1) -> list:
    """Colorea en rojo/verde la columna de impacto según el signo."""
    return [("TEXTCOLOR", (col, i + offset), (col, i + offset), DOWN if (r.get(key) or 0) < 0 else UP)
            for i, r in enumerate(rows)]


def _kpis(s: dict, width: float) -> Table:
    prev = s.get("prev") or {}

    def vs(k):
        if not prev or not prev.get(k):
            return "Sin periodo anterior comparable"
        return f"{pct(s[k] / prev[k] - 1)} vs periodo anterior ({num(prev[k])})"

    cells = [
        ("ALERTAS", num(s["total"]), vs("total"), NAVY),
        ("CAÍDAS / SUBIDAS", f"{num(s['caidas'])} / {num(s['subidas'])}", f"{num(s['altas'])} de prioridad alta", NAVY),
        ("RESTAURANTES AFECTADOS", num(s["restaurantes"]), f"Score medio {num(s['score_medio'], 2)}", NAVY),
        ("PÉRDIDAS ESTIMADAS", eur(s["perdidas"]), "Ventas por debajo de lo esperado", DOWN),
        ("VENTAS POR ENCIMA", eur(s["ganancias"], sign=True), f"Impacto neto {eur(s['impacto'], sign=True)}", UP),
    ]
    row = []
    for label, value, sub, color in cells:
        row.append([Paragraph(_t(label), _S["kpi_l"]),
                    Paragraph(_t(value), ParagraphStyle("v", parent=_S["kpi_v"], textColor=color)),
                    Paragraph(_t(sub), _S["kpi_s"])])
    t = Table([row], colWidths=[width / len(cells)] * len(cells))
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def build_report(ds: Dataset, f: dict) -> bytes:
    buf = io.BytesIO()
    page = landscape(A4)
    margin = 14 * mm
    width = page[0] - 2 * margin
    generated = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    run = ds.run or {}

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(BLUE)
        canvas.rect(0, page[1] - 4, page[0], 4, stroke=0, fill=1)
        canvas.setFillColor(VIOLET)
        canvas.rect(page[0] * 0.62, page[1] - 4, page[0] * 0.38, 4, stroke=0, fill=1)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(margin, 8 * mm, _t(f"Lupia · Informe de anomalías de ventas · {_filters_text(f)}")[:160])
        canvas.drawRightString(page[0] - margin, 8 * mm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=margin, rightMargin=margin, topMargin=12 * mm,
                            bottomMargin=15 * mm, title="Informe de anomalías de ventas", author="Lupia",
                            subject=_t(_filters_text(f)))
    story: list = []

    # Cabecera
    logo = []
    if (ASSETS / "lupia-icono.png").exists():
        logo.append(Image(str(ASSETS / "lupia-icono.png"), width=13 * mm, height=13 * mm * 341 / 410))
    if (ASSETS / "lupia-texto.png").exists():
        logo.append(Image(str(ASSETS / "lupia-texto.png"), width=30 * mm, height=30 * mm * 185 / 712))
    head_right = [_p("Informe de anomalías de ventas", "title"),
                  _p(f"{_filters_text(f)}", "sub"),
                  _p(f"Generado el {generated} · Datos hasta el {_fecha(ds.date_max)} · "
                     f"Ejecución del modelo {run.get('run_id') or '—'}"
                     f"{' (' + run['model_version'] + ')' if run.get('model_version') else ''}", "small")]
    head = Table([[logo, head_right]], colWidths=[48 * mm, width - 48 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (0, 0), 0), ("LEFTPADDING", (1, 0), (1, 0), 12),
                              ("LINEAFTER", (0, 0), (0, 0), 0.6, LINE)]))
    story += [head, Spacer(1, 8 * mm)]

    s = Q.summary(ds, f)
    story += [_p("Resumen", "h2"), _kpis(s, width)]
    if not s["total"]:
        story += [Spacer(1, 6 * mm), _p("No hay alertas con estos filtros en el periodo seleccionado.")]
        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
        return buf.getvalue()

    # Por canal + comunidades, en dos columnas
    ch = Q.by_channel(ds, f)
    ch_data = [["CANAL", "ALERTAS", "% TOTAL", "CAÍDAS", "SUBIDAS", "IMPACTO"]] + [
        [CHANNEL_LABEL[c["canal"]], num(c["total"]), pct(c["share"], sign=False), num(c["caidas"]), num(c["subidas"]),
         eur(c["impacto"], sign=True)] for c in ch]
    half = (width - 8 * mm) / 2
    ch_t = _table(ch_data, [half * w for w in (.24, .15, .15, .14, .14, .18)], (1, 2, 3, 4, 5), extra=_tone(ch, 5, "impacto"))

    geo = [g for g in Q.by_geo(ds, f, "ccaa") if g["nombre"] != "Sin dato de maestro"][:8]
    geo_data = [["COMUNIDAD", "ALERTAS", "CAÍDAS", "SUBIDAS", "LOCALES", "IMPACTO"]] + [
        [g["nombre"], num(g["anomalias"]), num(g["caidas"]), num(g["subidas"]),
         f"{num(g['restaurantes_afectados'])} / {num(g['restaurantes'])}", eur(g["impacto"], sign=True)] for g in geo]
    geo_t = _table(geo_data, [half * w for w in (.3, .12, .12, .12, .14, .2)], (1, 2, 3, 4, 5), extra=_tone(geo, 5, "impacto"))
    two = Table([[[_p("Por canal", "h2"), ch_t], [_p("Comunidades con más alertas", "h2"), geo_t]]],
                colWidths=[half + 4 * mm, half + 4 * mm])
    two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (0, 0), 8 * mm)]))
    story.append(two)

    # Restaurantes con más impacto
    rest = Q.by_restaurant(ds, f, sort="impacto", limit=10)["rows"]
    rest_data = [["RESTAURANTE", "PROVINCIA", "ALERTAS", "CAÍDAS", "SUBIDAS", "CANAL PRINCIPAL", "SCORE MEDIO",
                  "ÚLTIMA", "IMPACTO NETO"]] + [
        [r["restaurante_id"], r.get("provincia") or "Sin maestro", num(r["anomalias"]), num(r["caidas"]),
         num(r["subidas"]), CHANNEL_LABEL.get(r["canal_principal"], r["canal_principal"]), num(r["score_medio"], 2),
         _fecha(r["ultima"]), eur(r["impacto"], sign=True)] for r in rest]
    story += [KeepTogether([_p("Restaurantes con más impacto", "h2"),
                            _table(rest_data, [width * w for w in (.1, .16, .09, .09, .09, .13, .1, .1, .14)],
                                   (2, 3, 4, 6, 7, 8), extra=_tone(rest, 8, "impacto"))])]

    # Alertas principales con su explicación
    lst = sorted(Q.filter_anomalies(ds, f), key=lambda a: -abs(a["impacto"]))
    story += [PageBreak(), _p(f"Alertas principales ({min(TOP_EXPLAINED, len(lst))} con más impacto)", "h2")]
    top_data = [["ALERTA", "FECHA", "CANAL", "TIPO", "REALES / ESPERADAS", "IMPACTO", "EXPLICACIÓN"]]
    for a in lst[:TOP_EXPLAINED]:
        top_data.append([
            Paragraph(f"<b>{_t(a['restaurante_id'])}</b><br/><font color='#828aa6'>{_t(a.get('provincia') or 'Sin maestro')}</font>", _S["cell"]),
            _fecha(a["fecha"]), CHANNEL_LABEL[a["canal"]], "Caída" if a["tipo"] == "caida" else "Subida",
            Paragraph(f"{_t(eur(a['ventas']))}<br/><font color='#828aa6'>{_t(eur(a['ventas_esperadas']))} ({_t(pct(a.get('desviacion_pct')))})</font>", _S["cell"]),
            eur(a["impacto"], sign=True), Paragraph(_t(a.get("explicacion") or "—"), _S["cell"])])
    story.append(_table(top_data, [width * w for w in (.09, .08, .07, .06, .12, .09, .49)], (5,),
                        extra=_tone(lst[:TOP_EXPLAINED], 5, "impacto") + [("VALIGN", (0, 0), (-1, -1), "TOP")]))

    # Listado completo
    rows = lst[:MAX_ROWS]
    story += [PageBreak(), _p(f"Todas las alertas del filtro ({num(len(lst))})", "h2")]
    if len(lst) > MAX_ROWS:
        story.append(_p(f"Se muestran las {num(MAX_ROWS)} de mayor impacto. El listado completo está en la exportación CSV.", "small"))
    full = [["RESTAURANTE", "PROVINCIA", "FECHA", "CANAL", "TIPO", "PRIORIDAD", "REALES", "ESPERADAS", "DESV.", "IMPACTO", "SCORE"]]
    for a in rows:
        full.append([a["restaurante_id"], a.get("provincia") or "Sin maestro", _fecha(a["fecha"]), CHANNEL_LABEL[a["canal"]],
                     "Caída" if a["tipo"] == "caida" else "Subida", (a.get("prioridad") or "—").capitalize(),
                     eur(a["ventas"]), eur(a["ventas_esperadas"]), pct(a.get("desviacion_pct")),
                     eur(a["impacto"], sign=True), num(a["score"], 2)])
    story.append(_table(full, [width * w for w in (.09, .14, .08, .08, .07, .08, .1, .1, .07, .11, .08)],
                        (6, 7, 8, 9, 10), extra=_tone(rows, 9, "impacto")))
    story += [Spacer(1, 5 * mm), _p("Cifras calculadas por el modelo de detección sobre los datos de Databricks. "
                                    "Impacto = ventas reales - ventas esperadas.", "small")]

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
