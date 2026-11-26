"""API REST del reto RBE · IndesIAhack 2026 (FastAPI sobre Databricks Apps).

Arranque:
  Databricks Apps → app.yaml (uvicorn app.main:app --port 8000)
  Local           → uvicorn app.main:app --reload --port 8787   (puerto del proxy de Vite)
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .database import DataError, background, close
from .services.dataset import warm_up
from .routers import alerts, chat, episodes, export, kpis, meta, restaurants, sales, series

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s · %(message)s")
log = logging.getLogger("rbe")


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    log.info("Esquema: %s · warehouse: %s · IA: %s", s.rbe_schema, s.warehouse_id or "SIN CONFIGURAR",
             f"{s.ai_provider} ({s.ai_model})" if s.ai_configured else "no (modo determinista)")
    if os.getenv("WARM_UP_ON_START", "1") != "0":
        # En segundo plano: el servidor acepta peticiones ya; la primera visita encuentra la caché caliente
        # (y, si el warehouse serverless estaba parado, empieza a arrancarlo antes de que llegue nadie).
        background(warm_up)
    yield
    close()


app = FastAPI(
    title="RBE · Anomalías de ventas",
    version="1.0.0",
    description="Backend del reto IndesIAhack 2026 · RBE: anomalías y episodios priorizados por impacto, "
                "con explicaciones de negocio y copiloto sobre Azure OpenAI.",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_settings.cors_origins) or ["*"],
    allow_credentials="*" not in _settings.cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=2048)


# ── Errores: siempre {"error": "...", "code": ...} (lo que lee el frontend) ──
def _err(status: int, message: str, code=None) -> JSONResponse:
    return JSONResponse({"error": message, "code": code if code is not None else status}, status_code=status,
                        headers={"Cache-Control": "no-store"})


@app.exception_handler(DataError)
async def data_error(_: Request, e: DataError):
    if e.status >= 500 and e.code == "DB_ERROR":
        log.error("Error de datos: %s", e)
    return _err(e.status, str(e), e.code)


@app.exception_handler(RequestValidationError)
async def validation(_: Request, e: RequestValidationError):
    first = e.errors()[0] if e.errors() else {}
    where = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "query"))
    return _err(400, f"Parámetro no válido{f' «{where}»' if where else ''}: {first.get('msg', 'valor incorrecto')}", "BAD_REQUEST")


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, e: StarletteHTTPException):
    return _err(e.status_code, "Ruta no encontrada" if e.status_code == 404 else str(e.detail))


@app.exception_handler(Exception)
async def unexpected(_: Request, e: Exception):
    log.exception("Error no controlado")
    return _err(500, f"Error interno: {type(e).__name__}", "INTERNAL")


@app.middleware("http")
async def no_store_api(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/api/") and "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/assets/") and resp.status_code == 200:
        # Ficheros del build con hash en el nombre: el navegador puede guardarlos indefinidamente.
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


# ── Límite de peticiones por IP (para despliegues públicos sin login, p. ej. Render) ──
# Solo se activa con RATE_LIMIT=1. Ventana fija de 60 s, en memoria (una instancia).
if os.getenv("RATE_LIMIT", "0") == "1":
    import time
    from collections import defaultdict

    _LIMITS = {"chat": int(os.getenv("RATE_LIMIT_CHAT", "10")), "api": int(os.getenv("RATE_LIMIT_API", "120"))}
    _hits: dict = defaultdict(lambda: [0.0, 0])

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        fwd = request.headers.get("x-forwarded-for", "")
        ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "anon")
        kind = "chat" if path == "/api/chat" else "api"
        now = time.monotonic()
        if len(_hits) > 50_000:  # evita que crezca sin límite
            _hits.clear()
        win = _hits[(ip, kind)]
        if now - win[0] >= 60:
            win[0], win[1] = now, 0
        win[1] += 1
        if win[1] > _LIMITS[kind]:
            msg = "Demasiadas preguntas seguidas. Espera un minuto." if kind == "chat" else "Demasiadas peticiones. Espera un momento."
            r = _err(429, msg, "RATE_LIMIT")
            r.headers["Retry-After"] = str(int(60 - (now - win[0])) + 1)
            return r
        return await call_next(request)


for r in (meta, kpis, sales, alerts, episodes, series, restaurants, export, chat):
    app.include_router(r.router)


@app.get("/health", include_in_schema=False)
def liveness():
    """Comprobación ligera (no toca el warehouse)."""
    return {"status": "ok"}


@app.api_route("/api/{path:path}", methods=["GET", "POST"], include_in_schema=False)
def api_not_found(path: str):
    return _err(404, "Ruta no encontrada", 404)


# ── Frontend compilado (opcional): misma app, mismo origen, sin CORS ──
_static = _settings.static_dir
if _static and not os.path.isabs(_static):
    _static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _static)
if _static and os.path.isfile(os.path.join(_static, "index.html")):
    if os.path.isdir(os.path.join(_static, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(_static, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        file = os.path.normpath(os.path.join(_static, path))
        if path and file.startswith(_static) and os.path.isfile(file):
            return FileResponse(file)
        return FileResponse(os.path.join(_static, "index.html"))
