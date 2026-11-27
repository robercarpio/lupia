"""Configuración del backend, leída de variables de entorno (Databricks Apps las inyecta desde app.yaml)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


def _load_dotenv(path: str) -> None:
    """Carga un .env local (solo desarrollo). No sobrescribe variables ya definidas."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip("'\"")


_load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _http_path() -> str:
    """DATABRICKS_HTTP_PATH (/sql/1.0/warehouses/<id>) o, en su defecto, DATABRICKS_WAREHOUSE_ID."""
    path = os.getenv("DATABRICKS_HTTP_PATH", "").strip()
    if path:
        return path
    wid = os.getenv("DATABRICKS_WAREHOUSE_ID", "").strip()
    return f"/sql/1.0/warehouses/{wid}" if wid else ""


def _schema() -> str:
    """DATABRICKS_CATALOG + DATABRICKS_SCHEMA o, en su defecto, RBE_SCHEMA (catalogo.esquema)."""
    cat, sch = os.getenv("DATABRICKS_CATALOG", "").strip(), os.getenv("DATABRICKS_SCHEMA", "").strip()
    if cat and sch:
        return f"{cat}.{sch}"
    return os.getenv("RBE_SCHEMA", "dat_hack_rb06.rbe").strip()


@dataclass(frozen=True)
class Settings:
    # Conexión. Host y credenciales: DATABRICKS_HOST + DATABRICKS_TOKEN, o el perfil de ~/.databrickscfg
    # (DATABRICKS_CONFIG_PROFILE), o el Service Principal que inyecta Databricks Apps. Nunca se registran en logs.
    host: str = field(default_factory=lambda: os.getenv("DATABRICKS_HOST", "").strip())
    http_path: str = field(default_factory=_http_path)
    rbe_schema: str = field(default_factory=_schema)
    # Tiempo de vida de la caché de consultas (segundos).
    cache_ttl: int = field(default_factory=lambda: _int("CACHE_TTL_SECONDS", 900))
    cache_size: int = field(default_factory=lambda: _int("CACHE_MAX_ENTRIES", 1024))
    # Pasado el TTL y hasta este límite, se sirve el último resultado y se refresca en segundo plano.
    cache_stale: int = field(default_factory=lambda: _int("CACHE_STALE_SECONDS", 6 * 3600))
    pool_size: int = field(default_factory=lambda: _int("SQL_POOL_SIZE", 4))
    # Cada cuánto se comprueba si el modelo externo ha escrito anomalías nuevas (consulta ligera).
    anomaly_refresh: int = field(default_factory=lambda: _int("ANOMALY_REFRESH_SECONDS", 60))
    # Tiempo máximo de una consulta en el warehouse (segundos).
    statement_timeout: int = field(default_factory=lambda: _int("SQL_STATEMENT_TIMEOUT_SECONDS", 120))
    # Las columnas *_pct de la capa Gold se guardan en puntos porcentuales (-45.20 = -45,2 %).
    # El frontend trabaja con fracciones (-0.452). Cambiar a "fraction" si el pipeline guarda fracciones.
    pct_units: str = field(default_factory=lambda: os.getenv("RBE_PCT_UNITS", "percent").strip().lower())
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip())
    )
    # Frontend compilado (vite build) servido por la misma app. Vacío = no se sirve.
    static_dir: str = field(default_factory=lambda: os.getenv("FRONTEND_DIST", "static").strip())

    azure_endpoint: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT", "").strip())
    azure_api_key: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_KEY", "").strip())
    azure_deployment: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip())
    azure_api_version: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21").strip())
    # Alternativa sin claves: un modelo servido en Databricks (Foundation Model APIs / Model Serving),
    # p. ej. databricks-claude-sonnet-4-5. Se autentica igual que el warehouse (Service Principal de la
    # app o perfil del CLI). Si Azure OpenAI también está configurado, tiene prioridad Azure.
    llm_endpoint: str = field(default_factory=lambda: os.getenv("DATABRICKS_LLM_ENDPOINT", "").strip())
    # Temperatura del copiloto. 0 = respuestas estables: la misma pregunta da los mismos datos por
    # chat y por llamada. Vacío = la del proveedor (algunos modelos de razonamiento no la admiten).
    ai_temperature: float | None = field(
        default_factory=lambda: float(os.getenv("AI_TEMPERATURE", "0").strip()) if os.getenv("AI_TEMPERATURE", "0").strip() else None
    )

    def __post_init__(self) -> None:
        if not _SCHEMA_RE.match(self.rbe_schema):
            # El esquema se interpola en el SQL (los identificadores no admiten parámetros):
            # se valida de forma estricta para que no pueda inyectarse nada.
            raise ValueError(f"RBE_SCHEMA no válido: «{self.rbe_schema}». Formato esperado: catalogo.esquema")
        if self.http_path and not re.match(r"^/sql/[0-9.]+/warehouses/[A-Za-z0-9-]+$", self.http_path):
            raise ValueError("DATABRICKS_HTTP_PATH no válido. Formato esperado: /sql/1.0/warehouses/<id>")
        if self.pct_units not in ("percent", "fraction"):
            raise ValueError("RBE_PCT_UNITS debe ser 'percent' o 'fraction'")

    @property
    def warehouse_id(self) -> str:
        return self.http_path.rsplit("/", 1)[-1] if self.http_path else ""

    @property
    def azure_configured(self) -> bool:
        return bool(self.azure_endpoint and self.azure_api_key and self.azure_deployment)

    @property
    def ai_provider(self) -> str | None:
        """Proveedor del copiloto: "azure_openai", "databricks" o None (modo básico)."""
        if self.azure_configured:
            return "azure_openai"
        if self.llm_endpoint:
            return "databricks"
        return None

    @property
    def ai_configured(self) -> bool:
        return self.ai_provider is not None

    @property
    def ai_model(self) -> str | None:
        return {"azure_openai": self.azure_deployment, "databricks": self.llm_endpoint}.get(self.ai_provider or "")

    def table(self, name: str) -> str:
        if not re.match(r"^[a-z_]+$", name):
            raise ValueError(name)
        return f"{self.rbe_schema}.{name}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
