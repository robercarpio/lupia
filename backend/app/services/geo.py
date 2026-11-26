"""Nombres geográficos: la capa Gold guarda identificadores ('pais-vasco', 'la-coruna');
el frontend (mapa y filtros) trabaja con nombres oficiales ('País Vasco', 'A Coruña')."""
from __future__ import annotations

import re
import unicodedata

UNKNOWN = {"", "desconocido", "-", "nan", "none", "null"}

PROVINCIAS = {
    "la-coruna": "A Coruña", "a-coruna": "A Coruña", "alava": "Álava", "albacete": "Albacete", "alicante": "Alicante",
    "almeria": "Almería", "asturias": "Asturias", "avila": "Ávila", "badajoz": "Badajoz", "barcelona": "Barcelona",
    "vizcaya": "Bizkaia", "bizkaia": "Bizkaia", "burgos": "Burgos", "caceres": "Cáceres", "cadiz": "Cádiz",
    "cantabria": "Cantabria", "castellon": "Castellón", "ceuta": "Ceuta", "ciudad-real": "Ciudad Real",
    "cordoba": "Córdoba", "cuenca": "Cuenca", "guipuzcoa": "Gipuzkoa", "gipuzkoa": "Gipuzkoa", "girona": "Girona",
    "granada": "Granada", "guadalajara": "Guadalajara", "huelva": "Huelva", "huesca": "Huesca",
    "baleares": "Illes Balears", "islas-baleares": "Illes Balears", "jaen": "Jaén", "la-rioja": "La Rioja",
    "las-palmas": "Las Palmas", "leon": "León", "lleida": "Lleida", "lugo": "Lugo", "madrid": "Madrid",
    "malaga": "Málaga", "melilla": "Melilla", "murcia": "Murcia", "navarra": "Navarra", "ourense": "Ourense",
    "palencia": "Palencia", "pontevedra": "Pontevedra", "salamanca": "Salamanca",
    "tenerife": "Santa Cruz de Tenerife", "santa-cruz-de-tenerife": "Santa Cruz de Tenerife", "segovia": "Segovia",
    "sevilla": "Sevilla", "soria": "Soria", "tarragona": "Tarragona", "teruel": "Teruel", "toledo": "Toledo",
    "valencia": "Valencia", "valladolid": "Valladolid", "zamora": "Zamora", "zaragoza": "Zaragoza",
}

CCAAS = {
    "andalucia": "Andalucía", "aragon": "Aragón", "asturias": "Asturias", "canarias": "Canarias",
    "cantabria": "Cantabria", "castilla-la-mancha": "Castilla-La Mancha", "castilla-y-leon": "Castilla y León",
    "cataluna": "Cataluña", "ceuta": "Ceuta", "comunidad-de-madrid": "Comunidad de Madrid",
    "comunidad-valenciana": "Comunidad Valenciana", "extremadura": "Extremadura", "galicia": "Galicia",
    "islas-baleares": "Illes Balears", "la-rioja": "La Rioja", "melilla": "Melilla", "navarra": "Navarra",
    "pais-vasco": "País Vasco", "region-de-murcia": "Región de Murcia",
}

ALIASES = {
    "madrid": "comunidad de madrid", "murcia": "region de murcia", "valencia": "comunidad valenciana",
    "catalunya": "cataluna", "euskadi": "pais vasco", "baleares": "illes balears", "islas baleares": "illes balears",
    "vizcaya": "bizkaia", "guipuzcoa": "gipuzkoa", "araba": "alava", "la coruna": "a coruna", "coruna": "a coruna",
    "orense": "ourense", "gerona": "girona", "lerida": "lleida", "castello": "castellon", "alacant": "alicante",
    "tenerife": "santa cruz de tenerife", "principado de asturias": "asturias", "rioja": "la rioja",
    "comunidad foral de navarra": "navarra",
}


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _pretty(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("_", "-").split("-"))


def is_unknown(v: str | None) -> bool:
    return v is None or str(v).strip().lower() in UNKNOWN


def provincia_label(slug: str | None) -> str | None:
    if is_unknown(slug):
        return None
    return PROVINCIAS.get(str(slug).lower(), _pretty(str(slug)))


def ccaa_label(slug: str | None) -> str | None:
    if is_unknown(slug):
        return None
    return CCAAS.get(str(slug).lower(), _pretty(str(slug)))


def clean(v):
    """'desconocido' / '-' / vacío → None (como espera el frontend)."""
    return None if is_unknown(v) else v


def resolve(name: str | None, options: list[str]) -> str | None:
    """Encuentra un nombre de una lista cerrada tolerando tildes, alias, slugs y prefijos."""
    if not name:
        return None
    n = norm(name)
    pairs = [(norm(o), o) for o in options]
    for k, o in pairs:
        if k == n:
            return o
    alias = ALIASES.get(n)
    if alias:
        for k, o in pairs:
            if k == alias or alias in k:
                return o
    part = [o for k, o in pairs if k and (n in k or k in n)]
    if len(part) == 1:
        return part[0]
    for k, o in pairs:
        if k.endswith(n):
            return o
    return None
