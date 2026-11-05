"""Endpoints da Fase 4: banco de snapshots de mercado (SQLite), Text-to-Query e Retrieval Agent.

O agente principal (``POST /agent``) usa as mesmas peças como ferramentas; estes endpoints
expõem cada uma isoladamente para operação, depuração e avaliação.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from app.api import examples
from app.api.container import AppContainer
from app.api.routes import get_container
from app.market_data.ingestion import IngestionReport
from app.market_data.text_to_query import TextToQueryResult
from app.retrieval.agent import RetrievalResult

router = APIRouter()


class IngestRequest(BaseModel):
    assets: list[str] | None = Field(
        default=None,
        max_length=25,
        description="Ids CoinPaprika, símbolos ou nomes. Omitido = lista padrão (MARKET_DATA_ASSETS).",
    )


class MarketQueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class RetrieveRequest(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    mode: Literal["auto", "always", "never"] | None = Field(
        default=None, description="auto: reescreve só se a busca for fraca. Omitido = RETRIEVAL_REWRITE_MODE."
    )
    k: int = Field(default=3, ge=1, le=10)


@router.post("/market-data/ingest", response_model=IngestionReport, tags=["fase 4"])
def market_ingest(
    body: Annotated[IngestRequest, Body(openapi_examples=examples.MARKET_INGEST)],
    container: AppContainer = Depends(get_container),
) -> IngestionReport:
    """Coleta snapshots reais da CoinPaprika e grava no SQLite (consome cota da API)."""
    assets = body.assets or list(container.market_settings.default_assets)
    return container.market_ingestor().ingest(assets)


@router.get("/market-data/coverage", tags=["fase 4"])
def market_coverage(container: AppContainer = Depends(get_container)) -> dict:
    """O que o banco contém: ativos, quantidade de snapshots e janela de tempo coberta."""
    return container.market_store.coverage().as_dict()


@router.get("/market-data/snapshots", tags=["fase 4"])
def market_snapshots(
    symbol: str | None = Query(None, pattern=r"^[A-Za-z0-9]{1,15}$", openapi_examples=examples.SYMBOL),
    limit: int = Query(20, ge=1, le=200),
    container: AppContainer = Depends(get_container),
) -> list[dict]:
    return container.market_store.snapshots(symbol, limit)


@router.post("/market-data/query", response_model=TextToQueryResult, tags=["fase 4"])
def market_query(
    body: Annotated[MarketQueryRequest, Body(openapi_examples=examples.MARKET_QUERY)],
    container: AppContainer = Depends(get_container),
) -> TextToQueryResult:
    """Text-to-Query: pergunta → SQL validado (somente leitura) → linhas reais do banco."""
    return container.text_to_query.run(body.question)


@router.post("/knowledge/retrieve", response_model=RetrievalResult, tags=["fase 4"])
def knowledge_retrieve(
    body: Annotated[RetrieveRequest, Body(openapi_examples=examples.KNOWLEDGE_RETRIEVE)],
    container: AppContainer = Depends(get_container),
) -> RetrievalResult:
    """Retrieval Agent sobre a coleção existente, com Query Rewriting e os dois resultados para comparação."""
    return container.retrieval.retrieve(body.question, body.mode, body.k)
