"""Dublês da Fase 4: CoinPaprika simulada com vários ativos, banco de snapshots populado e índice falso."""

import copy
import json

import httpx
from langchain_core.documents import Document

from app.config import CoinPaprikaSettings
from app.market_data.store import MarketDataStore
from app.tools.coinpaprika import CoinPaprikaClient
from tests.unit.test_coinpaprika_client import TICKER

# id -> (símbolo, nome, preço, variação 24h, variação 7d)
ASSETS = {
    "btc-bitcoin": ("BTC", "Bitcoin", 81545.05, -1.02, -0.84),
    "eth-ethereum": ("ETH", "Ethereum", 2950.10, 3.5, 6.1),
    "sol-solana": ("SOL", "Solana", 180.25, 12.4, 15.0),
}


def ticker_payload(coin_id: str, last_updated: str = "2026-10-20T00:50:17Z") -> dict:
    symbol, name, price, ch24, ch7 = ASSETS[coin_id]
    data = copy.deepcopy(TICKER)
    data.update(id=coin_id, symbol=symbol, name=name, last_updated=last_updated)
    data["quotes"]["USD"].update(price=price, percent_change_24h=ch24, percent_change_7d=ch7)
    return data


def paprika_client(last_updated: str = "2026-10-20T00:50:17Z", fail: set[str] = frozenset()) -> CoinPaprikaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/v1/search"):
            q = request.url.params.get("q", "").lower()
            hits = [
                {"id": cid, "name": n, "symbol": s, "rank": i + 1, "is_active": True}
                for i, (cid, (s, n, *_)) in enumerate(ASSETS.items())
                if q in (cid, s.lower(), n.lower())
            ]
            return httpx.Response(200, json={"currencies": hits})
        coin_id = path.removeprefix("/v1/tickers/")
        if coin_id in fail:
            return httpx.Response(429, json={"error": "too many"})
        if coin_id in ASSETS:
            return httpx.Response(200, json=ticker_payload(coin_id, last_updated))
        return httpx.Response(404, json={"error": "id not found"})

    return CoinPaprikaClient(
        CoinPaprikaSettings(base_url="https://test.local/v1", max_retries=0),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


def populated_store(tmp_path) -> MarketDataStore:
    from app.market_data.ingestion import MarketDataIngestor

    store = MarketDataStore(tmp_path / "market.sqlite3")
    MarketDataIngestor(paprika_client(), store).ingest(list(ASSETS))
    return store


def sql_reply(sql: str | None, metrics=("percent_change_24h",), unsupported: str | None = None) -> str:
    return json.dumps({"metrics": list(metrics), "filters": ["filtro"], "sql": sql, "unsupported_reason": unsupported})


class FakeIndex:
    """Vector store falso: resultados roteirizados por consulta exata (distância de cosseno)."""

    def __init__(self, results: dict[str, list[tuple[str, str, float]]], contents: dict[str, str] | None = None) -> None:
        self.results = results
        self.contents = contents or {}
        self.queries: list[str] = []

    def similarity_search_with_score(self, query: str, k: int = 3) -> list[tuple[Document, float]]:
        self.queries.append(query)
        rows = self.results.get(query, [])
        return [
            (
                Document(
                    id=chunk_id,
                    page_content=self.contents.get(chunk_id, f"conteúdo de {chunk_id}"),
                    metadata={"source": chunk_id.split("#")[0], "title": title},
                ),
                score,
            )
            for chunk_id, title, score in rows[:k]
        ]
