"""Dependências compartilhadas da API, criadas uma vez no início da aplicação."""

import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel, BaseLanguageModel
from langchain_core.runnables import Runnable

from app.agents.factory import KnowledgeSearcher, build_agent, build_agent_tools, build_router
from app.agents.runner import CryptoAgent
from app.config import (
    AgentSettings,
    ConversationSettings,
    KnowledgeSettings,
    MarketDataSettings,
    RetrievalSettings,
    Settings,
    get_agent_settings,
    get_conversation_settings,
    get_knowledge_settings,
    get_market_data_settings,
    get_retrieval_settings,
    get_verification_settings,
    get_settings,
    project_path,
)
from app.conversation.chatbot import ConversationService
from app.integrated.agent import build_integrated_agent
from app.integrated.history import SessionHistoryAdapter
from app.integrated.service import IntegratedAgentService
from app.integrated.tools import structured_tools
from app.market_data.ingestion import MarketDataIngestor
from app.market_data.store import MarketDataStore
from app.market_data.text_to_query import TextToQueryService
from app.memory.conversation import BoundedConversationMemory
from app.memory.long_term import LazyMemoryBackend, Mem0Backend, MemoryBackend
from app.persistence.sqlite_store import ConversationStore
from app.retrieval.agent import RetrievalAgent
from app.retrieval.rewriter import QueryRewriter
from app.services.llm import build_llm
from app.tools.coinpaprika import CoinPaprikaClient
from app.verification.service import VerificationService


class SessionStore:
    """Memórias de conversa por sessão, limitadas em quantidade (LRU) e em tamanho (cada memória)."""

    def __init__(self, settings: AgentSettings, max_sessions: int = 100) -> None:
        self.settings = settings
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, BoundedConversationMemory] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str) -> BoundedConversationMemory:
        with self._lock:
            memory = self._sessions.pop(session_id, None) or BoundedConversationMemory(
                self.settings.memory_max_turns, self.settings.memory_max_chars
            )
            self._sessions[session_id] = memory
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
            return memory

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        return len(self._sessions)


@dataclass
class AppContainer:
    settings: Settings = field(default_factory=get_settings)
    agent_settings: AgentSettings = field(default_factory=get_agent_settings)
    knowledge_settings: KnowledgeSettings = field(default_factory=get_knowledge_settings)
    llm: BaseChatModel | None = None
    # LLM do agente ReAct (texto). None = ChatOllama sem modo JSON, criado por requisição.
    agent_llm: BaseLanguageModel | None = None
    coinpaprika: CoinPaprikaClient | None = None
    knowledge: KnowledgeSearcher | None = None
    sessions: SessionStore | None = None
    # Fase 3: conversa persistente.
    conversation_settings: ConversationSettings = field(default_factory=get_conversation_settings)
    conversation_store: ConversationStore | None = None
    memory_backend: MemoryBackend | None = None
    conversation: ConversationService | None = None
    background_maintenance: bool = True
    # Fase 4: banco de snapshots (Text-to-Query) e Retrieval Agent com Query Rewriting.
    market_settings: MarketDataSettings = field(default_factory=get_market_data_settings)
    retrieval_settings: RetrievalSettings = field(default_factory=get_retrieval_settings)
    market_store: MarketDataStore | None = None
    text_to_query: TextToQueryService | None = None
    retrieval: RetrievalAgent | None = None
    # Fase 5: agente integrado (tool calling nativo) + verificação de respostas.
    # tool_llm: ChatOllama sem modo JSON (tool calling); a verificação usa ``llm`` (modo JSON).
    tool_llm: BaseChatModel | None = None
    integrated: IntegratedAgentService | None = None
    _routers: dict[bool, Runnable] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.llm = self.llm or build_llm(self.settings)
        self.coinpaprika = self.coinpaprika or CoinPaprikaClient()
        self.knowledge = self.knowledge or KnowledgeSearcher(self.knowledge_settings)
        self.sessions = self.sessions or SessionStore(self.agent_settings)
        self.conversation_store = self.conversation_store or ConversationStore(
            project_path(self.conversation_settings.db_path)
        )
        self.memory_backend = self.memory_backend or LazyMemoryBackend(
            lambda: Mem0Backend(self.settings, self.conversation_settings, self.knowledge_settings)
        )
        self.conversation = self.conversation or ConversationService(
            self.conversation_store, self.memory_backend, self.llm, self.coinpaprika, self.knowledge,
            self.conversation_settings, background=self.background_maintenance,
        )
        self.market_store = self.market_store or MarketDataStore(project_path(self.market_settings.db_path))
        self.text_to_query = self.text_to_query or TextToQueryService(self.llm, self.market_store, self.market_settings)
        self.retrieval = self.retrieval or RetrievalAgent(
            self.knowledge.index, QueryRewriter(self.llm), self.retrieval_settings
        )

        if self.integrated is None:
            verification = get_verification_settings()
            self.tool_llm = self.tool_llm or build_llm(self.settings, json_mode=False)
            tools = structured_tools(build_agent_tools(self.coinpaprika, self.knowledge, self.retrieval, self.text_to_query))
            self.integrated = IntegratedAgentService(
                build_integrated_agent(self.tool_llm, tools),
                VerificationService(self.llm, verification.coherence_llm, verification.evidence_per_claim),
                self.conversation_store,
                SessionHistoryAdapter(self.conversation_store, self.memory_backend, self.conversation_settings),
                self.conversation,
                model_name=self.settings.ollama_model,
            )

    def market_ingestor(self) -> MarketDataIngestor:
        return MarketDataIngestor(self.coinpaprika, self.market_store)

    def agent(self, session_id: str) -> CryptoAgent:
        return build_agent(
            self.coinpaprika, self.knowledge, self.agent_settings, self.sessions.get(session_id),
            self.settings, self.agent_llm, retrieval=self.retrieval, market_db=self.text_to_query,
        )

    def router(self, legacy: bool = False) -> Runnable:
        if legacy not in self._routers:
            self._routers[legacy] = build_router(self.llm, self.coinpaprika, self.knowledge, legacy)
        return self._routers[legacy]

    def close(self) -> None:
        self.conversation.close()
        self.coinpaprika.close()
