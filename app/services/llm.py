import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama

from app.config import Settings, get_settings


def build_llm(settings: Settings | None = None, json_mode: bool = True) -> BaseChatModel:
    """Cria o ChatOllama configurado por variáveis de ambiente.

    ``json_mode=False`` é necessário para o agente ReAct, cujo protocolo é texto
    ("Action:", "Action Input:", "Final Answer:").
    """
    settings = settings or get_settings()
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=settings.ollama_temperature,
        # Modo JSON do Ollama: restringe a geração a JSON sintaticamente válido.
        format="json" if json_mode else None,
        reasoning=settings.ollama_reasoning,
        num_ctx=settings.ollama_num_ctx,
        client_kwargs={"timeout": settings.ollama_request_timeout},
    )


# Falhas transitórias de comunicação com o Ollama (container reiniciando, conexão recusada,
# timeout de rede). Respostas inválidas do modelo NÃO entram: com temperatura 0, repetir
# tende a gerar a mesma saída.
TRANSIENT_LLM_ERRORS: tuple[type[BaseException], ...] = (ConnectionError, httpx.TransportError)


def with_transient_retry(llm: BaseChatModel, attempts: int = 2, wait: bool = True) -> Runnable:
    return llm.with_retry(
        retry_if_exception_type=TRANSIENT_LLM_ERRORS,
        stop_after_attempt=attempts,
        wait_exponential_jitter=wait,
    )
