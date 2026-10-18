import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.services.benchmark import (
    BenchmarkCase,
    RunResult,
    comparison_table,
    load_cases,
    run_case,
    summarize,
)
from tests.conftest import VALID_OUTPUT

CASE = BenchmarkCase(
    id="comparacao",
    question="Compare BTC e ETH",
    expected_status="requires_market_data",
    expected_assets=["btc", "eth"],
)


def ollama_like(content: str, **metadata):
    """LLM falso que devolve metadados no formato do Ollama (durações em ns)."""
    return RunnableLambda(lambda _: AIMessage(content=content, response_metadata=metadata))


def test_project_cases_file_is_valid():
    cases = load_cases(__import__("pathlib").Path("benchmarks/questions.json"))
    assert len(cases) >= 5
    assert len({c.id for c in cases}) == len(cases)


def test_run_case_collects_ollama_metrics():
    llm = ollama_like(
        json.dumps(VALID_OUTPUT),
        load_duration=2_000_000_000,
        prompt_eval_count=1000,
        prompt_eval_duration=4_000_000_000,
        eval_count=100,
        eval_duration=10_000_000_000,
    )
    result = run_case(llm, CASE, run=1)
    assert result.valid and result.status_ok and result.assets_ok
    assert result.load_s == 2.0
    assert result.prompt_tokens == 1000
    assert result.tokens_per_s == 10.0
    assert result.output["assets"] == ["BTC", "ETH"]


def test_run_case_without_metadata():
    result = run_case(FakeListChatModel(responses=[json.dumps(VALID_OUTPUT)]), CASE, run=1)
    assert result.valid
    assert result.tokens_per_s is None and result.load_s is None


def test_run_case_flags_wrong_expectations():
    output = {**VALID_OUTPUT, "answer_status": "answered", "assets": ["SOL"]}
    result = run_case(ollama_like(json.dumps(output)), CASE, run=1)
    assert result.valid
    assert result.status_ok is False and result.assets_ok is False


def test_run_case_records_invalid_output():
    result = run_case(ollama_like("não é json"), CASE, run=1)
    assert not result.valid
    assert "AnalysisOutputError" in result.error or "OutputParserException" in result.error
    assert result.status_ok is None


def test_summarize_aggregates_runs():
    results = [
        RunResult("a", 1, wall_s=10, valid=True, status_ok=True, assets_ok=True,
                  output_tokens=100, eval_s=10),
        RunResult("a", 2, wall_s=20, valid=True, status_ok=False, assets_ok=True,
                  output_tokens=100, eval_s=5),
        RunResult("b", 1, wall_s=30, valid=False),
    ]
    summary = summarize("m", results, cold_start_s=3.0)
    assert summary.runs == 3
    assert summary.wall_mean_s == 20
    assert summary.wall_median_s == 20
    assert summary.wall_p95_s == 30
    # Soma/soma: 200 tokens em 15 s (a média das taxas daria 15).
    assert summary.output_tokens_per_s == pytest.approx(200 / 15)
    assert summary.valid_rate == pytest.approx(2 / 3)
    assert summary.status_accuracy == 0.5  # inválidos não contam na acurácia
    assert summary.per_case["a"]["wall_mean_s"] == 15
    assert summary.per_case["b"]["valid_rate"] == 0


def test_comparison_table_formats_missing_values():
    table = comparison_table(
        [
            {"model": "x", "reasoning": False, "wall_mean_s": 12.34, "valid_rate": 1.0},
            {"model": "y", "wall_mean_s": 1.0},
        ]
    )
    lines = table.splitlines()
    assert lines[0].startswith("| Modelo | Thinking |")
    assert "| x | desligado | — | 12.3 |" in lines[2]
    assert "100%" in lines[2]
    # Resultados antigos, sem o campo, aparecem como padrão do modelo.
    assert lines[3].startswith("| y | padrão |")
