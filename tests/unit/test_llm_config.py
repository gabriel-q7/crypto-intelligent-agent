import pytest
from langchain_ollama import ChatOllama

from app.chains import build_lcel_chain
from app.config import Settings, get_settings
from app.errors import LLMUnavailableError
from app.services.analysis import run_analysis
from app.services.llm import build_llm


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.3")
    settings = get_settings()
    assert settings.ollama_model == "llama3.2:3b"
    assert settings.ollama_base_url == "http://ollama:11434"
    assert settings.ollama_temperature == 0.3


@pytest.mark.parametrize(
    "raw, expected",
    [(None, False), ("", None), ("  ", None), ("true", True), ("ON", True), ("0", False), ("false", False)],
)
def test_reasoning_from_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("OLLAMA_REASONING", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_REASONING", raw)
    assert get_settings().ollama_reasoning is expected


def test_invalid_reasoning_value(monkeypatch):
    monkeypatch.setenv("OLLAMA_REASONING", "talvez")
    with pytest.raises(ValueError, match="booleano"):
        get_settings()


def test_default_model_is_gemma_without_thinking():
    assert Settings().ollama_model == "gemma4:e2b"
    assert Settings().ollama_reasoning is False


def test_build_llm_uses_settings():
    llm = build_llm(Settings(ollama_model="m", ollama_base_url="http://h:1"))
    assert isinstance(llm, ChatOllama)
    assert llm.model == "m"
    assert llm.base_url == "http://h:1"
    assert llm.format == "json"
    assert llm.reasoning is False
    assert build_llm(Settings(ollama_reasoning=None)).reasoning is None


def test_unreachable_ollama_raises_llm_unavailable(question):
    # Porta fechada local: falha imediata, sem depender de rede externa.
    llm = build_llm(Settings(ollama_base_url="http://127.0.0.1:9", ollama_request_timeout=5))
    try:
        run_analysis(build_lcel_chain(llm), question)
    except LLMUnavailableError:
        return
    raise AssertionError("esperado LLMUnavailableError")
