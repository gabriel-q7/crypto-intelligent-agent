"""Configuração da aplicação lida de variáveis de ambiente (.env)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Raiz do projeto: caminhos relativos da configuração (corpus, índice, benchmarks) são
# resolvidos a partir dela, e não da pasta em que o comando foi executado.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def project_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    ollama_model: str = "gemma4:e2b"
    ollama_base_url: str = "http://localhost:11434"
    ollama_temperature: float = 0.0
    ollama_request_timeout: float = 300.0
    # Raciocínio interno ("thinking") de modelos que suportam, ex.: gemma4.
    # None = padrão do modelo; False desliga (menos tokens, menor latência).
    # Padrão False: no benchmark o thinking não melhorou a acurácia e aumentou a latência.
    ollama_reasoning: bool | None = False
    # Janela de contexto. O padrão do Ollama (4096) trunca em silêncio o prompt do agente ReAct
    # (PTCF + exemplos + ferramentas + histórico + observações).
    ollama_num_ctx: int = 8192


def parse_optional_bool(value: str | None) -> bool | None:
    """'true'/'false' (e variações) -> bool; vazio ou ausente -> None."""
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"valor booleano inválido: {value!r}")


def get_settings() -> Settings:
    defaults = Settings()
    return Settings(
        ollama_model=os.getenv("OLLAMA_MODEL", defaults.ollama_model),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", defaults.ollama_base_url),
        ollama_temperature=float(os.getenv("OLLAMA_TEMPERATURE", defaults.ollama_temperature)),
        ollama_request_timeout=float(
            os.getenv("OLLAMA_REQUEST_TIMEOUT", defaults.ollama_request_timeout)
        ),
        # Ausente -> padrão do projeto (False); vazio -> padrão do modelo (None).
        ollama_reasoning=(
            defaults.ollama_reasoning
            if "OLLAMA_REASONING" not in os.environ
            else parse_optional_bool(os.environ["OLLAMA_REASONING"])
        ),
        ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX") or defaults.ollama_num_ctx),
    )


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name) or default)


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name) or default)


@dataclass(frozen=True)
class CoinPaprikaSettings:
    # Plano gratuito não exige chave; com chave, use a URL do plano pago (api-pro).
    base_url: str = "https://api.coinpaprika.com/v1"
    api_key: str | None = None
    timeout_s: float = 15.0
    max_retries: int = 2
    # Espera máxima aceitável por Retry-After antes de desistir (cota mensal reseta em dias).
    max_retry_wait_s: float = 10.0


def get_coinpaprika_settings() -> CoinPaprikaSettings:
    defaults = CoinPaprikaSettings()
    return CoinPaprikaSettings(
        base_url=os.getenv("COINPAPRIKA_BASE_URL") or defaults.base_url,
        api_key=os.getenv("COINPAPRIKA_API_KEY") or None,
        timeout_s=_env_float("COINPAPRIKA_TIMEOUT", defaults.timeout_s),
        max_retries=_env_int("COINPAPRIKA_MAX_RETRIES", defaults.max_retries),
        max_retry_wait_s=_env_float("COINPAPRIKA_MAX_RETRY_WAIT", defaults.max_retry_wait_s),
    )


@dataclass(frozen=True)
class AgentSettings:
    max_iterations: int = 6
    max_execution_time_s: float = 600.0
    memory_max_turns: int = 4
    memory_max_chars: int = 3000


def get_agent_settings() -> AgentSettings:
    defaults = AgentSettings()
    return AgentSettings(
        max_iterations=_env_int("AGENT_MAX_ITERATIONS", defaults.max_iterations),
        max_execution_time_s=_env_float("AGENT_MAX_EXECUTION_TIME", defaults.max_execution_time_s),
        memory_max_turns=_env_int("MEMORY_MAX_TURNS", defaults.memory_max_turns),
        memory_max_chars=_env_int("MEMORY_MAX_CHARS", defaults.memory_max_chars),
    )


@dataclass(frozen=True)
class KnowledgeSettings:
    embedding_model: str = "nomic-embed-text"
    corpus_dir: str = "knowledge/corpus"
    store_dir: str = "data/chroma"
    collection: str = "crypto_knowledge"
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 3


def get_knowledge_settings() -> KnowledgeSettings:
    defaults = KnowledgeSettings()
    return KnowledgeSettings(
        embedding_model=os.getenv("EMBEDDING_MODEL") or defaults.embedding_model,
        corpus_dir=os.getenv("KNOWLEDGE_CORPUS_DIR") or defaults.corpus_dir,
        store_dir=os.getenv("VECTOR_STORE_DIR") or defaults.store_dir,
        collection=os.getenv("KNOWLEDGE_COLLECTION") or defaults.collection,
        chunk_size=_env_int("KNOWLEDGE_CHUNK_SIZE", defaults.chunk_size),
        chunk_overlap=_env_int("KNOWLEDGE_CHUNK_OVERLAP", defaults.chunk_overlap),
        top_k=_env_int("KNOWLEDGE_TOP_K", defaults.top_k),
    )


@dataclass(frozen=True)
class ConversationSettings:
    # SQLite: fonte da verdade de sessões, mensagens, resumos e análises.
    db_path: str = "data/conversations.sqlite3"
    # Janela curta: últimas N mensagens (usuário + assistente) enviadas ao modelo.
    window_messages: int = 10
    # Resumo: disparado quando há pelo menos N mensagens fora da janela ainda não resumidas.
    summary_trigger_messages: int = 4
    summary_max_chars: int = 1500
    # Orçamento de contexto da conversa (tokens estimados) dentro de OLLAMA_NUM_CTX.
    context_budget_tokens: int = 3000
    # Memória longa (Mem0 sobre Chroma): diretório e coleção próprios, separados do corpus.
    memory_store_dir: str = "data/memory_chroma"
    memory_collection: str = "conversation_memory"
    # Histórico interno do Mem0 (eventos ADD/UPDATE/DELETE), em SQLite separado.
    memory_history_db: str = "data/mem0_history.sqlite3"
    # Dimensão do nomic-embed-text.
    embedding_dims: int = 768
    memory_top_k: int = 3
    # Score mínimo de similaridade do Mem0 para uma memória entrar no contexto.
    memory_min_score: float = 0.3
    # Extração semântica (Mem0 infer=True) em segundo plano; desligável.
    memory_extraction: bool = True
    # Resultado anterior com dados mais antigos que isso é sempre rotulado como histórico.
    stale_after_minutes: int = 15


def get_conversation_settings() -> ConversationSettings:
    defaults = ConversationSettings()
    return ConversationSettings(
        db_path=os.getenv("CONVERSATION_DB_PATH") or defaults.db_path,
        window_messages=_env_int("MEMORY_WINDOW_MESSAGES", defaults.window_messages),
        summary_trigger_messages=_env_int("SUMMARY_TRIGGER_MESSAGES", defaults.summary_trigger_messages),
        summary_max_chars=_env_int("SUMMARY_MAX_CHARS", defaults.summary_max_chars),
        context_budget_tokens=_env_int("CONTEXT_BUDGET_TOKENS", defaults.context_budget_tokens),
        memory_store_dir=os.getenv("MEMORY_STORE_DIR") or defaults.memory_store_dir,
        memory_collection=os.getenv("MEMORY_COLLECTION") or defaults.memory_collection,
        memory_history_db=os.getenv("MEMORY_HISTORY_DB") or defaults.memory_history_db,
        embedding_dims=_env_int("EMBEDDING_DIMS", defaults.embedding_dims),
        memory_top_k=_env_int("MEMORY_TOP_K", defaults.memory_top_k),
        memory_min_score=_env_float("MEMORY_MIN_SCORE", defaults.memory_min_score),
        memory_extraction=parse_optional_bool(os.getenv("MEMORY_EXTRACTION")) is not False,
        stale_after_minutes=_env_int("STALE_AFTER_MINUTES", defaults.stale_after_minutes),
    )


# Fase 4: ids CoinPaprika coletados por padrão em POST /market-data/ingest.
DEFAULT_MARKET_ASSETS = (
    "btc-bitcoin", "eth-ethereum", "usdt-tether", "bnb-binance-coin", "sol-solana",
    "xrp-xrp", "usdc-usd-coin", "ada-cardano", "doge-dogecoin", "trx-tron",
)


@dataclass(frozen=True)
class MarketDataSettings:
    # Banco SQLite próprio dos snapshots de mercado (separado das conversas da Fase 3).
    db_path: str = "data/market.sqlite3"
    default_assets: tuple[str, ...] = DEFAULT_MARKET_ASSETS
    # Text-to-Query: teto de linhas devolvidas e tempo máximo de execução de uma consulta.
    query_max_rows: int = 50
    query_timeout_s: float = 2.0


def get_market_data_settings() -> MarketDataSettings:
    defaults = MarketDataSettings()
    assets = os.getenv("MARKET_DATA_ASSETS")
    return MarketDataSettings(
        db_path=os.getenv("MARKET_DB_PATH") or defaults.db_path,
        default_assets=tuple(a.strip() for a in assets.split(",") if a.strip()) if assets else defaults.default_assets,
        query_max_rows=_env_int("TEXT_TO_SQL_MAX_ROWS", defaults.query_max_rows),
        query_timeout_s=_env_float("TEXT_TO_SQL_TIMEOUT", defaults.query_timeout_s),
    )


@dataclass(frozen=True)
class RetrievalSettings:
    # auto: reescreve só quando a busca original é fraca; always/never: para avaliação.
    rewrite_mode: str = "auto"
    # Distâncias de cosseno (0 = idêntico), calibradas com nomic-embed-text sobre o corpus atual
    # (docs/phase-4-evidence.md): buscas boas tiveram melhor trecho em 0,20-0,31; falhas de
    # vocabulário, 0,34-0,39; assunto ausente do corpus, acima de 0,43.
    # Melhor trecho acima disso: a pergunta é reescrita (modo auto).
    rewrite_trigger_distance: float = 0.33
    # Trechos acima disso não contam como evidência; sem nenhum abaixo → no_relevant_documents.
    relevance_max_distance: float = 0.38
    top_k: int = 3


def get_retrieval_settings() -> RetrievalSettings:
    defaults = RetrievalSettings()
    mode = (os.getenv("RETRIEVAL_REWRITE_MODE") or defaults.rewrite_mode).strip().lower()
    if mode not in {"auto", "always", "never"}:
        raise ValueError(f"RETRIEVAL_REWRITE_MODE inválido: {mode!r} (auto, always, never)")
    return RetrievalSettings(
        rewrite_mode=mode,
        rewrite_trigger_distance=_env_float("RETRIEVAL_REWRITE_DISTANCE", defaults.rewrite_trigger_distance),
        relevance_max_distance=_env_float("RETRIEVAL_MAX_DISTANCE", defaults.relevance_max_distance),
        top_k=_env_int("KNOWLEDGE_TOP_K", defaults.top_k),
    )


@dataclass(frozen=True)
class VerificationSettings:
    # Checagem de coerência lógica via LLM (as regras determinísticas rodam sempre).
    coherence_llm: bool = True
    # Evidências comparadas por afirmação (seleção por relevância, não todas contra todas).
    evidence_per_claim: int = 3


def get_verification_settings() -> VerificationSettings:
    defaults = VerificationSettings()
    return VerificationSettings(
        coherence_llm=parse_optional_bool(os.getenv("VERIFICATION_COHERENCE_LLM")) is not False,
        evidence_per_claim=_env_int("VERIFICATION_EVIDENCE_PER_CLAIM", defaults.evidence_per_claim),
    )
