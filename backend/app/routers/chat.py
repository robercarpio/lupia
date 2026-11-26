"""Copiloto: chat con streaming (contrato del frontend) y endpoint JSON de un turno."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..schemas import ChatRequest, ChatResetRequest, CopilotChatRequest, CopilotChatResponse
from ..services import copilot

router = APIRouter(prefix="/api", tags=["copiloto"])


@router.post("/chat", summary="Chat del AI Assistant (Server-Sent Events)")
def chat(body: ChatRequest):
    return StreamingResponse(
        copilot.chat_turn(body.model_dump()),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/reset", summary="Olvida una conversación")
def chat_reset(body: ChatResetRequest):
    copilot.reset_session(body.conversationId)
    return {"ok": True}


@router.post("/copilot/chat", response_model=CopilotChatResponse,
             summary="Pregunta sobre un episodio, una alerta, un restaurante o el periodo filtrado")
def copilot_chat(body: CopilotChatRequest):
    return copilot.copilot_answer(body.model_dump())
