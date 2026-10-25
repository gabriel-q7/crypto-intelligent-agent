import json

import httpx
import pytest

from app.config import CoinPaprikaSettings
from app.tools.coinpaprika import CoinPaprikaClient
from app.tools.market_tools import AssetInput, build_market_tools
from tests.unit.test_coinpaprika_client import SEARCH, TICKER


def tools_with(handler) -> dict:
    client = CoinPaprikaClient(
        CoinPaprikaSettings(base_url="https://test.local/v1"),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    return {tool.name: tool for tool in build_market_tools(client)}


def router(request):
    if request.url.path.startswith("/v1/search"):
        return httpx.Response(200, json=SEARCH)
    if request.url.path == "/v1/tickers/btc-bitcoin":
        return httpx.Response(200, json=TICKER)
    return httpx.Response(404, json={"error": "id not found"})


@pytest.mark.parametrize(
    "raw, expected",
    [("BTC", "BTC"), ('"bitcoin"', "bitcoin"), ("asset=eth", "eth"), ("Query: sol\nObservation", "sol"), (" `btc-bitcoin` ", "btc-bitcoin")],
)
def test_input_cleaning(raw, expected):
    assert AssetInput(value=raw).value == expected


def test_tools_are_single_input_for_zero_shot_react():
    tools = tools_with(router)
    assert set(tools) == {"search_crypto_asset", "get_market_data", "get_market_details", "get_global_market"}
    assert all(len(t.args) == 1 for t in tools.values())


def test_market_data_resolves_symbol_and_keeps_timestamps():
    result = json.loads(tools_with(router)["get_market_data"].invoke("BTC"))
    assert result["ok"] is True
    assert result["data"]["price_usd"] == 81545.05
    assert result["source"]["source_updated_at"] == "2026-10-20T00:50:17Z"
    assert result["source"]["retrieved_at"]


def test_market_data_error_is_explicit_json_without_values():
    result = json.loads(tools_with(router)["get_market_data"].invoke("xyz-unknown"))
    assert result["ok"] is False
    assert result["error"]["kind"] == "not_found"
    assert "data" not in result
    assert "Não invente" in result["instruction"]


def test_empty_input_rejected():
    result = json.loads(tools_with(router)["get_market_data"].invoke("   "))
    assert result["error"]["kind"] == "invalid_argument"


def test_global_tool_ignores_input_and_reports_failure():
    result = json.loads(tools_with(lambda r: httpx.Response(500))["get_global_market"].invoke("none"))
    assert result["ok"] is False and result["error"]["kind"] == "http_error"
