"""Configuração de logging: formato único, níveis por ambiente e redação de segredos."""

import logging
import os

from app.config import get_coinpaprika_settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class SecretRedactionFilter(logging.Filter):
    """Substitui segredos conhecidos (ex.: COINPAPRIKA_API_KEY) por '***' em qualquer log."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            message = record.getMessage()
            for secret in self.secrets:
                message = message.replace(secret, "***")
            record.msg, record.args = message, None
        return True


def configure_logging(level: str | None = None) -> None:
    level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(SecretRedactionFilter([get_coinpaprika_settings().api_key or ""]))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx registra a URL de cada requisição em INFO; o cliente já registra o essencial.
    for noisy in ("httpx", "httpcore", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
