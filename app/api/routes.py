"""Endpoints HTTP.

Endpoints síncronos (``def``): o FastAPI os executa em thread pool, adequado às chamadas
bloqueantes ao Ollama e à CoinPaprika.
"""

import json
import uuid
from collections.abc import Iterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.container import AppContainer
from app.api.schemas import (
    AgentRequest,
    AnalyzeRequest,
    BatchAnalyzeRequest,
    BatchItem,
    HealthResponse,
    KnowledgeHitResponse,
    ReportResponse,
    RouteRequest,
)
from app.chains import build_analysis_chain, build_json_chain
from app.errors import AnalysisError, AnalysisOutputError
from app.knowledge.store import build_index, search
from app.observability.callbacks import TraceCallbackHandler
from app.schemas import CryptoAnalysisRequest, CryptoQuestion
from app.services.analysis import run_analysis, run_batch, translate_error
from app.services.coinpaprika_check import run_checks
from app.services.validation import validate_analysis

router = APIRouter()


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


# --------------------------------------------------------------------------- saúde


@router.get("/health", response_model=HealthResponse, tags=["operação"])
def health(container: AppContainer = Depends(get_container)) -> HealthResponse:
    chat, embedding = container.settings.ollama_model, container.knowledge_settings.embedding_model
    try:
        tags = httpx.get(f"{container.settings.ollama_base_url}/api/tags", timeout=3).json()
        names = {m["name"].removesuffix(":latest") for m in tags.get("models", [])}
        reachable = True
    except httpx.HTTPError:
        names, reachable = set(), False
    models = {chat: chat.removesuffix(":latest") in names, embedding: embedding.removesuffix(":latest") in names}
    try:
        container.knowledge.index()
        index_ok = True
    except Exception:  # noqa: BLE001 - qualquer falha de carga = índice indisponível
        index_ok = False
    ok = reachable and all(models.values()) and index_ok
    return HealthResponse(
        status="ok" if ok else "degraded",
        chat_model=chat,
        embedding_model=embedding,
        ollama_reachable=reachable,
        models_available=models,
        knowledge_index=index_ok,
        active_sessions=len(container.sessions),
    )


@router.get("/health/coinpaprika", tags=["operação"])
def coinpaprika_health(
    asset: str = Query("btc-bitcoin", pattern=r"^[a-z0-9]+(-[a-z0-9]+)+$"),
    container: AppContainer = Depends(get_container),
) -> dict:
    """Verificação real dos endpoints da CoinPaprika (consome cota da API)."""
    return run_checks(container.coinpaprika, asset)


# --------------------------------------------------------------------------- Fase 1: análise estruturada


@router.post("/analyze", response_model=CryptoAnalysisRequest, tags=["fase 1"])
def analyze(body: AnalyzeRequest, container: AppContainer = Depends(get_container)) -> CryptoAnalysisRequest:
    """``invoke``: plano de análise + resposta mínima, sem dados externos."""
    question = CryptoQuestion(**body.model_dump(exclude={"composition"}))
    return run_analysis(build_analysis_chain(container.llm, body.composition), question)


@router.post("/analyze/batch", response_model=list[BatchItem], tags=["fase 1"])
def analyze_batch(body: BatchAnalyzeRequest, container: AppContainer = Depends(get_container)) -> list[BatchItem]:
    """``batch``: falhas isoladas por item."""
    results = run_batch(build_analysis_chain(container.llm), body.questions)
    return [
        BatchItem(ok=False, error=f"{type(r).__name__}: {r}") if isinstance(r, AnalysisError) else BatchItem(ok=True, result=r)
        for r in results
    ]


@router.post("/analyze/stream", tags=["fase 1"])
def analyze_stream(body: CryptoQuestion, container: AppContainer = Depends(get_container)) -> StreamingResponse:
    """``stream``: NDJSON com objetos parciais (não validados) e, por fim, o objeto validado.

    Limitação: só o último objeto passa pelo schema; erros após o início do stream chegam
    como evento ``error`` (o status HTTP 200 já foi enviado).
    """

    def line(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str) + "\n"

    def events() -> Iterator[str]:
        last: dict | None = None
        try:
            # Cada parcial é enviado assim que o JsonOutputParser o produz.
            for partial in build_json_chain(container.llm).stream(body):
                last = partial
                yield line({"type": "partial", "data": partial})
            if last is None:
                raise AnalysisOutputError("o streaming terminou sem produzir JSON")
            yield line({"type": "final", "data": validate_analysis(last).model_dump(mode="json")})
        except Exception as exc:  # noqa: BLE001 - após o início do stream o status HTTP já foi enviado
            translated = translate_error(exc)
            if not isinstance(translated, AnalysisError):
                raise
            yield line({"type": "error", "error": f"{type(translated).__name__}: {translated}"})

    return StreamingResponse(events(), media_type="application/x-ndjson")


# --------------------------------------------------------------------------- Fase 2: agente e chains


@router.post("/agent", response_model=ReportResponse, tags=["fase 2"])
def run_agent(body: AgentRequest, container: AppContainer = Depends(get_container)) -> ReportResponse:
    """Agente ReAct clássico com ferramentas reais (CoinPaprika, base de conhecimento)."""
    session_id = body.session_id or uuid.uuid4().hex
    agent = container.agent(session_id)
    report = agent.run(body.question)
    trace = agent.last_trace.to_dicts() if body.include_trace and agent.last_trace else None
    return ReportResponse(report=report, session_id=session_id, trace=trace)


@router.delete("/agent/sessions/{session_id}", status_code=204, tags=["fase 2"])
def delete_session(session_id: str, container: AppContainer = Depends(get_container)) -> None:
    if not container.sessions.delete(session_id):
        raise HTTPException(404, "sessão não encontrada")


@router.post("/route", response_model=ReportResponse, tags=["fase 2"])
def route(body: RouteRequest, container: AppContainer = Depends(get_container)) -> ReportResponse:
    """Roteamento dinâmico: análise de mercado, comparação, conhecimento ou DeFi (indisponível)."""
    trace = TraceCallbackHandler()
    report = container.router(legacy=body.router == "legacy").invoke(
        {"question": body.question, "assets": body.assets}, config={"callbacks": [trace]}
    )
    return ReportResponse(report=report, trace=trace.to_dicts() if body.include_trace else None)


# --------------------------------------------------------------------------- conhecimento


@router.get("/knowledge/search", response_model=list[KnowledgeHitResponse], tags=["conhecimento"])
def knowledge_search(
    q: str = Query(min_length=2, max_length=300),
    k: int = Query(3, ge=1, le=10),
    container: AppContainer = Depends(get_container),
) -> list[KnowledgeHitResponse]:
    hits = search(container.knowledge.index(), q, k)
    return [KnowledgeHitResponse(source=h.source, title=h.title, score=h.score, content=h.content) for h in hits]


@router.post("/knowledge/ingest", tags=["conhecimento"])
def knowledge_ingest(container: AppContainer = Depends(get_container)) -> dict:
    """Reconstrói o índice FAISS a partir de knowledge/corpus e recarrega a busca."""
    _, manifest = build_index(container.knowledge_settings, container.knowledge.embeddings)
    container.knowledge.reload()
    container.knowledge.index()  # valida que o índice salvo carrega
    return manifest
