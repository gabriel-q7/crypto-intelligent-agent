"""Banco SQLite de snapshots de mercado (Fase 4), separado das conversas da Fase 3.

Modelo (campos reais devolvidos por ``CoinPaprikaClient.get_ticker``):
- ``assets``: um registro por ativo CoinPaprika (id, símbolo, nome, rank mais recente);
- ``market_snapshots``: uma leitura de ``/tickers/{id}`` por linha, com os valores originais
  do provedor (NULL quando o provedor não informa), o horário de atualização da fonte
  (``source_updated_at``) e o horário local da consulta (``retrieved_at``);
- ``latest_snapshots`` (view): o snapshot mais recente de cada ativo, já com símbolo e nome;
- ``snapshot_history`` (view): todos os snapshots, já com símbolo e nome (consultas temporais).

Duplicidade: o mesmo ``source_updated_at`` de um ativo não é gravado duas vezes (índice único
parcial). A CoinPaprika atualiza o ticker a cada poucos minutos; coletas repetidas dentro desse
intervalo não criam linhas novas. Sem ``source_updated_at`` a linha é sempre gravada.

Horários são gravados como texto ISO-8601 em UTC (``2026-11-04T12:00:00Z``): ordenáveis como
texto e aceitos pelas funções de data do SQLite.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.tools.coinpaprika import MarketTicker

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    coinpaprika_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    rank INTEGER
);
CREATE INDEX IF NOT EXISTS idx_assets_symbol ON assets(symbol);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    price_usd REAL NOT NULL CHECK (price_usd >= 0),
    market_cap_usd REAL,
    volume_24h_usd REAL,
    percent_change_1h REAL,
    percent_change_24h REAL,
    percent_change_7d REAL,
    percent_change_30d REAL,
    ath_price_usd REAL,
    percent_from_ath REAL,
    circulating_supply REAL,
    max_supply REAL,
    rank INTEGER,
    provider TEXT NOT NULL DEFAULT 'CoinPaprika',
    source_endpoint TEXT NOT NULL,
    source_updated_at TEXT,
    retrieved_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_source_time
    ON market_snapshots(asset_id, source_updated_at) WHERE source_updated_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_snapshots_asset_time ON market_snapshots(asset_id, retrieved_at);

CREATE VIEW IF NOT EXISTS latest_snapshots AS
SELECT a.coinpaprika_id, a.symbol, a.name, s.*
FROM market_snapshots s
JOIN assets a ON a.id = s.asset_id
WHERE s.id = (SELECT s2.id FROM market_snapshots s2 WHERE s2.asset_id = s.asset_id
              ORDER BY s2.retrieved_at DESC, s2.id DESC LIMIT 1);

-- v2: todos os snapshots já com símbolo e nome (o modelo pequeno errava o JOIN com assets).
CREATE VIEW IF NOT EXISTS snapshot_history AS
SELECT a.coinpaprika_id, a.symbol, a.name, s.*
FROM market_snapshots s
JOIN assets a ON a.id = s.asset_id;
"""

# Colunas que o Text-to-Query pode ler (contrato explícito; o resto do banco é inacessível).
SNAPSHOT_COLUMNS = (
    "id", "asset_id", "price_usd", "market_cap_usd", "volume_24h_usd", "percent_change_1h",
    "percent_change_24h", "percent_change_7d", "percent_change_30d", "ath_price_usd",
    "percent_from_ath", "circulating_supply", "max_supply", "rank", "provider", "source_endpoint",
    "source_updated_at", "retrieved_at",
)
ALLOWED_COLUMNS: dict[str, frozenset[str]] = {
    "assets": frozenset({"id", "coinpaprika_id", "symbol", "name", "rank"}),
    "market_snapshots": frozenset(SNAPSHOT_COLUMNS),
    "latest_snapshots": frozenset({"coinpaprika_id", "symbol", "name", *SNAPSHOT_COLUMNS}),
    "snapshot_history": frozenset({"coinpaprika_id", "symbol", "name", *SNAPSHOT_COLUMNS}),
}
VIEWS = frozenset({"latest_snapshots", "snapshot_history"})
METRIC_COLUMNS = frozenset(SNAPSHOT_COLUMNS) - {
    "id", "asset_id", "provider", "source_endpoint", "source_updated_at", "retrieved_at",
}


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


@dataclass(frozen=True)
class Coverage:
    """O que o banco realmente contém (base para declarar falta de dados ou de histórico)."""

    assets: int
    snapshots: int
    first_retrieved_at: datetime | None
    last_retrieved_at: datetime | None
    last_source_updated_at: datetime | None
    symbols: tuple[str, ...]

    @property
    def span_days(self) -> float:
        if not self.first_retrieved_at or not self.last_retrieved_at:
            return 0.0
        return (self.last_retrieved_at - self.first_retrieved_at).total_seconds() / 86400

    def as_dict(self) -> dict:
        return {
            "assets": self.assets,
            "snapshots": self.snapshots,
            "first_retrieved_at": iso_utc(self.first_retrieved_at),
            "last_retrieved_at": iso_utc(self.last_retrieved_at),
            "last_source_updated_at": iso_utc(self.last_source_updated_at),
            "span_days": round(self.span_days, 3),
            "symbols": list(self.symbols),
        }


class MarketDataStore:
    """Repositório dos snapshots. O schema é criado na primeira operação (arquivo sob demanda)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._ready = False
        self._lock = threading.Lock()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._ensure_schema()
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            if self._ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10)
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.executescript(SCHEMA)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.commit()
            finally:
                conn.close()
            self._ready = True

    def ensure_ready(self) -> None:
        self._ensure_schema()

    # ------------------------------------------------------------------ gravação

    def save_ticker(self, ticker: MarketTicker) -> bool:
        """Grava o ativo (upsert) e o snapshot. Retorna False se o snapshot já existia."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO assets (coinpaprika_id, symbol, name, rank) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(coinpaprika_id) DO UPDATE SET symbol=excluded.symbol, name=excluded.name, "
                "rank=excluded.rank",
                (ticker.id, ticker.symbol.upper(), ticker.name, ticker.rank),
            )
            asset_id = conn.execute("SELECT id FROM assets WHERE coinpaprika_id = ?", (ticker.id,)).fetchone()[0]
            cursor = conn.execute(
                "INSERT OR IGNORE INTO market_snapshots (asset_id, price_usd, market_cap_usd, volume_24h_usd, "
                "percent_change_1h, percent_change_24h, percent_change_7d, percent_change_30d, ath_price_usd, "
                "percent_from_ath, circulating_supply, max_supply, rank, provider, source_endpoint, "
                "source_updated_at, retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    asset_id, ticker.price_usd, ticker.market_cap_usd, ticker.volume_24h_usd,
                    ticker.percent_change_1h, ticker.percent_change_24h, ticker.percent_change_7d,
                    ticker.percent_change_30d, ticker.ath_price_usd, ticker.percent_from_ath,
                    ticker.circulating_supply, ticker.max_supply, ticker.rank, ticker.source.provider,
                    ticker.source.endpoint, iso_utc(ticker.source.source_updated_at),
                    iso_utc(ticker.source.retrieved_at),
                ),
            )
            conn.commit()
            return cursor.rowcount == 1

    # ------------------------------------------------------------------ leitura

    def coverage(self) -> Coverage:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT asset_id), COUNT(*), MIN(retrieved_at), MAX(retrieved_at), "
                "MAX(source_updated_at) FROM market_snapshots"
            ).fetchone()
            symbols = [r[0] for r in conn.execute(
                "SELECT DISTINCT a.symbol FROM assets a JOIN market_snapshots s ON s.asset_id = a.id ORDER BY a.symbol"
            )]
        return Coverage(row[0], row[1], parse_iso(row[2]), parse_iso(row[3]), parse_iso(row[4]), tuple(symbols))

    def snapshots(self, symbol: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM market_snapshots s JOIN assets a ON a.id = s.asset_id"
        params: tuple = ()
        if symbol:
            sql += " WHERE a.symbol = ?"
            params = (symbol.upper(),)
        sql += " ORDER BY s.retrieved_at DESC, s.id DESC LIMIT ?"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (*params, limit))]

    def count_non_null(self, column: str) -> int:
        """Quantos ativos têm ``column`` preenchida no snapshot mais recente."""
        if column not in METRIC_COLUMNS:
            raise ValueError(f"coluna não permitida: {column}")
        with self._connect() as conn:
            return conn.execute(f"SELECT COUNT({column}) FROM latest_snapshots").fetchone()[0]

    def read_only_connection(self) -> sqlite3.Connection:
        """Conexão somente leitura (``mode=ro``): o SQLite recusa qualquer escrita nela."""
        self._ensure_schema()
        conn = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
