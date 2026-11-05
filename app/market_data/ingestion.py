"""Coleta de snapshots reais da CoinPaprika para o banco SQLite de mercado.

Reusa o ``CoinPaprikaClient`` existente (timeouts, retries, rate limit, erros tipados, horários
da fonte). Cada ativo é coletado de forma independente: uma falha vira um item ``failed`` com
o tipo do erro e nada é gravado para ele — nunca há valor substituto.
"""

import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.market_data.store import Coverage, MarketDataStore
from app.tools.coinpaprika import COIN_ID_PATTERN, CoinPaprikaClient, CoinPaprikaError

logger = logging.getLogger(__name__)


class IngestedAsset(BaseModel):
    query: str
    coinpaprika_id: str | None = None
    symbol: str | None = None
    status: Literal["inserted", "duplicate", "failed"]
    source_updated_at: datetime | None = None
    retrieved_at: datetime | None = None
    error_kind: str | None = None
    error_message: str | None = None


class IngestionReport(BaseModel):
    started_at: datetime
    finished_at: datetime
    inserted: int
    duplicates: int
    failed: int
    items: list[IngestedAsset]
    coverage: dict = Field(description="Cobertura do banco depois da coleta.")


class MarketDataIngestor:
    def __init__(self, client: CoinPaprikaClient, store: MarketDataStore) -> None:
        self.client = client
        self.store = store

    def _coin_id(self, query: str) -> str:
        lowered = query.strip().lower()
        return lowered if COIN_ID_PATTERN.match(lowered) else self.client.resolve_asset(query).id

    def ingest_one(self, query: str) -> IngestedAsset:
        try:
            ticker = self.client.get_ticker(self._coin_id(query))
        except CoinPaprikaError as exc:
            logger.warning("market ingestion failed query=%s kind=%s", query, exc.kind)
            return IngestedAsset(query=query, status="failed", error_kind=exc.kind, error_message=str(exc))
        inserted = self.store.save_ticker(ticker)
        return IngestedAsset(
            query=query,
            coinpaprika_id=ticker.id,
            symbol=ticker.symbol,
            status="inserted" if inserted else "duplicate",
            source_updated_at=ticker.source.source_updated_at,
            retrieved_at=ticker.source.retrieved_at,
        )

    def ingest(self, queries: list[str] | tuple[str, ...]) -> IngestionReport:
        started = datetime.now(UTC)
        items = [self.ingest_one(q) for q in dict.fromkeys(q.strip() for q in queries if q.strip())]
        coverage: Coverage = self.store.coverage()
        report = IngestionReport(
            started_at=started,
            finished_at=datetime.now(UTC),
            inserted=sum(i.status == "inserted" for i in items),
            duplicates=sum(i.status == "duplicate" for i in items),
            failed=sum(i.status == "failed" for i in items),
            items=items,
            coverage=coverage.as_dict(),
        )
        logger.info(
            "market ingestion inserted=%d duplicates=%d failed=%d", report.inserted, report.duplicates, report.failed
        )
        return report
