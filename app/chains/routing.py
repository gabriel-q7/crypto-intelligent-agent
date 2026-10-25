"""Roteamento dinâmico entre análise de mercado, DeFi, comparação e conhecimento.

Classificação (moderna, padrão): LLM em modo JSON → ``RouteDecision`` validado; se o modelo
falhar ou responder fora do esquema, cai para regras de palavras-chave (determinísticas).
Despacho: ``RunnableBranch``. A variante legada (``RouterChain``) está em
``app/legacy/router_chain.py`` e usa os mesmos handlers.
"""

import logging
import re
import unicodedata
from collections.abc import Callable
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableBranch, RunnableLambda
from pydantic import BaseModel, ValidationError

from app.chains.market_chains import (
    _ask_json,
    build_comparison_chain,
    build_defi_chain,
    build_knowledge_chain,
    build_market_analysis_chain,
)
from app.knowledge.store import KnowledgeHit
from app.prompts.ptcf import json_system_prompt
from app.schemas.report import CryptoReport
from app.tools.coinpaprika import CoinPaprikaClient

logger = logging.getLogger(__name__)

Route = Literal["market_analysis", "defi_analysis", "asset_comparison", "knowledge_retrieval"]

ROUTE_DESCRIPTIONS: dict[str, str] = {
    "market_analysis": "dados de mercado atuais de UM ativo ou do mercado (preço, volume, variação, market cap)",
    "asset_comparison": "comparar DOIS OU MAIS ativos (desempenho, preço, variação, market cap)",
    "defi_analysis": "protocolos DeFi, TVL, rendimentos, pools e liquidez de protocolos",
    "knowledge_retrieval": "conceitos e explicações (o que é, como funciona), sem dados atuais",
}


class RouteDecision(BaseModel):
    route: Route
    reason: str = ""
    method: str = "llm"


ROUTER_HINT = (
    'Esquema: {"route": "<rota>", "reason": "uma frase"}. Rotas:\n'
    + "\n".join(f"- {name}: {desc}" for name, desc in ROUTE_DESCRIPTIONS.items())
)

_CONCEPT = re.compile(r"^\s*(o que (e|sao)|como funciona|explique|qual a diferenca|defina|o que significa)")
_DEFI = re.compile(r"\b(defi|tvl|protocolo|pool|pools|liquidez|yield|rendimento|lending|emprestimo|dex)\b")
_COMPARE = re.compile(r"\b(compar\w*|versus|vs\.?|melhor que|diferenca de desempenho)\b|\bx\b")


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def keyword_route(question: str) -> RouteDecision:
    text = _normalize(question)
    if _CONCEPT.search(text):
        return RouteDecision(route="knowledge_retrieval", reason="pergunta conceitual", method="keywords")
    if _DEFI.search(text):
        return RouteDecision(route="defi_analysis", reason="termos DeFi", method="keywords")
    if _COMPARE.search(text):
        return RouteDecision(route="asset_comparison", reason="termos de comparação", method="keywords")
    return RouteDecision(route="market_analysis", reason="padrão: dados de mercado", method="keywords")


def classify(llm: BaseChatModel, question: str) -> RouteDecision:
    try:
        decision = _ask_json(llm, json_system_prompt(ROUTER_HINT), question, RouteDecision)
        return decision.model_copy(update={"method": "llm"})
    except (ValidationError, ValueError) as exc:
        logger.warning("router llm invalid, using keywords: %s", exc)
        return keyword_route(question)


def build_route_handlers(
    llm: BaseChatModel, client: CoinPaprikaClient, knowledge_search: Callable[[str], list[KnowledgeHit]]
) -> dict[str, Runnable]:
    return {
        "market_analysis": build_market_analysis_chain(llm, client),
        "asset_comparison": build_comparison_chain(llm, client),
        "knowledge_retrieval": build_knowledge_chain(llm, knowledge_search),
        "defi_analysis": build_defi_chain(),
    }


def build_dispatcher(handlers: dict[str, Runnable]) -> Runnable:
    """Recebe inputs com ``route`` já decidido e despacha via RunnableBranch."""

    def is_route(name: str) -> Callable[[dict], bool]:
        return lambda inputs: inputs["route"] == name

    return RunnableBranch(
        (is_route("defi_analysis"), handlers["defi_analysis"]),
        (is_route("asset_comparison"), handlers["asset_comparison"]),
        (is_route("knowledge_retrieval"), handlers["knowledge_retrieval"]),
        handlers["market_analysis"],
    )


def build_router_chain(
    llm: BaseChatModel,
    handlers: dict[str, Runnable],
    classifier: Callable[[str], RouteDecision] | None = None,
) -> Runnable:
    """Chain completa (moderna): classificar → despachar. Entrada: {"question", "assets"?}."""
    classifier = classifier or (lambda q: classify(llm, q))

    def decide(inputs: dict) -> dict:
        decision = classifier(inputs["question"])
        logger.info("route decided route=%s method=%s reason=%s", decision.route, decision.method, decision.reason)
        return {
            **inputs,
            "route": decision.route,
            "decisions": [f"rota={decision.route} via {decision.method}: {decision.reason}"],
            "observations": [],
            "uncertainties": [],
        }

    return RunnableLambda(decide, name="classify_route") | build_dispatcher(handlers)


def run_routed(chain: Runnable, question: str, assets: list[str] | None = None) -> CryptoReport:
    return chain.invoke({"question": question, "assets": assets or []})
