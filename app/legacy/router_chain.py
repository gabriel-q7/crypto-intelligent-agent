"""Classificação de rota com ``RouterChain`` (``LLMRouterChain`` + ``RouterOutputParser``).

API LEGADA, isolada neste módulo (pacote ``langchain-classic``, depreciada no LangChain 1.x).
Usada só para DECIDIR a rota; o despacho reaproveita os handlers modernos
(``app.chains.routing.build_dispatcher``). ``MultiPromptChain`` não é usado porque exige
destinos ``LLMChain``, e nossos destinos são chains LCEL com chamadas de API.

Alternativa moderna: classificação com saída estruturada + ``RunnableBranch``
(``app.chains.routing``), ou arestas condicionais em LangGraph.
"""

import logging
import warnings

from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import PromptTemplate

from app.chains.routing import ROUTE_DESCRIPTIONS, RouteDecision, keyword_route

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from langchain_classic.chains.router.llm_router import LLMRouterChain, RouterOutputParser
    from langchain_classic.chains.router.multi_prompt_prompt import MULTI_PROMPT_ROUTER_TEMPLATE

logger = logging.getLogger(__name__)


def build_legacy_router(llm: BaseLanguageModel) -> LLMRouterChain:
    destinations = "\n".join(f"{name}: {desc}" for name, desc in ROUTE_DESCRIPTIONS.items())
    template = MULTI_PROMPT_ROUTER_TEMPLATE.format(destinations=destinations)
    prompt = PromptTemplate(
        template=template, input_variables=["input"], output_parser=RouterOutputParser()
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return LLMRouterChain.from_llm(llm, prompt)


def legacy_classify(router: LLMRouterChain, question: str) -> RouteDecision:
    """``destination`` None (DEFAULT) ou inválido → regras de palavras-chave."""
    try:
        result = router.invoke({"input": question})
    except Exception as exc:  # parser/LLM: o legado lança OutputParserException e afins
        logger.warning("legacy router failed, using keywords: %s", exc)
        return keyword_route(question)
    destination = result.get("destination")
    if destination not in ROUTE_DESCRIPTIONS:
        logger.info("legacy router returned %r, using keywords", destination)
        return keyword_route(question)
    return RouteDecision(route=destination, reason="LLMRouterChain", method="legacy_router_chain")
