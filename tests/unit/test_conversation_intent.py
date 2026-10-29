"""Classificação, extração de entidades e resolução de contexto (sem LLM)."""

from datetime import UTC, datetime

import pytest

from app.intent.classifier import classify_message, find_assets, find_period, rule_signals
from app.intent.resolver import active_context_from, resolve
from app.intent.schemas import ConversationIntent as I
from app.persistence.sqlite_store import StoredAnalysis


def step(message, active=None, analyses=()):
    extracted = classify_message(message, llm=None, active_context=active)
    resolved = resolve(extracted, active, list(analyses))
    return extracted, resolved


def analysis(aid, assets, intent="asset_comparison", period=None):
    now = datetime(2026, 10, 28, 12, tzinfo=UTC)
    return StoredAnalysis(aid, "s1", 2, intent, {"assets": assets, "period": period}, {}, now, now, True)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Compare BTC and ETH", ["BTC", "ETH"]),
        ("compare bitcoin e ethereum", ["BTC", "ETH"]),
        ("o sol está forte hoje", []),  # "sol" minúsculo é palavra comum
        ("E o SOL?", ["SOL"]),
        ("Analise o Aave e o UNI", ["AAVE", "UNI"]),
    ],
)
def test_find_assets(text, expected):
    assert find_assets(text) == expected


@pytest.mark.parametrize(
    "text, days",
    [("Considere os últimos 30 dias", 30), ("consider the last 30 days", 30), ("I meant 30 days, not 7", 30),
     ("nas últimas 2 semanas", 14), ("últimas 24 horas", 1), ("sem período", None)],
)
def test_find_period(text, days):
    assert find_period(text)[1] == days


def test_correction_and_previous_reference_signals():
    assert rule_signals("Eu quis dizer 30 dias, não 7").correction
    assert rule_signals("I meant 30 days, not 7").correction
    assert rule_signals("Agora compare com o resultado anterior").previous_reference
    assert rule_signals("Now compare them with the previous result").previous_reference


def test_scenario_a_follow_up_keeps_assets_and_adds_period():
    _, first = step("Compare BTC e ETH no mercado atual")
    assert first.effective_intent == I.ASSET_COMPARISON and first.assets == ["BTC", "ETH"]
    extracted, second = step("Considere os últimos 30 dias", active_context_from(first))
    assert extracted.intent == I.FOLLOW_UP
    assert second.assets == ["BTC", "ETH"] and second.period_days == 30
    assert second.effective_intent == I.ASSET_COMPARISON
    assert second.context_sources == {"assets": "active_context", "period": "current_message"}
    assert second.clarification_question is None


def test_scenario_b_defi_dimension_preserves_assets_and_period():
    _, first = step("Compare BTC e ETH nos últimos 30 dias")
    _, second = step("E quanto a DeFi?", active_context_from(first))
    assert second.effective_intent == I.DEFI_ANALYSIS
    assert second.assets == ["BTC", "ETH"] and second.period_days == 30


def test_scenario_c_previous_result_single_candidate():
    _, first = step("Compare BTC e ETH")
    _, second = step("Agora compare com o resultado anterior", active_context_from(first), [analysis("a1", ["BTC", "ETH"])])
    assert second.effective_intent == I.PREVIOUS_RESULT_REFERENCE
    assert second.referenced_analysis_id == "a1" and second.assets == ["BTC", "ETH"]


def test_scenario_c_ambiguous_previous_result_asks():
    _, resolved = step("Compare com o resultado anterior", None, [analysis("a1", ["BTC", "ETH"]), analysis("a2", ["SOL"], "market_analysis")])
    assert resolved.clarification_question and "a1" in resolved.clarification_question and "a2" in resolved.clarification_question


def test_scenario_c_without_previous_analysis_asks():
    _, resolved = step("Compare com o resultado anterior")
    assert resolved.clarification_question


def test_scenario_d_correction_replaces_period():
    _, first = step("Analise BTC nos últimos 7 dias")
    assert first.period_days == 7 and first.effective_intent == I.MARKET_ANALYSIS
    extracted, second = step("Eu quis dizer 30 dias, não 7", active_context_from(first))
    assert extracted.is_correction
    assert second.period_days == 30 and second.assets == ["BTC"]
    assert second.effective_intent == I.HISTORICAL_ANALYSIS
    assert second.is_correction


def test_new_topic_does_not_inherit_previous_entities():
    _, first = step("Compare BTC e ETH nos últimos 30 dias")
    _, second = step("Qual o preço do SOL?", active_context_from(first))
    assert second.assets == ["SOL"] and second.period is None
    assert second.new_topic


def test_concept_question_does_not_carry_context():
    _, first = step("Compare BTC e ETH")
    _, second = step("O que é staking?", active_context_from(first))
    assert second.effective_intent == I.GENERAL_RESEARCH and second.assets == []


def test_market_request_without_asset_asks_instead_of_guessing():
    _, resolved = step("Qual o preço atual?")
    assert resolved.clarification_question


def test_follow_up_without_context_asks():
    _, resolved = step("Considere os últimos 30 dias")
    assert resolved.clarification_question


def test_previous_reference_uses_active_assets_only_with_anaphora():
    analyses = [analysis("a1", ["BTC", "ETH"]), analysis("a2", ["SOL"], "market_analysis")]
    active = {"assets": ["BTC", "ETH"], "effective_intent": "asset_comparison"}
    _, explicit = step("Now compare them with the previous result", active, analyses)
    assert explicit.referenced_analysis_id == "a1"
    _, vague = step("Compare com o resultado anterior", active, analyses)
    assert vague.clarification_question
