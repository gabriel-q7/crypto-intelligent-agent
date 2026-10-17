"""Configuração da aplicação lida de variáveis de ambiente (.env)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    ollama_model: str = "qwen2.5:7b"
    ollama_base_url: str = "http://localhost:11434"
    ollama_temperature: float = 0.0
    ollama_request_timeout: float = 180.0


def get_settings() -> Settings:
    defaults = Settings()
    return Settings(
        ollama_model=os.getenv("OLLAMA_MODEL", defaults.ollama_model),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", defaults.ollama_base_url),
        ollama_temperature=float(os.getenv("OLLAMA_TEMPERATURE", defaults.ollama_temperature)),
        ollama_request_timeout=float(
            os.getenv("OLLAMA_REQUEST_TIMEOUT", defaults.ollama_request_timeout)
        ),
    )
