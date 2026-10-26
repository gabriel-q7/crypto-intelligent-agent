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
