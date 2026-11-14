import httpx
import pytest

from app.config import CoinPaprikaSettings
from app.tools.coinpaprika import CoinPaprikaClient, CoinPaprikaError, validate_coin_id

TICKER = {
    "id": "btc-bitcoin", "name": "Bitcoin", "symbol": "BTC", "rank": 1,
    "last_updated": "2026-10-20T00:50:17Z", "max_supply": 21000000,
    "quotes": {"USD": {
        "price": 81545.05, "volume_24h": 1.9e10, "market_cap": 1.6e12,
        "percent_change_1h": -0.37, "percent_change_24h": -1.02, "percent_change_7d": -0.84,
        "percent_change_30d": 0, "ath_price": 126173.18, "ath_date": "2025-10-06T19:00:40Z",
        "percent_from_price_ath": -35.33,
    }},
}
SEARCH = {"currencies": [
    {"id": "btcb-bitcoin-bep2", "name": "Bitcoin BEP2", "symbol": "BTCB", "rank": 300, "is_active": True},
    {"id": "btc-bitcoin", "name": "Bitcoin", "symbol": "BTC", "rank": 1, "is_active": True},
]}


def make_client(handler, **settings) -> tuple[CoinPaprikaClient, list[float]]:
    sleeps: list[float] = []
    client = CoinPaprikaClient(
        CoinPaprikaSettings(base_url="https://test.local/v1", **settings),
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    return client, sleeps


def test_ticker_parsed_with_source_timestamps():
    client, _ = make_client(lambda req: httpx.Response(200, json=TICKER))
    ticker = client.get_ticker("btc-bitcoin")
    assert ticker.price_usd == 81545.05
    assert ticker.percent_change_24h == -1.02
    assert ticker.source.endpoint == "/tickers/btc-bitcoin"
    assert ticker.source.source_updated_at.isoformat() == "2026-10-20T00:50:17+00:00"
    assert ticker.source.retrieved_at.tzinfo is not None


def test_api_key_sent_as_header_only_when_configured():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json=TICKER)

    make_client(handler)[0].get_ticker("btc-bitcoin")
    make_client(handler, api_key="secret")[0].get_ticker("btc-bitcoin")
    assert seen == [None, "secret"]


@pytest.mark.parametrize(
    "status, kind",
    [(404, "not_found"), (402, "plan_restricted"), (400, "http_error")],
)
def test_non_retryable_errors(status, kind):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, json={"error": "x"})

    client, sleeps = make_client(handler)
    with pytest.raises(CoinPaprikaError) as info:
        client.get_ticker("btc-bitcoin")
    assert info.value.kind == kind
    assert len(calls) == 1 and sleeps == []


def test_rate_limit_short_retry_after_is_retried():
    responses = iter([httpx.Response(429, headers={"retry-after": "2"}), httpx.Response(200, json=TICKER)])
    client, sleeps = make_client(lambda req: next(responses))
    assert client.get_ticker("btc-bitcoin").symbol == "BTC"
    assert sleeps == [2.0]


def test_rate_limit_long_retry_after_fails_fast():
    client, sleeps = make_client(lambda req: httpx.Response(429, headers={"retry-after": "86400"}))
    with pytest.raises(CoinPaprikaError) as info:
        client.get_ticker("btc-bitcoin")
    assert info.value.kind == "rate_limited" and info.value.retry_after_s == 86400
    assert sleeps == []


def test_server_error_retried_with_backoff_then_fails():
    client, sleeps = make_client(lambda req: httpx.Response(503), max_retries=2)
    with pytest.raises(CoinPaprikaError) as info:
        client.get_ticker("btc-bitcoin")
    assert info.value.kind == "http_error" and info.value.status_code == 503
    assert sleeps == [0.5, 1.0]


def test_timeout_retried_then_reported():
    def handler(request):
        raise httpx.ReadTimeout("lento", request=request)

    client, sleeps = make_client(handler, max_retries=1)
    with pytest.raises(CoinPaprikaError) as info:
        client.get_ticker("btc-bitcoin")
    assert info.value.kind == "timeout" and len(sleeps) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>erro</html>"),
        httpx.Response(200, json={"id": "btc-bitcoin"}),  # sem quotes
        httpx.Response(200, json={**TICKER, "quotes": {"USD": {"price": "caro"}}}),
    ],
)
def test_malformed_responses(response):
    client, _ = make_client(lambda req: response)
    with pytest.raises(CoinPaprikaError) as info:
        client.get_ticker("btc-bitcoin")
    assert info.value.kind == "malformed"


def test_invalid_id_rejected_before_http():
    client, _ = make_client(lambda req: pytest.fail("não deveria chamar a API"))
    with pytest.raises(CoinPaprikaError) as info:
        client.get_ticker("BTC!")
    assert info.value.kind == "invalid_argument"
    assert validate_coin_id(" ETH-Ethereum ") == "eth-ethereum"


def test_resolve_prefers_exact_symbol_and_best_rank():
    client, _ = make_client(lambda req: httpx.Response(200, json=SEARCH))
    assert client.resolve_asset("btc").id == "btc-bitcoin"


def test_resolve_without_results_is_not_found():
    client, _ = make_client(lambda req: httpx.Response(200, json={"currencies": []}))
    with pytest.raises(CoinPaprikaError) as info:
        client.resolve_asset("zzz")
    assert info.value.kind == "not_found"


def test_search_uses_trailing_slash_endpoint():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=SEARCH)

    make_client(handler)[0].search_assets("bitcoin")
    assert paths == ["/v1/search/"]


def test_global_epoch_timestamp_converted():
    payload = {"market_cap_usd": 2.9e12, "volume_24h_usd": 5e11, "bitcoin_dominance_percentage": 57.45,
               "cryptocurrencies_number": 11618, "market_cap_ath_value": 4.8e12, "last_updated": 1792457364}
    market = make_client(lambda req: httpx.Response(200, json=payload))[0].get_global()
    assert market.source.source_updated_at.year == 2026


def test_free_plan_zero_30d_change_is_unavailable():
    """O plano gratuito devolve percent_change_30d = 0 para todos os ativos (preenchimento)."""
    client, _ = make_client(lambda req: httpx.Response(200, json=TICKER))
    assert client.get_ticker("btc-bitcoin").percent_change_30d is None
    real = {**TICKER, "quotes": {"USD": {**TICKER["quotes"]["USD"], "percent_change_30d": -2.5}}}
    client, _ = make_client(lambda req: httpx.Response(200, json=real))
    assert client.get_ticker("btc-bitcoin").percent_change_30d == -2.5


HISTORY = [
    {"timestamp": "2026-11-06T00:00:00Z", "price": 89.99, "volume_24h": 2432262463, "market_cap": 53122377085},
    {"timestamp": "2026-11-07T00:00:00Z", "price": 89.82, "volume_24h": 1034689529, "market_cap": 53084826400},
]


def test_price_history_uses_free_tickers_historical_daily():
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json=HISTORY)

    client, _ = make_client(handler)
    history = client.get_price_history("sol-solana", 7)
    assert seen[0].path == "/v1/tickers/sol-solana/historical" and seen[0].params["interval"] == "1d"
    assert [p.price_usd for p in history.points] == [89.99, 89.82] and history.interval == "1d"
    assert history.source.source_updated_at.isoformat() == "2026-11-07T00:00:00+00:00"
    client.get_price_history("sol-solana", 90)
    assert seen[1].params["interval"] == "7d"  # acima de 31 dias, por semana


def test_price_history_rejects_period_outside_free_plan_and_maps_402():
    client, _ = make_client(lambda req: httpx.Response(402, json={"error": "not allowed in this plan"}))
    with pytest.raises(CoinPaprikaError) as exc:
        client.get_price_history("sol-solana", 400)
    assert exc.value.kind == "invalid_argument"
    with pytest.raises(CoinPaprikaError) as exc:
        client.get_price_history("sol-solana", 7)
    assert exc.value.kind == "plan_restricted"
