"""Endpoints de conversa (Fase 3): sessões persistentes com memória.

Sem autenticação nesta fase: o ``session_id`` identifica a conversa, mas NÃO é credencial —
quem conhece o id lê a conversa. Ver docs/memory-strategy.md (limitações).
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request

from app.api import examples
from app.api.container import AppContainer
from app.conversation.schemas import (
    SESSION_ID_PATTERN,
    ConversationReply,
    CreateConversationRequest,
    HistoryResponse,
    MessageView,
    SendMessageRequest,
    SessionInfo,
    SummaryResponse,
)

router = APIRouter(prefix="/conversations", tags=["fase 3: conversa"])

SessionId = Annotated[str, Path(pattern=SESSION_ID_PATTERN, description="Id da sessão (32 hex).")]


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


def _session_or_404(container: AppContainer, session_id: str):
    session = container.conversation_store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "sessão não encontrada")
    return session


def _info(session) -> SessionInfo:
    return SessionInfo(
        session_id=session.id, title=session.title, created_at=session.created_at,
        last_activity_at=session.last_activity_at, message_count=session.message_count,
        active_context=session.active_context,
    )


@router.post("", response_model=SessionInfo, status_code=201)
def create_conversation(
    body: Annotated[CreateConversationRequest, Body(openapi_examples=examples.CREATE_CONVERSATION)] = None,
    container: AppContainer = Depends(get_container),
) -> SessionInfo:
    return _info(container.conversation_store.create_session((body or CreateConversationRequest()).title))


@router.get("/{session_id}", response_model=SessionInfo)
def get_conversation(session_id: SessionId, container: AppContainer = Depends(get_container)) -> SessionInfo:
    """Também serve para retomar uma sessão (inclusive após reinício da API)."""
    return _info(_session_or_404(container, session_id))


@router.post("/{session_id}/messages", response_model=ConversationReply)
def send_message(
    session_id: SessionId,
    body: Annotated[SendMessageRequest, Body(openapi_examples=examples.CONVERSATION_MESSAGE)],
    container: AppContainer = Depends(get_container),
) -> ConversationReply:
    _session_or_404(container, session_id)
    return container.conversation.handle(session_id, body.message)


@router.get("/{session_id}/messages", response_model=HistoryResponse)
def get_history(
    session_id: SessionId,
    after_seq: int = Query(0, ge=0, description="Só mensagens com seq maior que este."),
    limit: int | None = Query(None, ge=1, le=500, description="Só as N mais recentes."),
    container: AppContainer = Depends(get_container),
) -> HistoryResponse:
    session = _session_or_404(container, session_id)
    messages = container.conversation_store.list_messages(session_id, after_seq=after_seq, last=limit)
    return HistoryResponse(
        session_id=session_id,
        total=session.message_count,
        messages=[
            MessageView(seq=m.seq, role=m.role, content=m.content, created_at=m.created_at,
                        intent=m.metadata.get("effective_intent") or m.metadata.get("intent"),
                        analysis_id=m.metadata.get("analysis_id"))
            for m in messages
        ],
    )


@router.get("/{session_id}/summary", response_model=SummaryResponse)
def get_summary(session_id: SessionId, container: AppContainer = Depends(get_container)) -> SummaryResponse:
    _session_or_404(container, session_id)
    summary = container.conversation_store.get_summary(session_id)
    if summary is None:
        return SummaryResponse(session_id=session_id, summary=None)
    return SummaryResponse(
        session_id=session_id, summary=summary.summary, details=summary.details,
        covers_until_seq=summary.covers_until_seq, version=summary.version, updated_at=summary.updated_at,
    )


@router.delete("/{session_id}", status_code=204)
def delete_conversation(session_id: SessionId, container: AppContainer = Depends(get_container)) -> None:
    """Apaga mensagens, resumos e análises (SQLite) e as memórias da sessão (Chroma)."""
    if not container.conversation.delete_session(session_id):
        raise HTTPException(404, "sessão não encontrada")
