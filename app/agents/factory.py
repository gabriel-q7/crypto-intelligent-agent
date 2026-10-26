"""Fábricas dos componentes da Fase 2 (agente, roteador e busca de conhecimento)."""

import threading
from dataclasses import replace

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel, BaseLanguageModel
from langchain_core.runnables import Runnable
from langchain_core.vectorstores import VectorStore

from app.agents.runner import CryptoAgent
from app.chains.routing import build_route_handlers, build_router_chain
from app.config import AgentSettings, KnowledgeSettings, Settings, get_settings
from app.knowledge.store import KnowledgeHit, load_index, search
from app.legacy.router_chain import build_legacy_router, legacy_classify
from app.memory.conversation import BoundedConversationMemory
from app.services.llm import build_llm
from app.tools.coinpaprika import CoinPaprikaClient
from app.tools.knowledge_tool import build_knowledge_tool
from app.tools.market_tools import build_market_tools


class KnowledgeSearcher:
    """Abre a coleção Chroma sob demanda (uma vez) e permite recarregar após nova ingestão."""

    def __init__(self, settings: KnowledgeSettings, embeddings: Embeddings | None = None) -> None:
        self.settings = settings
        self.embeddings = embeddings
        self._index: VectorStore | None = None
        self._lock = threading.Lock()

    def index(self) -> VectorStore:
        with self._lock:
            if self._index is None:
                self._index = load_index(self.settings, self.embeddings)
            return self._index

    def reload(self) -> None:
        with self._lock:
            self._index = None

    def __call__(self, query: str) -> list[KnowledgeHit]:
        return search(self.index(), query, self.settings.top_k)


def chat_settings(settings: Settings | None = None, model: str | None = None) -> Settings:
    settings = settings or get_settings()
    return replace(settings, ollama_model=model) if model else settings


def build_agent(
    client: CoinPaprikaClient,
    knowledge: KnowledgeSearcher,
    agent_settings: AgentSettings,
    memory: BoundedConversationMemory | None = None,
    settings: Settings | None = None,
    llm: BaseLanguageModel | None = None,
) -> CryptoAgent:
    settings = settings or get_settings()
    # Protocolo ReAct é texto: sem modo JSON.
    llm = llm or build_llm(settings, json_mode=False)
    tools = [*build_market_tools(client), build_knowledge_tool(knowledge.index, knowledge.settings.top_k)]
    return CryptoAgent(llm, tools, agent_settings, memory, model_name=settings.ollama_model)


def build_router(
    llm: BaseChatModel, client: CoinPaprikaClient, knowledge: KnowledgeSearcher, legacy: bool = False
) -> Runnable:
    handlers = build_route_handlers(llm, client, knowledge)
    classifier = None
    if legacy:
        router = build_legacy_router(llm)
        classifier = lambda question: legacy_classify(router, question)  # noqa: E731
    return build_router_chain(llm, handlers, classifier)
