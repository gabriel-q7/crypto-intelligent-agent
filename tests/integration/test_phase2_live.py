"""Integração real da Fase 2: CoinPaprika (rede) e agente/chains com Ollama.

Execução explícita:
    pytest -m network                      # só API real
    pytest -m "integration or network"     # tudo (precisa de Ollama + modelos + internet)
"""

import httpx
import pytest

from app.config import get_knowledge_settings, get_settings
from app.services.grounding import extract_figure_readings, is_grounded, observed_numbers
from app.tools.coinpaprika import CoinPaprikaClient, CoinPaprikaError


def _ollama_has(model: str) -> bool:
    try:
        tags = httpx.get(f"{get_settings().ollama_base_url}/api/tags", timeout=3).json()
    except httpx.HTTPError:
        return False
    return any(m["name"].split(":latest")[0] == model.split(":latest")[0] for m in tags.get("models", []))


@pytest.fixture(scope="module")
def client():
    try:
        httpx.get("https://api.coinpaprika.com/v1/global", timeout=5)
    except httpx.HTTPError:
        pytest.skip("sem acesso à CoinPaprika")
    return CoinPaprikaClient()


# --------------------------------------------------------------------------- API real


@pytest.mark.network
def test_live_ticker_has_price_and_timestamps(client):
    ticker = client.get_ticker(client.resolve_asset("ETH").id)
    assert ticker.symbol == "ETH" and ticker.price_usd > 0
    assert ticker.source.source_updated_at is not None and ticker.source.retrieved_at is not None


@pytest.mark.network
def test_live_missing_asset_and_paid_endpoint_are_explicit(client):
    with pytest.raises(CoinPaprikaError) as info:
        client.get_ticker("xyz-ativo-que-nao-existe")
    assert info.value.kind == "not_found"
    with pytest.raises(CoinPaprikaError) as info:
        client._get("/coins/btc-bitcoin/ohlcv/historical", {"start": "2020-01-01"})
    assert info.value.kind == "plan_restricted"


@pytest.mark.network
def test_live_global_and_ohlcv(client):
    assert 0 < client.get_global().bitcoin_dominance_percentage < 100
    ohlcv = client.get_ohlcv_today("btc-bitcoin")
    assert ohlcv.low <= ohlcv.close <= ohlcv.high


# --------------------------------------------------------------------------- agente e chains (modelo real)


@pytest.fixture(scope="module")
def llm_ready(client):
    if not _ollama_has(get_settings().ollama_model):
        pytest.skip("Ollama indisponível ou modelo de chat não baixado")


@pytest.mark.integration
@pytest.mark.network
def test_react_agent_calls_coinpaprika_and_uses_real_values(llm_ready):
    from app.agents.factory import KnowledgeSearcher, build_agent
    from app.config import get_agent_settings

    agent = build_agent(CoinPaprikaClient(), KnowledgeSearcher(get_knowledge_settings()), get_agent_settings())
    report = agent.run("Qual o preço atual do Bitcoin e a variação em 24 horas?")
    market = [c for c in report.tool_calls if c.tool == "get_market_data"]
    assert market and market[0].ok, report.tool_calls
    assert report.status.value == "complete", report
    assert report.data_sources[0].endpoint == "/tickers/btc-bitcoin"
    # A resposta final cita valores, e cada um existe na resposta real da CoinPaprika.
    observed = observed_numbers(agent.last_observations)
    figures = extract_figure_readings(report.answer)
    assert figures, f"resposta sem valores: {report.answer}"
    for raw, readings in figures:
        assert any(is_grounded(v, observed) or is_grounded(-v, observed) for v in readings), raw
    print("\nRESPOSTA:", report.answer)


@pytest.mark.integration
@pytest.mark.network
def test_router_comparison_parallel_live(llm_ready):
    from app.chains.routing import build_route_handlers, build_router_chain
    from app.services.llm import build_llm

    llm = build_llm()
    chain = build_router_chain(llm, build_route_handlers(llm, CoinPaprikaClient(), lambda q: []))
    report = chain.invoke({"question": "Compare BTC e ETH nas últimas 24 horas", "assets": []})
    assert report.mode == "route:asset_comparison"
    assert {s.endpoint for s in report.data_sources} >= {"/tickers/btc-bitcoin", "/tickers/eth-ethereum"}
    assert any(f.statement.startswith("Ranking market cap: BTC") for f in report.findings)


@pytest.mark.integration
def test_knowledge_index_with_real_embeddings(tmp_path):
    knowledge = get_knowledge_settings()
    if not _ollama_has(knowledge.embedding_model):
        pytest.skip("modelo de embedding não baixado")
    from app.config import KnowledgeSettings
    from app.knowledge.store import build_index, load_index, search

    settings = KnowledgeSettings(**{**knowledge.__dict__, "store_dir": str(tmp_path / "chroma")})
    build_index(settings)
    hits = search(load_index(settings), "o que acontece no halving do bitcoin", k=1)
    assert hits[0].source == "bitcoin.md"
