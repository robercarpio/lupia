"""Acceso al SQL Warehouse de Azure Databricks.

- Autenticación nativa con databricks.sdk.core.Config: dentro de Databricks Apps usa el
  Service Principal de la app (OAuth M2M); en local, el perfil de ~/.databrickscfg. Sin PAT.
- Todas las consultas son parametrizadas (marcadores :nombre). Los únicos identificadores
  interpolados son las tablas del esquema, validado en config.py.
- Conversión de tipos: Decimal → float, date/datetime → ISO, *_json → dict.
- Caché TTL en memoria por (sql, parámetros) + pool pequeño de conexiones reutilizables.
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import logging
import os
import queue
import threading
import time
from typing import Any, Iterable

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from cachetools import LRUCache, TTLCache

from .config import get_settings

log = logging.getLogger("rbe.database")

JSON_COLUMNS = {"evidencia_json", "params_json"}


class DataError(Exception):
    """Error base de la capa de datos."""

    status = 500
    code = "DB_ERROR"


class WarehouseUnavailable(DataError):
    status = 503
    code = "WAREHOUSE_STARTING"


class NotConfigured(DataError):
    status = 503
    code = "NOT_CONFIGURED"


class NotFound(DataError):
    status = 404
    code = "NOT_FOUND"


class BadRequest(DataError):
    status = 400
    code = "BAD_REQUEST"


# ── Conversión de tipos ───────────────────────────────────────────────
def convert_value(name: str, v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.datetime):
        return v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if name in JSON_COLUMNS and isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, (dict, list)) else {"valor": parsed}
        except (ValueError, TypeError):
            return {"raw": v}
    return v


def convert_rows(columns: list[str], rows: Iterable[Iterable[Any]]) -> list[dict[str, Any]]:
    return [{c: convert_value(c, v) for c, v in zip(columns, row)} for row in rows]


def _param(v: Any) -> Any:
    """Normaliza parámetros para el conector (fechas ISO → date)."""
    if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-":
        try:
            return dt.date.fromisoformat(v)
        except ValueError:
            return v
    return v


# ── Conexiones ────────────────────────────────────────────────────────
class _Pool:
    def __init__(self) -> None:
        self._idle: "queue.LifoQueue[Any]" = queue.LifoQueue()
        self._lock = threading.Lock()
        self._cfg = None

    def _config(self):
        if self._cfg is None:
            from databricks.sdk.core import Config

            s = get_settings()
            token = os.getenv("DATABRICKS_TOKEN", "").strip()
            try:
                if token:
                    # Token personal del equipo (solo en el .env local). Se fija el método para que no
                    # se mezcle con el perfil de ~/.databrickscfg.
                    if not s.host:
                        raise NotConfigured("DATABRICKS_TOKEN está definido pero falta DATABRICKS_HOST.")
                    self._cfg = Config(host=s.host, token=token, auth_type="pat")
                else:
                    # Databricks Apps (Service Principal), DATABRICKS_HOST + OAuth o perfil del CLI.
                    self._cfg = Config(host=s.host or None)
            except NotConfigured:
                raise
            except Exception as e:  # noqa: BLE001 - nunca se incluye el token en el mensaje
                raise NotConfigured(f"No se puede autenticar con Databricks ({type(e).__name__}). Revisa DATABRICKS_HOST, "
                                    "DATABRICKS_TOKEN o el perfil de ~/.databrickscfg.") from None
        return self._cfg

    def _new(self):
        from databricks import sql

        s = get_settings()
        if not s.http_path:
            raise NotConfigured("Falta DATABRICKS_HTTP_PATH (o DATABRICKS_WAREHOUSE_ID) en el entorno.")
        cfg = self._config()
        host = (cfg.host or "").replace("https://", "").replace("http://", "").rstrip("/")
        if not host:
            raise NotConfigured("Falta DATABRICKS_HOST (o un perfil en ~/.databrickscfg).")
        return sql.connect(
            server_hostname=host,
            http_path=s.http_path,
            credentials_provider=lambda: cfg.authenticate,
            user_agent_entry="rbe-anomalias-backend",
            # Límite de coste: ninguna consulta puede ocupar el warehouse más de N segundos.
            session_configuration={"STATEMENT_TIMEOUT": str(s.statement_timeout)},
            _socket_timeout=s.statement_timeout + 30,
        )

    def acquire(self):
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            return self._new()

    def release(self, conn, broken: bool = False) -> None:
        if broken or self._idle.qsize() >= get_settings().pool_size:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - cierre best-effort
                pass
            return
        self._idle.put(conn)

    def close_all(self) -> None:
        while True:
            try:
                self._idle.get_nowait().close()
            except queue.Empty:
                break
            except Exception:  # noqa: BLE001
                pass

    def workspace_client(self):
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient(config=self._config())


_pool = _Pool()


def llm_credentials() -> tuple[str, str]:
    """(URL base OpenAI-compatible de Model Serving, token vigente) con la misma autenticación que el warehouse.

    El token se pide en cada llamada: el SDK lo cachea y lo renueva antes de que caduque (OAuth M2M).
    """
    cfg = _pool._config()
    host = (cfg.host or "").rstrip("/")
    if not host:
        raise NotConfigured("Falta DATABRICKS_HOST (o un perfil en ~/.databrickscfg).")
    auth = cfg.authenticate().get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise NotConfigured("No se ha podido obtener un token de Databricks para el modelo.")
    return f"{host}/serving-endpoints", auth[len("Bearer "):]
# Caché de consultas «stale-while-revalidate»: clave → (instante, filas). Dentro del TTL se sirve tal cual;
# caducada (pero con menos de CACHE_STALE_SECONDS) se sirve al instante y se refresca en segundo plano,
# así ningún usuario espera a Databricks por una caducidad. Las anomalías nuevas invalidan todo (dataset.py).
_cache: LRUCache = LRUCache(maxsize=get_settings().cache_size)
_cache_lock = threading.Lock()
_inflight: dict[Any, threading.Lock] = {}
_refreshing: set = set()

# Hilos: «parallel» para las consultas de una misma petición; «background» para refrescos y precalentado.
_parallel = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sql")
_background = ThreadPoolExecutor(max_workers=2, thread_name_prefix="refresh")


def parallel(*fns: Callable[[], Any]) -> list[Any]:
    """Ejecuta varias funciones (consultas) a la vez y devuelve sus resultados en orden.

    Si alguna falla, se propaga su excepción (no se convierten errores en resultados vacíos).
    Solo debe usarse un nivel (una función lanzada aquí no debe volver a llamar a parallel).
    """
    if len(fns) <= 1:
        return [f() for f in fns]
    futures = [_parallel.submit(f) for f in fns]
    return [f.result() for f in futures]


def background(fn: Callable[[], Any]) -> None:
    def run():
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - un refresco fallido no debe tirar nada; se reintenta luego
            log.warning("Tarea en segundo plano fallida: %s", e)
    _background.submit(run)


_STARTING_HINTS = ("starting", "is not running", "warehouse", "temporarily unavailable", "503", "timed out", "timeout")
_wh_state: dict[str, Any] = {"at": 0.0, "value": None}


def warehouse_state(max_age: float = 30.0) -> str | None:
    """Estado del SQL Warehouse (RUNNING, STARTING, STOPPED…) o None. Se guarda `max_age` segundos."""
    s = get_settings()
    if not s.warehouse_id:
        return None
    if max_age and time.monotonic() - _wh_state["at"] < max_age:
        return _wh_state["value"]
    try:
        w = _pool.workspace_client().warehouses.get(s.warehouse_id)
        value = w.state.value if w.state else None
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo leer el estado del warehouse: %s", e)
        value = None
    _wh_state.update(at=time.monotonic(), value=value)
    return value


def _classify(err: Exception) -> DataError:
    from databricks.sql import exc

    msg = str(err)
    if isinstance(err, DataError):
        return err
    if isinstance(err, (exc.RequestError, exc.OperationalError, exc.MaxRetryDurationError)) or any(
        h in msg.lower() for h in _STARTING_HINTS
    ):
        state = warehouse_state(max_age=0)
        if state and state != "RUNNING":
            return WarehouseUnavailable(f"El SQL Warehouse está arrancando (estado: {state}). Reintenta en unos segundos.")
        if isinstance(err, (exc.RequestError, exc.MaxRetryDurationError)):
            return WarehouseUnavailable("No se puede conectar con el SQL Warehouse. Reintenta en unos segundos.")
    e = DataError(f"Error en la consulta a Databricks: {msg[:500]}")
    return e


_READ_ONLY = ("select", "with", "show", "describe")


def _run(sql_text: str, params: dict[str, Any] | None) -> list[dict[str, Any]]:
    # El backend solo lee: cualquier otra sentencia se rechaza antes de llegar al warehouse.
    if not sql_text.lstrip().lower().startswith(_READ_ONLY) or ";" in sql_text.strip().rstrip(";"):
        raise BadRequest("Solo se permiten consultas de lectura.")
    params = {k: _param(v) for k, v in (params or {}).items()}
    last: Exception | None = None
    for attempt in range(2):
        conn = _pool.acquire() if attempt == 0 else _pool._new()
        broken = False
        try:
            with conn.cursor() as cur:
                t0 = time.perf_counter()
                cur.execute(sql_text, params)
                cols = [d[0] for d in (cur.description or [])]
                rows = cur.fetchall() if cols else []
                log.debug("SQL %.0f ms · %d filas", (time.perf_counter() - t0) * 1000, len(rows))
                return convert_rows(cols, rows)
        except Exception as e:  # noqa: BLE001
            broken = True
            last = e
            from databricks.sql import exc

            # Conexión caducada o cerrada: un reintento con una conexión nueva.
            if attempt == 0 and isinstance(e, (exc.SessionAlreadyClosedError, exc.CursorAlreadyClosedError, exc.RequestError)):
                continue
            raise _classify(e) from e
        finally:
            _pool.release(conn, broken)
    raise _classify(last or DataError("Error desconocido"))


def _store(key, rows) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), rows)


def _revalidate(key, sql_text: str, params: dict[str, Any] | None, generation: int) -> None:
    try:
        rows = _run(sql_text, params)
        if generation == _generation[0]:  # no reescribir si entretanto se invalidó la caché
            _store(key, rows)
    finally:
        with _cache_lock:
            _refreshing.discard(key)


_generation = [0]


def query(sql_text: str, params: dict[str, Any] | None = None, *, cache: bool = True) -> list[dict[str, Any]]:
    """Ejecuta una consulta parametrizada y devuelve filas como dicts con tipos JSON."""
    if not cache:
        return _run(sql_text, params)
    s = get_settings()
    key = (sql_text, tuple(sorted((params or {}).items(), key=lambda kv: kv[0])))
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            age = time.monotonic() - hit[0]
            if age < s.cache_ttl:
                return hit[1]
            if age < s.cache_stale and key not in _refreshing:
                # Caducada: se sirve ya y se refresca detrás (stale-while-revalidate).
                _refreshing.add(key)
                gen = _generation[0]
                background(lambda: _revalidate(key, sql_text, params, gen))
            if age < s.cache_stale:
                return hit[1]
        lock = _inflight.setdefault(key, threading.Lock())
    # Una sola ejecución por clave aunque lleguen varias peticiones a la vez.
    with lock:
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None and time.monotonic() - hit[0] < s.cache_ttl:
                return hit[1]
        try:
            rows = _run(sql_text, params)
        finally:
            with _cache_lock:
                _inflight.pop(key, None)
        _store(key, rows)
        return rows


def query_one(sql_text: str, params: dict[str, Any] | None = None, *, cache: bool = True) -> dict[str, Any] | None:
    rows = query(sql_text, params, cache=cache)
    return rows[0] if rows else None


# Cachés de objetos derivados (snapshot del dataset, etc.) que se invalidan junto a la de SQL.
_derived: TTLCache = TTLCache(maxsize=64, ttl=get_settings().cache_ttl)
_derived_lock = threading.RLock()


def cached(key: str, builder):
    with _derived_lock:
        if key in _derived:
            return _derived[key]
        value = builder()
        _derived[key] = value
        return value


def clear_cache() -> dict[str, int]:
    with _cache_lock:
        n_sql = len(_cache)
        _cache.clear()
        _inflight.clear()
        _generation[0] += 1
    with _derived_lock:
        n_obj = len(_derived)
        _derived.clear()
    return {"consultas": n_sql, "objetos": n_obj}


def close() -> None:
    _pool.close_all()
