"""Chains sequencial, paralela, conhecimento, DeFi e roteamento (moderno e legado), offline."""

import json
import threading
import time

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.language_models.fake import FakeListLLM

from app.chains.routing import build_route_handlers, build_router_chain, classify, keyword_route
from app.knowledge.store import KnowledgeHit, KnowledgeIndexError
from app.legacy.router_chain import build_legacy_router, legacy_classify
from app.schemas.report import ReportStatus
from app.tools.coinpaprika import AssetMatch, CoinPaprikaError, GlobalMarket, MarketTicker, SourceInfo

NOW = "2026-10-20T01:00:00+00:00"


def ticker(symbol: str, price: float) -> MarketTicker:
    return MarketTicker(
        id=f"{symbol.lower()}-coin", name=symbol.title(), symbol=symbol, rank=1, price_usd=price,
        volume_24h_usd=1e9, market_cap_usd=1e11, percent_change_1h=0.1, percent_change_24h=-1.5,
        percent_change_7d=2.0, percent_change_30d=None, ath_price_usd=None, ath_date=None, percent_from_ath=None,
        source=SourceInfo(endpoint=f"/tickers/{symbol.lower()}-coin", source_updated_at=NOW, retrieved_at=NOW),
    )


class FakeClient:
    """Cliente CoinPaprika falso: ativos conhecidos, falhas configuráveis e medição de concorrência."""

    def __init__(self, prices: dict[str, float], failing: set[str] = frozenset(), delay: float = 0.0):
        self.prices, self.failing, self.delay = prices, failing, delay
        self.active, self.max_active, self._lock = 0, 0, threading.Lock()

    def resolve_asset(self, name: str) -> AssetMatch:
        symbol = name.upper()
        if symbol not in self.prices and symbol not in self.failing:
            raise CoinPaprikaError("not_found", f"{name} não encontrado")
        return AssetMatch(id=f"{symbol.lower()}-coin", name=symbol.title(), symbol=symbol, rank=1)

    def get_ticker(self, coin_id: str) -> MarketTicker:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            symbol = coin_id.split("-")[0].upper()
            if symbol in self.failing:
                raise CoinPaprikaError("rate_limited", "limite atingido")
            return ticker(symbol, self.prices[symbol])
        finally:
            with self._lock:
                self.active -= 1

    def get_global(self) -> GlobalMarket:
        return GlobalMarket(market_cap_usd=2.9e12, volume_24h_usd=5e11, bitcoin_dominance_percentage=57.4,
                            cryptocurrencies_number=1, market_cap_ath_usd=None,
                            source=SourceInfo(endpoint="/global", source_updated_at=NOW, retrieved_at=NOW))


def chat(*responses: dict | str) -> FakeListChatModel:
    return FakeListChatModel(responses=[r if isinstance(r, str) else json.dumps(r) for r in responses])


def analysis(summary: str) -> dict:
    return {"summary": summary, "key_points": [], "uncertainties": []}


def handlers(llm, client, search=lambda q: []):
    return build_route_handlers(llm, client, search)


def run(llm, client, question, route, assets=None, search=lambda q: []):
    chain = build_router_chain(llm, handlers(llm, client, search), classifier=lambda q: keyword_route(q).model_copy(update={"route": route}))
    return chain.invoke({"question": question, "assets": assets or []})


# --------------------------------------------------------------------------- sequencial


def test_sequential_market_chain_identifies_fetches_validates_and_analyses():
    llm = chat({"assets": ["btc"]}, analysis("O BTC está em US$ 81.000,00 (CoinPaprika)."))
    report = run(llm, FakeClient({"BTC": 81000.0}), "Preço do BTC?", "market_analysis")
    assert report.mode == "route:market_analysis"
    assert report.status is ReportStatus.COMPLETE
    assert "81.000,00" in report.answer
    assert report.data_sources[0].endpoint == "/tickers/btc-coin"


def test_sequential_chain_without_assets_asks_for_them():
    report = run(chat({"assets": []}), FakeClient({}), "Está caro?", "market_analysis")
    assert report.status is ReportStatus.FAILED and "Informe o nome" in report.answer


def test_sequential_chain_failure_does_not_produce_analysis():
    report = run(chat({"assets": ["btc"]}, analysis("nunca usado")), FakeClient({}, failing={"BTC"}), "BTC?", "market_analysis")
    assert report.status is ReportStatus.FAILED
    assert "nenhum valor foi estimado" in report.answer
    assert any("rate_limited" in u for u in report.uncertainties)


def test_invalid_model_json_is_handled_explicitly():
    report = run(chat({"assets": ["btc"]}, "isto não é json"), FakeClient({"BTC": 1.0}), "BTC?", "market_analysis")
    assert report.status is ReportStatus.PARTIAL
    assert report.findings  # dados reais preservados
    assert "análise do modelo inválida" in report.uncertainties


def test_invented_value_in_analysis_is_removed():
    llm = chat({"assets": ["btc"]}, analysis("O BTC está em US$ 81.000,00. Vai a US$ 200.000."))
    report = run(llm, FakeClient({"BTC": 81000.0}), "BTC?", "market_analysis")
    assert "200.000" not in report.answer


# --------------------------------------------------------------------------- paralela


def test_parallel_comparison_runs_concurrently():
    client = FakeClient({"BTC": 81000.0, "ETH": 2400.0, "SOL": 150.0}, delay=0.2)
    llm = chat(analysis("BTC em US$ 81.000,00 e ETH em US$ 2.400,00."))
    report = run(llm, client, "Compare", "asset_comparison", assets=["btc", "eth", "sol"])
    assert client.max_active >= 2  # buscas sobrepostas
    assert report.status is ReportStatus.COMPLETE
    assert {s.endpoint for s in report.data_sources} >= {"/tickers/btc-coin", "/tickers/eth-coin", "/global"}


def test_parallel_comparison_supports_partial_failure():
    client = FakeClient({"BTC": 81000.0}, failing={"ETH"})
    report = run(chat(analysis("BTC em US$ 81.000,00; ETH indisponível.")), client, "Compare", "asset_comparison", assets=["btc", "eth"])
    assert report.status is ReportStatus.PARTIAL
    assert any("rate_limited" in u for u in report.uncertainties)
    assert any("BTC" in f.statement for f in report.findings)


# --------------------------------------------------------------------------- conhecimento e DeFi


def test_knowledge_route_answers_from_excerpts_and_strips_prices():
    hits = [KnowledgeHit(content="TVL é o valor total depositado.", source="defi-metrics.md", title="DeFi", score=0.5)]
    llm = chat({"answer": "TVL é o valor total depositado em um protocolo. Hoje está em US$ 90 bi.", "used_sources": ["defi-metrics.md"]})
    report = run(llm, FakeClient({}), "O que é TVL?", "knowledge_retrieval", search=lambda q: hits)
    assert report.status is ReportStatus.COMPLETE
    assert "90 bi" not in report.answer
    assert report.data_sources[0].endpoint == "defi-metrics.md"


def test_knowledge_route_reports_missing_index():
    def missing(_):
        raise KnowledgeIndexError("rode ingest")

    report = run(chat(), FakeClient({}), "O que é TVL?", "knowledge_retrieval", search=missing)
    assert report.status is ReportStatus.FAILED and "indisponível" in report.answer


def test_defi_route_reports_unavailable_capability():
    report = run(chat(), FakeClient({}), "TVL do Aave?", "defi_analysis")
    assert report.status is ReportStatus.UNAVAILABLE
    assert "DeFiLlama" in report.answer and report.findings == []


# --------------------------------------------------------------------------- roteamento


def test_modern_classifier_uses_llm_and_falls_back_to_keywords():
    assert classify(chat({"route": "asset_comparison", "reason": "dois ativos"}), "BTC ou ETH?").route == "asset_comparison"
    fallback = classify(chat({"route": "inexistente"}), "O que é staking?")
    assert fallback.route == "knowledge_retrieval" and fallback.method == "keywords"


def test_keyword_routes():
    assert keyword_route("O que é TVL?").route == "knowledge_retrieval"
    assert keyword_route("Como está o TVL do Aave?").route == "defi_analysis"
    assert keyword_route("Compare BTC e ETH").route == "asset_comparison"
    assert keyword_route("Preço do BTC").route == "market_analysis"


def test_router_decision_is_recorded():
    report = run(chat(), FakeClient({}), "TVL do Aave?", "defi_analysis")
    assert report.decision_summary[0].startswith("rota=defi_analysis")


def test_legacy_router_chain_routes_and_falls_back():
    router = build_legacy_router(FakeListLLM(responses=['```json\n{"destination": "asset_comparison", "next_inputs": "BTC vs ETH"}\n```']))
    assert legacy_classify(router, "BTC vs ETH").model_dump() == {"route": "asset_comparison", "reason": "LLMRouterChain", "method": "legacy_router_chain"}
    default = build_legacy_router(FakeListLLM(responses=['{"destination": "DEFAULT", "next_inputs": "x"}']))
    assert legacy_classify(default, "O que é DeFi?").method == "keywords"
    broken = build_legacy_router(FakeListLLM(responses=["sem json"]))
    assert legacy_classify(broken, "Preço do BTC").route == "market_analysis"


def test_comparison_ranking_is_computed_and_given_to_model():
    seen = []

    class Recording(FakeListChatModel):
        def _call(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append(messages[-1].content)
            return super()._call(messages, stop, run_manager, **kwargs)

    client = FakeClient({"BTC": 81000.0, "ETH": 2400.0})
    client.get_ticker_original = client.get_ticker

    def ticker_with_change(coin_id):
        t = client.get_ticker_original(coin_id)
        return t.model_copy(update={"percent_change_24h": {"btc-coin": -0.89, "eth-coin": -1.03}[coin_id]})

    client.get_ticker = ticker_with_change
    llm = Recording(responses=[json.dumps(analysis("BTC caiu 0,89% e ETH caiu 1,03%."))])
    report = run(llm, client, "Compare", "asset_comparison", assets=["btc", "eth"])
    assert "RANKING CALCULADO" in seen[0] and "BTC -0,89% > ETH -1,03%" in seen[0]
    assert any(f.statement.startswith("Ranking variação 24h") for f in report.findings)
    assert any(f.statement.startswith("Ranking market cap") for f in report.findings)


def test_key_points_and_llm_uncertainties_are_verified():
    llm = chat({"summary": "BTC em US$ 81.000,00.", "key_points": ["Preço US$ 81.000,00.", "Alvo US$ 300.000."],
                "uncertainties": ["Preço real seria US$ 79.000."]})
    report = run(llm, FakeClient({"BTC": 81000.0}), "BTC?", "market_analysis", assets=["btc"])
    assert report.key_points == ["Preço US$ 81.000,00."]
    assert not any("79.000" in u for u in report.uncertainties[:-1] if "removidos" not in u)
    assert any("US$ 300.000" in u and "US$ 79.000" in u for u in report.uncertainties)


def test_comparison_conclusion_is_deterministic_not_llm_summary():
    client = FakeClient({"BTC": 81000.0, "ETH": 2400.0})
    original = client.get_ticker

    def ticker_with_change(coin_id):
        return original(coin_id).model_copy(update={"percent_change_24h": {"btc-coin": -1.06, "eth-coin": -1.17}[coin_id]})

    client.get_ticker = ticker_with_change
    # Resumo errado do modelo (inverte a ordem): deve ser descartado.
    llm = chat({"summary": "BTC teve a maior queda.", "key_points": ["ETH caiu 1,17%."], "uncertainties": []})
    report = run(llm, client, "Compare", "asset_comparison", assets=["btc", "eth"])
    assert "BTC teve a maior queda" not in report.answer
    assert "Variação 24h, do maior para o menor: BTC -1,06% > ETH -1,17%." in report.answer
    assert report.key_points == ["ETH caiu 1,17%."]
