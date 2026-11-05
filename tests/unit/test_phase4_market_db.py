"""Fase 4: banco de snapshots, ingestão, validação SQL e Text-to-Query (offline, LLM roteirizado)."""

import json
import sqlite3

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.config import MarketDataSettings
from app.market_data.ingestion import MarketDataIngestor
from app.market_data.sql_guard import QueryExecutionError, UnsafeQueryError, execute_query, validate_sql
from app.market_data.store import ALLOWED_COLUMNS, MarketDataStore
from app.market_data.text_to_query import TextToQueryService
from app.tools.market_db_tool import build_market_db_tool
from tests.unit.phase4_fakes import ASSETS, paprika_client, populated_store, sql_reply

SETTINGS = MarketDataSettings(query_max_rows=50, query_timeout_s=2.0)


def service(store: MarketDataStore, *replies: str) -> TextToQueryService:
    return TextToQueryService(FakeListChatModel(responses=list(replies) or ["{}"]), store, SETTINGS)


# --------------------------------------------------------------------------- repositório e ingestão


def test_ingestion_persists_provider_values_and_timestamps(tmp_path):
    store = MarketDataStore(tmp_path / "m.sqlite3")
    report = MarketDataIngestor(paprika_client(), store).ingest(["btc-bitcoin", "ETH", "solana"])
    assert (report.inserted, report.duplicates, report.failed) == (3, 0, 0)
    rows = {r["symbol"]: r for r in store.snapshots()}
    assert rows["SOL"]["price_usd"] == 180.25 and rows["SOL"]["percent_change_24h"] == 12.4
    assert rows["BTC"]["source_updated_at"] == "2026-10-20T00:50:17Z"
    assert rows["BTC"]["retrieved_at"].endswith("Z") and rows["BTC"]["provider"] == "CoinPaprika"
    # 30d = 0 no plano gratuito vira NULL (não é gravado como dado).
    assert rows["BTC"]["percent_change_30d"] is None
    assert report.coverage["symbols"] == ["BTC", "ETH", "SOL"]


def test_same_source_timestamp_is_not_duplicated_but_new_one_is(tmp_path):
    store = MarketDataStore(tmp_path / "m.sqlite3")
    MarketDataIngestor(paprika_client(), store).ingest(["btc-bitcoin"])
    again = MarketDataIngestor(paprika_client(), store).ingest(["btc-bitcoin"])
    assert again.duplicates == 1 and store.coverage().snapshots == 1
    later = MarketDataIngestor(paprika_client("2026-10-20T01:00:00Z"), store).ingest(["btc-bitcoin"])
    assert later.inserted == 1 and store.coverage().snapshots == 2


def test_api_failure_stores_nothing_for_that_asset(tmp_path):
    store = MarketDataStore(tmp_path / "m.sqlite3")
    report = MarketDataIngestor(paprika_client(fail={"eth-ethereum"}), store).ingest(["btc-bitcoin", "eth-ethereum", "xyz-unknown"])
    kinds = {i.query: (i.status, i.error_kind) for i in report.items}
    assert kinds["eth-ethereum"] == ("failed", "rate_limited")
    assert kinds["xyz-unknown"] == ("failed", "not_found")
    assert store.coverage().symbols == ("BTC",)


def test_store_file_created_only_on_first_use(tmp_path):
    path = tmp_path / "lazy.sqlite3"
    store = MarketDataStore(path)
    assert not path.exists()
    assert store.coverage().snapshots == 0 and path.exists()


# --------------------------------------------------------------------------- validação SQL


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM assets",
        "UPDATE market_snapshots SET price_usd = 0",
        "INSERT INTO assets (symbol) VALUES ('X')",
        "DROP TABLE assets",
        "ALTER TABLE assets ADD COLUMN x",
        "CREATE TABLE x (a)",
        "PRAGMA table_info(assets)",
        "ATTACH DATABASE 'other.db' AS other",
        "SELECT 1 FROM assets; DROP TABLE assets",
        "SELECT * FROM sqlite_master",
        "SELECT secret FROM assets",
        "SELECT load_extension('x') FROM assets",
        "SELECT * FROM main.assets",
        "SELECT * FROM assets LIMIT (SELECT 5)",
        "não é sql",
        "",
    ],
)
def test_unsafe_or_invalid_sql_is_rejected_before_execution(sql):
    with pytest.raises(UnsafeQueryError):
        validate_sql(sql, ALLOWED_COLUMNS, 50)


def test_valid_select_gets_limit_and_bound_parameters():
    query = validate_sql(
        "SELECT symbol, name, percent_change_24h FROM latest_snapshots WHERE percent_change_24h > 10 AND symbol <> 'BTC'",
        ALLOWED_COLUMNS, 50,
    )
    assert query.sql.endswith("LIMIT 50")
    assert "?" in query.sql and "10" not in query.sql and "'BTC'" not in query.sql
    assert query.params == (10, "BTC")
    assert query.filter_columns == {"percent_change_24h", "symbol"}


def test_limit_above_maximum_is_capped_and_ctes_are_allowed():
    query = validate_sql(
        "WITH t AS (SELECT symbol, price_usd AS p FROM latest_snapshots) SELECT symbol, p FROM t ORDER BY 2 DESC LIMIT 500",
        ALLOWED_COLUMNS, 20,
    )
    assert query.limit == 20 and query.sql.endswith("LIMIT 20") and query.tables == {"latest_snapshots"}


def test_execution_is_read_only_and_authorizer_blocks_unlisted_functions(tmp_path):
    store = populated_store(tmp_path)
    conn = store.read_only_connection()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM assets")  # conexão mode=ro
    query = validate_sql("SELECT random() AS r FROM assets", ALLOWED_COLUMNS, 5)
    with pytest.raises(QueryExecutionError) as error:
        execute_query(conn, query, ALLOWED_COLUMNS, 2)
    assert error.value.kind == "invalid_query"
    columns, rows = execute_query(conn, validate_sql("SELECT COUNT(*) AS n FROM market_snapshots", ALLOWED_COLUMNS, 5), ALLOWED_COLUMNS, 2)
    assert columns == ["n"] and rows == [{"n": 3}]


def test_timeout_interrupts_long_query(tmp_path):
    store = populated_store(tmp_path)
    slow = (
        "WITH RECURSIVE c(x) AS (SELECT 1 FROM assets LIMIT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 100000000) "
        "SELECT COUNT(x) AS n FROM c"
    )
    query = validate_sql(slow, ALLOWED_COLUMNS, 5)
    with pytest.raises(QueryExecutionError) as error:
        execute_query(store.read_only_connection(), query, ALLOWED_COLUMNS, 0.05)
    assert error.value.kind == "timeout"


# --------------------------------------------------------------------------- serviço Text-to-Query


def test_question_returns_real_rows(tmp_path):
    sql = "SELECT symbol, name, percent_change_24h FROM latest_snapshots WHERE percent_change_24h > 10 ORDER BY percent_change_24h DESC"
    result = service(populated_store(tmp_path), sql_reply(sql)).run("Quais ativos tiveram alta maior que 10%?")
    assert result.status == "ok"
    assert result.rows == [{"symbol": "SOL", "name": "Solana", "percent_change_24h": 12.4}]
    assert result.params == [10] and result.coverage["snapshots"] == 3


def test_no_match_differs_from_missing_metric(tmp_path):
    store = populated_store(tmp_path)
    none = service(store, sql_reply("SELECT symbol FROM latest_snapshots WHERE percent_change_24h > 50")).run("Quem subiu 50%?")
    assert none.status == "no_matching_records"
    missing = service(store, sql_reply("SELECT symbol FROM latest_snapshots WHERE percent_change_30d > 5", ("percent_change_30d",))).run(
        "Quem subiu mais de 5% em 30d?"
    )
    # 30d é janela do provedor; na fixture ele não informa (0 → NULL): dado ausente, não "nenhum ativo".
    assert missing.status == "missing_data"
    missing = service(store, sql_reply("SELECT symbol FROM latest_snapshots WHERE percent_change_30d > 5", ("percent_change_30d",))).run(
        "Quem tem variação mensal do provedor acima de 5%?"
    )
    assert missing.status == "missing_data" and "percent_change_30d" in missing.message


def test_empty_database_and_insufficient_coverage_skip_the_llm(tmp_path):
    llm = FakeListChatModel(responses=["não deveria ser chamado"])
    empty = TextToQueryService(llm, MarketDataStore(tmp_path / "empty.sqlite3"), SETTINGS).run("Quais ativos subiram?")
    assert empty.status == "missing_data" and "ingest" in empty.message
    store = populated_store(tmp_path)
    result = TextToQueryService(llm, store, SETTINGS).run("Qual ativo mais subiu nos últimos 90 dias?")
    assert result.status == "insufficient_coverage" and result.requested_period_days == 90 and result.sql is None


def test_seven_day_window_uses_provider_metric_without_history(tmp_path):
    sql = "SELECT symbol, percent_change_7d FROM latest_snapshots WHERE percent_change_7d > 5"
    result = service(populated_store(tmp_path), sql_reply(sql, ("percent_change_7d",))).run("Quem subiu mais de 5% nos últimos 7 dias?")
    assert result.status == "ok" and {r["symbol"] for r in result.rows} == {"ETH", "SOL"}


def test_unsafe_generated_sql_is_reported_not_executed(tmp_path):
    result = service(populated_store(tmp_path), sql_reply("DELETE FROM market_snapshots")).run("Apague tudo")
    assert result.status == "invalid_query" and "SELECT" in result.message and result.rows == []


def test_unsupported_and_malformed_generation(tmp_path):
    store = populated_store(tmp_path)
    unsupported = service(store, sql_reply(None, (), "o banco não tem TVL")).run("Qual o TVL da Uniswap?")
    assert unsupported.status == "unsupported" and "TVL" in unsupported.message
    malformed = service(store, "isto não é json").run("Quais ativos subiram?")
    assert malformed.status == "invalid_query"


def test_unknown_column_in_generated_sql_is_invalid(tmp_path):
    result = service(populated_store(tmp_path), sql_reply("SELECT symbol, tvl FROM latest_snapshots")).run("TVL?")
    assert result.status == "invalid_query" and "tvl" in result.message
    assert "/" not in result.message  # sem caminho de arquivo nas mensagens


# --------------------------------------------------------------------------- ferramenta


def test_tool_contract_ok_and_error(tmp_path):
    store = populated_store(tmp_path)
    sql = "SELECT symbol, name, market_cap_usd FROM latest_snapshots ORDER BY market_cap_usd DESC LIMIT 2"
    tool = build_market_db_tool(service(store, sql_reply(sql, ("market_cap_usd",))))
    payload = json.loads(tool.run("query: Top 2 por market cap"))
    assert payload["ok"] and payload["tool"] == "query_market_database"
    assert payload["data"]["row_count"] == 2 and payload["data"]["status"] == "ok"
    assert payload["source"]["provider"].startswith("SQLite local") and payload["source"]["retrieved_at"]
    failed = json.loads(build_market_db_tool(service(store)).run("Qual ativo mais subiu nos últimos 90 dias?"))
    assert not failed["ok"] and failed["error"]["kind"] == "insufficient_coverage" and "instruction" in failed
    assert json.loads(tool.run("  "))["error"]["kind"] == "invalid_argument"
    assert len(ASSETS) == 3


def test_history_view_exposes_symbol_for_temporal_queries(tmp_path):
    store = populated_store(tmp_path)
    MarketDataIngestor(paprika_client("2026-10-20T01:00:00Z"), store).ingest(["btc-bitcoin"])
    sql = "SELECT symbol, AVG(price_usd) AS avg_price_usd, COUNT(*) AS snapshots FROM snapshot_history WHERE symbol = 'BTC' GROUP BY symbol"
    result = service(store, sql_reply(sql, ("price_usd",))).run("Qual o preço médio do BTC nos snapshots?")
    assert result.status == "ok" and result.rows == [{"symbol": "BTC", "avg_price_usd": 81545.05, "snapshots": 2}]
    # Tabela-base sem symbol continua recusada antes da execução.
    bad = service(store, sql_reply("SELECT AVG(price_usd) FROM market_snapshots WHERE symbol = 'BTC'")).run("média BTC")
    assert bad.status == "invalid_query" and "symbol" in bad.message
