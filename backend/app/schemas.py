"""Modelos Pydantic de entrada y salida.

Las respuestas del dashboard conservan exactamente las claves que ya consume el frontend
(contrato heredado de server/index.mjs). Por eso los modelos de salida aceptan campos extra:
documentan el contrato sin recortar campos adicionales útiles para gráficas.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Canal = Literal["sala", "domicilio", "autoking"]
Tipo = Literal["caida", "subida"]


class Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


# ── Entrada ───────────────────────────────────────────────────────────
class Filters(BaseModel):
    """Filtros globales del dashboard. Se envían como JSON en el parámetro ?f= (GET) o en el cuerpo (POST)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    from_: str | None = Field(default=None, alias="from", description="AAAA-MM-DD o 'all'")
    to: str | None = None
    restaurante: str | None = None
    canales: list[str] | None = None
    tipo: str | None = None
    ccaa: str | None = None
    provincia: str | None = None
    tipologia: str | None = None
    ubicacion: str | None = None
    minScore: float | None = None
    minImpact: float | None = None
    prioridad: str | None = Field(default=None, description="alta | media | baja (o P1 | P2 | P3)")

    def as_raw(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class ChatRequest(BaseModel):
    """Cuerpo de POST /api/chat (streaming SSE, contrato del frontend)."""

    model_config = ConfigDict(extra="ignore")
    conversationId: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    voice: bool = False
    dashboardFilters: dict[str, Any] | None = None
    focusId: str | None = None


class ChatResetRequest(BaseModel):
    conversationId: str | None = None


class CopilotChatRequest(BaseModel):
    """Cuerpo de POST /api/copilot/chat (respuesta JSON de un turno)."""

    model_config = ConfigDict(extra="ignore")
    question: str = Field(min_length=1, max_length=4000, validation_alias="question")
    episode_id: str | None = None
    restaurante_id: str | None = None
    anomaly_id: str | None = Field(default=None, description="Id de alerta diaria: R001-sala-2025-03-11")
    filters: dict[str, Any] | None = None
    conversation_id: str | None = None


# ── Salida ────────────────────────────────────────────────────────────
class ErrorResponse(BaseModel):
    error: str
    code: str | int | None = None


class AiStatus(BaseModel):
    configured: bool
    model: str | None
    provider: str = "azure_openai"


class HealthResponse(BaseModel):
    ok: bool
    data: dict[str, Any]
    ai: AiStatus
    warehouse: dict[str, Any]


class CacheClearResponse(BaseModel):
    ok: bool
    cleared: dict[str, int]


class KpiGlobal(Loose):
    total_alertas_dia: int = 0
    total_episodios: int = 0
    restaurantes_afectados: int = 0
    total_caidas: int = 0
    total_subidas: int = 0
    alertas_p1: int = 0
    impacto_caidas_eur: float = 0
    impacto_subidas_eur: float = 0
    impacto_bruto_total_eur: float = 0


class ModelRun(Loose):
    run_id: str
    model_version: str | None = None
    is_active: bool | None = None
    created_at: str | None = None
    eval_start_date: str | None = None
    eval_end_date: str | None = None
    total_candidate_rows: int | None = None
    total_alerts: int | None = None
    total_caidas: int | None = None
    total_subidas: int | None = None
    f1_synthetic: float | None = None
    precision_synthetic: float | None = None
    recall_synthetic: float | None = None
    precision_at_100_synthetic: float | None = None
    params_json: dict[str, Any] | list | None = None


class KpisResponse(BaseModel):
    global_: KpiGlobal = Field(serialization_alias="global")
    model_run: ModelRun | None


class ModelRunsResponse(BaseModel):
    active: ModelRun | None
    runs: list[ModelRun]


class DashboardResponse(Loose):
    filters: dict[str, Any]
    warnings: list[str]
    kpis: dict[str, Any]
    timeline: dict[str, Any]
    channels: list[dict[str, Any]]
    geo: dict[str, list[dict[str, Any]]]


class PageResponse(Loose):
    total: int
    page: int
    pages: int
    size: int
    rows: list[dict[str, Any]]


class RankingResponse(Loose):
    total: int
    rows: list[dict[str, Any]]


class FilterOptions(Loose):
    dateMin: str
    dateMax: str
    evalStart: str
    defaultRange: dict[str, str]
    canales: list[str]
    tipos: list[str]
    prioridades: list[dict[str, str]]
    ccaas: list[str]
    provincias: list[dict[str, str | None]]
    tipologias: list[str]
    ubicaciones: list[str]
    restaurantes: list[str]


class CopilotChatResponse(BaseModel):
    answer: str
    source: Literal["azure_openai", "fallback"]
    model: str | None = None
    context: dict[str, Any] = Field(default_factory=dict, description="Qué datos se usaron para responder")
    warnings: list[str] = Field(default_factory=list)
