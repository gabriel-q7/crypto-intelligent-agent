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


PROFILE = {
    "id": "sol-solana", "name": "Solana", "symbol": "SOL", "type": "coin", "is_active": True,
    "started_at": "2020-04-10T00:00:00Z", "development_status": "Working product",
    "proof_type": "Delegated Proof of Stake", "hash_algorithm": "Tower BFT", "org_structure": "Centralized > Hierarchical",
    "open_source": True,
    "description": "Solana (SOL) is a high-performance blockchain platform designed for decentralized applications. "
                   "Source: CoinPaprika.com",
    "whitepaper": {"link": "https://static.coinpaprika.com/whitepapers/sol-solana/whitepaper.pdf"},
    "links": {"website": ["https://solana.com"], "source_code": ["https://github.com/solana-labs/"]},
    "tags": [{"name": "Layer 1 (L1)"}, {"name": "Byzantine Fault Tolerance"}],
}


def profile_router(request):
    if request.url.path == "/v1/coins/sol-solana":
        return httpx.Response(200, json=PROFILE)
    return router(request)


def test_asset_profile_tool_is_opt_in():
    client = CoinPaprikaClient(CoinPaprikaSettings(base_url="https://test.local/v1"),
                               transport=httpx.MockTransport(profile_router), sleep=lambda _: None)
    assert "get_asset_profile" not in {t.name for t in build_market_tools(client)}
    assert "get_asset_profile" in {t.name for t in build_market_tools(client, profile=True)}


def test_asset_profile_returns_technical_fields_without_prices():
    client = CoinPaprikaClient(CoinPaprikaSettings(base_url="https://test.local/v1"),
                               transport=httpx.MockTransport(profile_router), sleep=lambda _: None)
    tool = {t.name: t for t in build_market_tools(client, profile=True)}["get_asset_profile"]
    result = json.loads(tool.invoke("sol-solana"))
    data = result["ok"] and result["data"]
    assert data["proof_type"] == "Delegated Proof of Stake" and "hash_algorithm" not in data  # defasado na fonte
    assert data["open_source"] is True and data["website"] == "https://solana.com"
    assert data["whitepaper_url"].endswith("whitepaper.pdf") and data["source_code"] == "https://github.com/solana-labs/"
    assert data["description"].endswith("decentralized applications.")  # crédito "Source: CoinPaprika.com" removido
    assert "price_usd" not in data and result["source"]["endpoint"] == "/coins/sol-solana"


def test_long_description_is_truncated():
    from app.tools.coinpaprika import DESCRIPTION_MAX_CHARS, clean_description

    text = clean_description("palavra " * 500)
    assert len(text) <= DESCRIPTION_MAX_CHARS + 1 and text.endswith("…")
    assert clean_description(None) is None and clean_description("Source: CoinPaprika.com") is None


def test_profile_becomes_portuguese_evidence_and_finding():
    from app.services.grounding import findings_from
    from app.verification.evidence import build_evidence

    data = {"name": "Solana", "symbol": "SOL", "proof_type": "Delegated Proof of Stake",
            "open_source": True, "description": "High-performance blockchain.", "tags": ["Layer 1 (L1)"]}
    obs = {"ok": True, "tool": "get_asset_profile", "data": data,
           "source": {"provider": "CoinPaprika", "endpoint": "/coins/sol-solana", "retrieved_at": "2026-11-12T00:00:00Z"}}
    [item] = build_evidence([obs])
    assert item.kind == "api" and "SOL" in item.entities
    assert "consenso Delegated Proof of Stake" in item.text
    assert "código aberto" in item.text and "descrição: High-performance blockchain." in item.text
    assert findings_from([obs])[0].statement == "Solana: perfil técnico — consenso Delegated Proof of Stake"
