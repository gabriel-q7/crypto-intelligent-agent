"""Testes de integração: exigem Ollama rodando com o modelo configurado.

Execução: pytest -m integration
(no Docker: docker compose run --rm app pytest -m integration)
"""

import httpx
import pytest

from app.chains import build_imperative_chain, build_lcel_chain
from app.config import get_settings
from app.schemas import AnalysisIntent, CryptoAnalysisRequest, CryptoQuestion
from app.services.analysis import run_analysis, run_batch, stream_analysis
from app.services.llm import build_llm

pytestmark = pytest.mark.integration


def _model_available() -> bool:
    settings = get_settings()
    try:
        tags = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3).json()
    except httpx.HTTPError:
        return False
    names = {m["name"] for m in tags.get("models", [])}
    return settings.ollama_model in names


@pytest.fixture(scope="module")
def llm():
    if not _model_available():
        pytest.skip("Ollama indisponível ou modelo não baixado")
    return build_llm()


QUESTION = CryptoQuestion(
    question="Compare a volatilidade de BTC e ETH", assets=["BTC", "ETH"], period="últimos 90 dias"
)


@pytest.mark.parametrize("builder", [build_lcel_chain, build_imperative_chain])
def test_invoke_returns_valid_analysis(llm, builder):
    result = run_analysis(builder(llm), QUESTION)
    assert isinstance(result, CryptoAnalysisRequest)
    assert {"BTC", "ETH"} <= set(result.assets)
    assert result.investigation_steps


def test_batch(llm):
    questions = [QUESTION, CryptoQuestion(question="O que é staking de ETH?")]
    results = run_batch(build_lcel_chain(llm), questions)
    assert all(isinstance(r, CryptoAnalysisRequest) for r in results)


def test_stream(llm):
    partials = []
    result = stream_analysis(llm, QUESTION, on_partial=partials.append)
    assert len(partials) > 1
    assert isinstance(result.intent, AnalysisIntent)
