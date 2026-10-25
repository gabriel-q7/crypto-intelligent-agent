import pytest

from app.services.grounding import (
    extract_figures,
    findings_from,
    format_usd,
    parse_number,
    sources_from,
    verify_figures,
)

OBS_BTC = {
    "ok": True,
    "tool": "get_market_data",
    "data": {"name": "Bitcoin", "symbol": "BTC", "price_usd": 81528.29, "percent_change_24h": -1.11,
             "percent_change_7d": -0.89, "market_cap_usd": 1638778050800, "volume_24h_usd": 19198782038.89},
    "source": {"provider": "CoinPaprika", "endpoint": "/tickers/btc-bitcoin",
               "source_updated_at": "2026-10-20T00:55:15Z", "retrieved_at": "2026-10-20T00:59:57Z"},
}
OBS_FAIL = {"ok": False, "tool": "get_market_data", "error": {"kind": "rate_limited", "message": "x"}}


@pytest.mark.parametrize(
    "raw, value",
    [("81.528,29", 81528.29), ("81,528.29", 81528.29), ("81528.29", 81528.29), ("1,11", 1.11),
     ("2.394", 2394), ("81,528", 81528), ("-1,11", -1.11), ("12.", 12)],
)
def test_parse_number_formats(raw, value):
    assert parse_number(raw) == pytest.approx(value)


def test_extract_figures_currency_percent_and_multipliers():
    figures = dict(extract_figures("Preço US$ 81.528,29, queda de -1,11% e market cap de 1,64 trilhão de dólares; volume $19,2 bi."))
    assert figures["US$ 81.528,29"] == pytest.approx(81528.29)
    assert figures["-1,11%"] == pytest.approx(-1.11)
    assert figures["1,64 trilhão de dólares"] == pytest.approx(1.64e12)
    assert figures["$19,2 bi"] == pytest.approx(19.2e9)


def test_plain_numbers_are_not_market_figures():
    assert extract_figures("Em 2024 houve o halving; limite de 21 milhões de BTC.") == []


def test_verify_keeps_grounded_and_removes_invented_sentences():
    answer = "O BTC está em US$ 81.528,29 (CoinPaprika). Caiu 1,11% em 24h. Deve chegar a US$ 100.000 amanhã."
    text, removed = verify_figures(answer, [OBS_BTC])
    assert "81.528,29" in text and "1,11%" in text
    assert "100.000" not in text
    assert removed == ["US$ 100.000"]


def test_rounded_values_within_tolerance_are_grounded():
    text, removed = verify_figures("Market cap de cerca de US$ 1,64 trilhão.", [OBS_BTC])
    assert removed == [] and text


def test_values_from_failed_observations_are_not_evidence():
    _, removed = verify_figures("O BTC está em US$ 81.528,29.", [{**OBS_FAIL, "data": {"price_usd": 81528.29}}])
    assert removed == ["US$ 81.528,29"]


def test_findings_and_sources_come_from_successful_observations_only():
    findings = findings_from([OBS_BTC, OBS_FAIL])
    assert findings[0].statement == "Bitcoin (BTC): preço US$ 81.528,29"
    assert all(f.source_updated_at.isoformat() == "2026-10-20T00:55:15+00:00" for f in findings)
    sources = sources_from([OBS_BTC, OBS_FAIL])
    assert len(sources) == 1 and sources[0].endpoint == "/tickers/btc-bitcoin"


def test_format_usd():
    assert format_usd(81528.29) == "US$ 81.528,29"
    assert format_usd(0.000123) == "US$ 0,000123"
    assert format_usd(None) == "n/d"


def test_ambiguous_separator_with_multiplier_accepts_both_readings():
    # "1.638 trilhões" no estilo en (1,638 tri) corresponde ao market cap observado.
    text, removed = verify_figures("Market cap do BTC: $1.638 trilhões.", [OBS_BTC])
    assert removed == [] and text
    _, removed = verify_figures("Market cap: $9.999 trilhões.", [OBS_BTC])
    assert removed == ["$9.999 trilhões"]
