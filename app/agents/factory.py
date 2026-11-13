"""Fábricas dos componentes da Fase 2 (agente, roteador e busca de conhecimento)."""

import threading
from dataclasses import replace

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel, BaseLanguageModel
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.vectorstores import VectorStore

from app.agents.runner import CryptoAgent
from app.chains.routing import build_route_handlers, build_router_chain
from app.config import AgentSettings, KnowledgeSettings, Settings, get_settings
from app.knowledge.store import KnowledgeHit, load_index, search
from app.legacy.router_chain import build_legacy_router, legacy_classify
from app.market_data.text_to_query import TextToQueryService
from app.memory.conversation import BoundedConversationMemory
from app.retrieval.agent import RetrievalAgent
from app.services.llm import build_llm
from app.tools.calculation_tools import build_calculation_tools
from app.tools.coinpaprika import CoinPaprikaClient
from app.tools.knowledge_tool import build_knowledge_tool
from app.tools.market_db_tool import build_market_db_tool
from app.tools.market_tools import build_market_tools
from app.tools.retrieval_tool import build_retrieval_tool


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


def build_agent_tools(
    client: CoinPaprikaClient,
    knowledge: KnowledgeSearcher,
    retrieval: RetrievalAgent | None = None,
    market_db: TextToQueryService | None = None,
    profile: bool = False,
) -> list[BaseTool]:
    """Ferramentas do agente principal.

    Fase 2: CoinPaprika (API) + busca de conhecimento. Fase 4: cálculo determinístico,
    Text-to-Query no banco de snapshots e Retrieval Agent (com Query Rewriting) no lugar da
    busca simples — mesmo nome de ferramenta, ``search_knowledge_base``. ``profile`` acrescenta o perfil
    técnico da CoinPaprika (``get_asset_profile``), usado só pelo agente integrado.
    """
    knowledge_tool = (
        build_retrieval_tool(retrieval) if retrieval else build_knowledge_tool(knowledge.index, knowledge.settings.top_k)
    )
    tools: list[BaseTool] = [*build_market_tools(client, profile), knowledge_tool, *build_calculation_tools()]
    if market_db:
        tools.append(build_market_db_tool(market_db))
    return tools


def build_agent(
    client: CoinPaprikaClient,
    knowledge: KnowledgeSearcher,
    agent_settings: AgentSettings,
    memory: BoundedConversationMemory | None = None,
    settings: Settings | None = None,
    llm: BaseLanguageModel | None = None,
    retrieval: RetrievalAgent | None = None,
    market_db: TextToQueryService | None = None,
) -> CryptoAgent:
    settings = settings or get_settings()
    # Protocolo ReAct é texto: sem modo JSON.
    llm = llm or build_llm(settings, json_mode=False)
    tools = build_agent_tools(client, knowledge, retrieval, market_db)
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
