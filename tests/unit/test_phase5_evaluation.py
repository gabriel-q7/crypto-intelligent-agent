"""Fase 5: dataset de avaliação e avaliadores (determinísticos e LLM-juiz roteirizado)."""

import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.evaluation import evaluators as ev
from app.evaluation.runner import _summarize, example_uuid, load_dataset
from app.evaluation.target import _fill
from tests.unit.phase5_fakes import OBS_BTC, OBS_SQL_FAIL

AGENT = {"mode": "agent", "question": "q"}


def test_dataset_is_reproducible_and_covers_required_categories():
    data = load_dataset()
    assert 15 <= len(data["examples"]) <= 20
    categories = {e["category"] for e in data["examples"]}
    assert {"api", "sql", "rag", "api+sql", "api+rag", "factual_verification", "logical_verification",
            "memory", "error_handling"} <= categories
    assert len({e["id"] for e in data["examples"]}) == len(data["examples"])
    assert example_uuid("api-01") == example_uuid("api-01") != example_uuid("api-02")


def test_trajectory_evaluators():
    ref = {"expected_categories": ["api", "rag"], "required_tools": ["get_market_data", "search_knowledge_base"],
           "forbidden_tools": ["query_market_database"], "expected_assets": ["ETH"]}
    good = {"tools": ["get_market_data", "search_knowledge_base"], "categories": ["api", "rag"],
            "steps": [{"tool": "get_market_data", "args": {"asset": "ethereum"}, "ok": True}]}
    bad = {"tools": ["get_market_data", "query_market_database"], "categories": ["api", "sql"],
           "steps": [{"tool": "get_market_data", "args": {"asset": "BTC"}, "ok": True}]}
    for fn, g, b in [(ev.tool_selection, 1.0, 0.5), (ev.required_tools, 1.0, 0.5), (ev.no_unnecessary_tools, 1.0, 0.0),
                     (ev.tool_combination, 1.0, 0.0), (ev.tool_inputs, 1.0, 0.0)]:
        assert fn(AGENT, good, ref)["score"] == g and fn(AGENT, bad, ref)["score"] == b, fn.__name__
    assert ev.tool_selection(AGENT, {"categories": []}, {"expected_categories": []})["score"] == 1.0
    assert ev.tool_combination(AGENT, good, {"expected_categories": ["api"]})["score"] is None


def test_error_handling_and_uncertainty():
    ref = {"expected_error_kinds": ["insufficient_coverage"], "uncertainty_expected": True}
    out = {"answer": "Os snapshots não cobrem 90 dias.", "steps": [{"tool": "query_market_database", "ok": False,
                                                                     "error_kind": "insufficient_coverage"}]}
    assert ev.error_handling(AGENT, out, ref)["score"] == 1.0 and ev.uncertainty(AGENT, out, ref)["score"] == 1.0
    silent = {"answer": "O SOL subiu mais.", "steps": out["steps"]}
    assert ev.error_handling(AGENT, silent, ref)["score"] == 0.5 and ev.uncertainty(AGENT, silent, ref)["score"] == 0.0


def test_answer_evaluators():
    out = {"answer": "O BTC está em US$ 81.545,05 e US$ 5,55.", "observations": [OBS_BTC],
           "verification": {"status": "verified", "labels": ["entailment"], "findings": []}}
    assert ev.groundedness(AGENT, out, {})["score"] == 0.5
    assert ev.evidence_used(AGENT, out, {})["score"] == 1.0
    assert ev.factual_consistency(AGENT, out, {})["score"] == 1.0 and ev.logical_coherence(AGENT, out, {})["score"] == 1.0
    verify = {"mode": "verify"}
    planted = {"verification": {"status": "contradicted", "labels": ["contradiction"], "findings": ["calculation"]}}
    assert ev.factual_consistency(verify, planted, {"expected_verification": ["contradicted"], "expected_labels": ["contradiction"]})["score"] == 1.0
    assert ev.logical_coherence(verify, planted, {"expected_findings": ["calculation"]})["score"] == 1.0
    assert ev.logical_coherence(verify, planted, {"expected_findings": ["direction_mismatch"]})["score"] == 0.0
    assert ev.groundedness(verify, planted, {})["score"] is None


def test_llm_judge_and_required_traits():
    judge = ev.llm_judges(FakeListChatModel(responses=[json.dumps({"score": 0.9, "reason": "ok"}), "quebrado"]))
    correctness, relevance = judge
    result = correctness(AGENT, {"answer": "sem o termo"}, {"answer_must_include_any": ["halving"]})
    assert result["score"] == 0.5 and "faltou" in result["comment"]
    assert relevance(AGENT, {"answer": "x"}, {})["score"] is None  # juiz indisponível não vira nota


def test_placeholders_filled_with_real_values_and_summary():
    text = _fill("BTC a {BTC.price_usd_fmt}; {BTC.opposite_verb} {BTC.abs_change_24h_fmt}.", [OBS_BTC, OBS_SQL_FAIL])
    assert text == "BTC a US$ 81.545,05; subiu 1,02%."
    rows = [{"category": "api", "scores": {"tool_selection": {"score": 1.0}, "correctness": {"score": 0.5}, "x": {"score": None}}}]
    summary = _summarize(rows)
    assert summary["trajectory_mean"] == 1.0 and summary["answer_mean"] == 0.5 and summary["by_category"]["api"]["correctness"] == 0.5
