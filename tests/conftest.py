import json
import re

import langsmith
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.schemas import CryptoQuestion

# O .env pode ligar LANGSMITH_TRACING e o container carrega o .env: sem isto, a suíte offline envia
# os traces dos dublês ao LangSmith e polui as métricas do projeto (Fase 5, seção 5.11). Só uma
# execução que seleciona integração/rede (ex.: -m "integration or network") mantém o rastreamento.
POSITIVE_LIVE_MARK = re.compile(r"(?<!not )\b(integration|network)\b")


def pytest_configure(config):
    if not POSITIVE_LIVE_MARK.search(config.option.markexpr or ""):
        langsmith.configure(enabled=False)


VALID_OUTPUT = {
    "intent": "comparison",
    "assets": ["btc", "ETH"],
    "requested_metrics": ["preço", "volume"],
    "period": "últimos 30 dias",
    "investigation_steps": ["Obter série de preços", "Comparar variação percentual"],
    "missing_information": ["moeda de cotação"],
    "assumptions": ["cotação em USD"],
    "answer_status": "requires_market_data",
    "answer": "A comparação depende do histórico de preços de BTC e ETH, ainda não consultado.",
    "confidence": 0.8,
}


@pytest.fixture
def valid_output() -> dict:
    return dict(VALID_OUTPUT)


@pytest.fixture
def question() -> CryptoQuestion:
    return CryptoQuestion(
        question="Compare BTC e ETH", assets=["btc", "eth"], period="últimos 30 dias"
    )


def fake_llm(*responses: str) -> FakeListChatModel:
    return FakeListChatModel(responses=list(responses))


@pytest.fixture
def valid_llm() -> FakeListChatModel:
    return fake_llm(json.dumps(VALID_OUTPUT, ensure_ascii=False))
