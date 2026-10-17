from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from app.config import Settings, get_settings


def build_llm(settings: Settings | None = None) -> BaseChatModel:
    """Cria o ChatOllama configurado por variáveis de ambiente."""
    settings = settings or get_settings()
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=settings.ollama_temperature,
        # Modo JSON do Ollama: restringe a geração a JSON sintaticamente válido.
        format="json",
        client_kwargs={"timeout": settings.ollama_request_timeout},
    )
