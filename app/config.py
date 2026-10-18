"""Configuração da aplicação lida de variáveis de ambiente (.env)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


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
    )
