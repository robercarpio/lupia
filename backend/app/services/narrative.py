"""Textos de negocio deterministas para cada alerta.

Regla del reto: nunca se atribuye una causa externa (obras, averías, meteorología, huelgas…)
que no esté en la evidencia. Todo lo que se dice aquí sale de cifras de la alerta, del
calendario de su provincia, de los otros canales del restaurante y de sus pares.
"""
from __future__ import annotations

import datetime as dt

CHANNEL_LABEL = {"sala": "Sala", "domicilio": "Domicilio", "autoking": "Autoking"}
DIAS = ["", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
METRICA_LABEL = {"ventas": "ventas", "tickets": "tickets", "ambas": "ventas y tickets", "ticket_medio": "ticket medio"}


def eur(v: float | None, sign: bool = False) -> str:
    if v is None:
        return "—"
    s = f"{abs(round(v)):,}".replace(",", ".")
    pre = "+" if sign and v > 0 else "-" if v < 0 else ""
    return f"{pre}{s} €"


def pct(frac: float | None, sign: bool = True) -> str:
    if frac is None:
        return "—"
    v = round(frac * 100)
    pre = "+" if sign and v > 0 else "-" if v < 0 else ""
    return f"{pre}{abs(v)} %"


def num(v: float | None, d: int = 0) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fecha_larga(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{DIAS[d.isoweekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


def short_explanation(a: dict) -> str:
    """Texto corto para tablas y anomalias.csv."""
    canal = CHANNEL_LABEL.get(a["canal"], a["canal"]).lower()
    if a.get("metrica") == "tickets" and a.get("desviacion_pct") is not None and abs(a["desviacion_pct"]) < 0.2:
        base = f"Tickets {pct(a.get('desviacion_tickets_pct'))} con ventas {pct(a.get('desviacion_pct'))} en {canal}"
    else:
        base = f"Ventas {pct(a.get('desviacion_pct'))} en {canal}"
    extra = []
    if a.get("z"):
        extra.append(f"{num(a['z'], 1)} veces su variabilidad habitual")
    if a.get("tiene_otros_canales") is False:
        extra.append("local monocanal")
    elif a.get("otros_canales_normales") is True:
        extra.append("resto de canales normales")
    elif a.get("otros_canales_normales") is False:
        extra.append("también se desvían otros canales")
    extra.append("sin festivo ni evento en calendario")
    return f"{base} ({', '.join(extra)})"


def recommendation(a: dict) -> str:
    canal = CHANNEL_LABEL.get(a["canal"], a["canal"]).lower()
    alta = a.get("prioridad") == "alta"
    if a["tipo"] == "caida":
        if alta:
            return (f"Revisión prioritaria: confirmar con operaciones si el canal {canal} funcionó con normalidad el "
                    f"{fecha_larga(a['fecha'])} y si las ventas se registraron completas.")
        return f"Revisar con el restaurante si hubo alguna incidencia en el canal {canal} ese día."
    if alta:
        return ("Revisión prioritaria: verificar que las ventas no estén duplicadas y, si son reales, documentar el "
                "motivo para excluir el día del entrenamiento de la previsión.")
    return "Comprobar si las ventas están duplicadas antes de usar el día para entrenar la previsión."


def build_detail(a: dict, ctx: dict) -> dict:
    """{resumen, hechos, contexto, hipotesis, recomendacion} en lenguaje de negocio.

    ctx: calendario (fila de series_summary del día), otros_canales [{canal, desv}], cohorte (fracción),
    zona_mismo_dia (int), historial (int).
    """
    canal = CHANNEL_LABEL.get(a["canal"], a["canal"])
    caida = a["tipo"] == "caida"
    hechos: list[str] = []
    contexto: list[str] = []
    senales: list[str] = []

    hechos.append(f"{canal} de {a['restaurante_id']} vendió {eur(a.get('ventas'))} frente a "
                  f"{eur(a.get('ventas_esperadas'))} esperados ({pct(a.get('desviacion_pct'))}).")
    if a.get("tickets") is not None and a.get("tickets_esperados") is not None:
        hechos.append(f"Tickets: {num(a['tickets'])} frente a {num(a['tickets_esperados'])} esperados "
                      f"({pct(a.get('desviacion_tickets_pct'))}).")
    tm, tme = a.get("ticket_medio"), a.get("ticket_medio_esperado")
    if tm is not None and tme:
        dtm = tm / tme - 1
        if abs(dtm) >= 0.10:
            hechos.append(f"El ticket medio fue de {num(tm, 2)} € frente a {num(tme, 2)} € habituales ({pct(dtm)}).")
    if a.get("z"):
        hechos.append(f"La desviación es {num(a['z'], 1)} veces la variabilidad habitual de este restaurante y canal.")

    cal = ctx.get("calendario")
    d = dt.date.fromisoformat(a["fecha"])
    dia = DIAS[d.isoweekday()]
    if cal is not None:
        motivos = []
        if cal.get("tipo_festivo"):
            motivos.append(f"festivo ({cal['tipo_festivo']})")
        if cal.get("vispera_festivo"):
            motivos.append("víspera de festivo")
        if cal.get("fecha_especial"):
            motivos.append(f"fecha especial ({cal['fecha_especial']})")
        if cal.get("navidad"):
            motivos.append("periodo de Navidad")
        if cal.get("semana_santa"):
            motivos.append("Semana Santa")
        lugar = f" en {a['provincia']}" if a.get("provincia") else ""
        if motivos:
            contexto.append(f"Fue {dia}{lugar}; el calendario marca: {', '.join(motivos)}.")
        else:
            contexto.append(f"Fue {dia}{lugar}; no consta festivo, víspera de festivo ni fecha especial en el calendario.")
        if cal.get("is_systemic_day"):
            contexto.append("Ese día muchos restaurantes se desviaron a la vez (evento general).")
    elif not a.get("provincia"):
        contexto.append(f"Fue {dia}. El restaurante no tiene provincia en el maestro: solo se han podido descartar "
                        "festivos nacionales y fechas especiales.")
    else:
        contexto.append(f"Fue {dia}.")

    coh = ctx.get("cohorte")
    if coh is not None:
        if abs(coh) < 0.08:
            contexto.append(f"El resto de restaurantes de su zona se comportó con normalidad ese día en "
                            f"{canal.lower()} ({pct(coh)}).")
        else:
            contexto.append(f"El conjunto de restaurantes de su zona se movió {pct(coh)} ese día; lo esperado ya "
                            "incorpora ese movimiento.")
    otros = ctx.get("otros_canales") or []
    raros = [o for o in otros if o.get("desv") is not None and abs(o["desv"]) >= 0.25]
    if a.get("tiene_otros_canales") is False:
        contexto.append(f"Este restaurante solo opera el canal {canal.lower()} (no dispone de otros canales activos).")
    elif otros:
        if raros:
            contexto.append("Otros canales del restaurante también se desviaron: " + ", ".join(
                f"{CHANNEL_LABEL.get(o['canal'], o['canal']).lower()} {pct(o['desv'])}" for o in raros) + ".")
        else:
            contexto.append("Los demás canales del restaurante (" + ", ".join(
                CHANNEL_LABEL.get(o["canal"], o["canal"]).lower() for o in otros) + ") estuvieron dentro de lo normal.")
    racha = a.get("racha") or 1
    if racha > 1:
        contexto.append(f"Forma parte de un episodio de {racha} días seguidos con alerta en este canal.")

    # Señales observadas (no son causas): de dónde viene la variación según los datos.
    ct, ctm = a.get("contribucion_tickets_pct"), a.get("contribucion_tm_pct")
    if ct is not None and ctm is not None:
        if abs(ct) >= abs(ctm):
            senales.append(f"La variación se explica sobre todo por el número de tickets ({pct(abs(ct), sign=False)} de "
                           f"la variación) y menos por el ticket medio ({pct(abs(ctm), sign=False)}).")
        else:
            senales.append(f"La variación se explica sobre todo por el ticket medio ({pct(abs(ctm), sign=False)} de "
                           f"la variación) y menos por el número de tickets ({pct(abs(ct), sign=False)}).")
    if otros and not raros:
        senales.append(f"Solo se desvía {canal.lower()}: la señal apunta a algo propio de este canal del restaurante.")
    elif raros and len(raros) == len(otros):
        senales.append("Se desvían todos los canales del restaurante a la vez: la señal apunta al restaurante en su "
                       "conjunto, no a un canal concreto.")
    if coh is not None and abs(coh) < 0.08:
        senales.append("Sus restaurantes comparables no se movieron: no parece un efecto de zona ni de campaña.")
    if racha > 2:
        senales.append("Al prolongarse varios días, se trata de una situación sostenida más que de un hecho puntual.")
    if not senales:
        senales.append("Los datos disponibles no permiten aislar el origen de la desviación.")
    senales.append("Los datos no incluyen la causa real: conviene confirmarla con el restaurante.")

    reco = a.get("recomendacion_revision") or recommendation(a)
    mueve = ("unos tickets un " + pct(abs(a.get("desviacion_tickets_pct") or 0), sign=False) +
             (" inferiores" if (a.get("desviacion_tickets_pct") or 0) < 0 else " superiores")) \
        if a.get("metrica") == "tickets" and abs(a.get("desviacion_pct") or 0) < 0.2 else \
        ("unas ventas un " + pct(abs(a.get("desviacion_pct") or 0), sign=False) +
         (" inferiores" if (a.get("desviacion_pct") or 0) < 0 else " superiores"))
    resumen = a.get("explicacion_negocio") or (
        f"El canal {canal.lower()} de {a['restaurante_id']} registró el {fecha_larga(a['fecha'])} {mueve} a lo "
        f"esperado. El impacto estimado es de {eur(a.get('impacto'), sign=True)}. "
        + ("No se identifica un festivo ni una fecha del calendario que lo explique. " if not (cal and (
            cal.get('tipo_festivo') or cal.get('vispera_festivo') or cal.get('fecha_especial'))) else "")
        + reco
    )
    return {"resumen": resumen, "hechos": hechos, "contexto": contexto, "hipotesis": senales, "recomendacion": reco}


def merge_evidence_detail(base: dict, evidencia: dict | None) -> dict:
    """Si el pipeline dejó textos en evidencia_json, tienen prioridad sobre los generados."""
    if not isinstance(evidencia, dict):
        return base
    out = dict(base)
    for k in ("resumen", "recomendacion"):
        if isinstance(evidencia.get(k), str) and evidencia[k].strip():
            out[k] = evidencia[k]
    for k in ("hechos", "contexto", "hipotesis"):
        if isinstance(evidencia.get(k), list) and evidencia[k]:
            out[k] = [str(x) for x in evidencia[k]]
    return out
