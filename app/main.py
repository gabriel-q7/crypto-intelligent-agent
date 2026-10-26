"""Aplicação HTTP do Crypto Intelligence Agent.

Execução:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
Documentação interativa: http://localhost:8000/docs
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from ollama import ResponseError

from app.api.container import AppContainer
from app.api.routes import router
from app.errors import AnalysisOutputError, LLMUnavailableError
from app.knowledge.store import KnowledgeIndexError
from app.observability.logging import configure_logging


def create_app(container_factory: Callable[[], AppContainer] = AppContainer) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        app.state.container = container_factory()
        yield
        app.state.container.close()

    app = FastAPI(
        title="Crypto Intelligence Agent",
        version="0.2.0",
        description="Fase 1: análise estruturada. Fase 2: agente ReAct, chains roteadas, CoinPaprika e base de conhecimento (Chroma).",
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.exception_handler(LLMUnavailableError)
    async def llm_unavailable(_: Request, exc: LLMUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc), "error": "llm_unavailable"})

    async def ollama_unreachable(_: Request, exc: Exception) -> JSONResponse:
        # Chains da Fase 2: o Ollama continuou inacessível mesmo após o retry.
        return JSONResponse(status_code=503, content={"detail": f"Ollama indisponível: {exc}", "error": "llm_unavailable"})

    for error_type in (ConnectionError, httpx.TransportError, ResponseError):
        app.add_exception_handler(error_type, ollama_unreachable)

    @app.exception_handler(AnalysisOutputError)
    async def invalid_model_output(_: Request, exc: AnalysisOutputError) -> JSONResponse:
        # O modelo respondeu algo fora do schema: falha do "servidor upstream" (o LLM).
        return JSONResponse(status_code=502, content={"detail": str(exc), "error": "invalid_model_output"})

    @app.exception_handler(KnowledgeIndexError)
    async def index_unavailable(_: Request, exc: KnowledgeIndexError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc), "error": "knowledge_index_unavailable"})

    return app


app = create_app()
