"""Text-to-Query: pergunta em linguagem natural → SQL validado → resultado do banco de mercado.

Fluxo:
1. cobertura do banco (sem snapshots → ``missing_data``, sem chamar o LLM);
2. período pedido detectado por regra (``find_period`` da Fase 3); se exigir histórico que o
   banco não cobre → ``insufficient_coverage``, sem executar;
3. LLM (modo JSON) interpreta métricas, filtros e gera o SQL (``SqlGeneration``);
4. ``validate_sql`` (parser) → ``execute_query`` (conexão somente leitura + authorizer + timeout);
5. sem linhas: distingue ``missing_data`` (métrica filtrada está vazia no banco) de
   ``no_matching_records`` (há dados, nenhum atende ao filtro).

O resultado traz o SQL executado, os parâmetros e a cobertura: o agente responde com base nas
linhas reais e informa que os dados são snapshots armazenados (não cotação ao vivo).
"""

import logging
from typing import Literal

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError

from app.chains.market_chains import ask_json
from app.config import MarketDataSettings
from app.intent.classifier import find_period
from app.market_data.sql_guard import (
    QueryExecutionError,
    UnsafeQueryError,
    execute_query,
    validate_sql,
)
from app.market_data.store import ALLOWED_COLUMNS, METRIC_COLUMNS, Coverage, MarketDataStore

logger = logging.getLogger(__name__)

QueryStatus = Literal[
    "ok",
    "no_matching_records",
    "missing_data",
    "insufficient_coverage",
    "invalid_query",
    "unsupported",
    "execution_error",
]
# Janelas que o próprio provedor calcula e o snapshot já guarda (percent_change_24h/_7d/_30d):
# não dependem de várias observações no banco. Se o provedor não informar (NULL), o resultado é
# missing_data — nunca uma série inventada.
PROVIDER_WINDOWS_DAYS = {1, 7, 30}


class SqlGeneration(BaseModel):
    """Saída do LLM: interpretação da pergunta + SQL proposto (ainda não confiável)."""

    metrics: list[str] = Field(default=[], description="Colunas/métricas necessárias.")
    filters: list[str] = Field(default=[], description="Condições em linguagem natural.")
    sql: str | None = Field(default=None, description="Um único SELECT; null se não for possível.")
    unsupported_reason: str | None = Field(
        default=None, description="Preenchido quando o banco não tem o dado pedido."
    )


class TextToQueryResult(BaseModel):
    status: QueryStatus
    question: str
    message: str
    interpretation: SqlGeneration | None = None
    sql: str | None = Field(default=None, description="SQL validado e executado (literais como '?').")
    params: list = []
    columns: list[str] = []
    rows: list[dict] = []
    row_count: int = 0
    requested_period_days: int | None = None
    coverage: dict = {}
    data_origin: str = Field(
        default="snapshots CoinPaprika armazenados no SQLite local (não é cotação ao vivo)",
    )


SCHEMA_DESCRIPTION = """Banco SQLite (somente leitura) com snapshots de mercado coletados da CoinPaprika.

VIEW latest_snapshots  -- snapshot MAIS RECENTE de cada ativo (use para perguntas sobre "agora"/"atual")
  coinpaprika_id TEXT, symbol TEXT (ex.: 'BTC'), name TEXT (ex.: 'Bitcoin'), asset_id INTEGER,
  price_usd REAL, market_cap_usd REAL, volume_24h_usd REAL,
  percent_change_1h REAL, percent_change_24h REAL, percent_change_7d REAL, percent_change_30d REAL,
  ath_price_usd REAL, percent_from_ath REAL, circulating_supply REAL, max_supply REAL, rank INTEGER,
  provider TEXT, source_endpoint TEXT, source_updated_at TEXT, retrieved_at TEXT

VIEW snapshot_history  -- TODAS as leituras coletadas (histórico), com as MESMAS colunas de latest_snapshots
  (use para médias, evolução, primeiro × último snapshot de um ativo)

Também existem as tabelas-base market_snapshots (sem symbol/name; ligar por asset_id) e
assets (id, coinpaprika_id, symbol, name, rank); prefira as views.

Observações:
- percent_change_* são percentuais (10 = 10%), calculados pelo provedor no momento do snapshot.
- percent_change_30d pode ser NULL quando o provedor não informa.
- Horários são texto ISO-8601 UTC (ex.: '2026-11-04T12:00:00Z'); use datetime('now', '-7 days') para janelas.
- Valores podem ser NULL: não trate NULL como zero."""

SYSTEM_PROMPT = f"""Você converte perguntas sobre o banco de mercado em UMA consulta SQLite somente leitura.

{SCHEMA_DESCRIPTION}

Regras:
1. Apenas um SELECT. Nunca INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, PRAGMA ou ATTACH.
2. Use somente as tabelas e colunas acima. Sempre inclua symbol e name quando listar ativos.
   Só filtre por symbol/name quando a pergunta citar o ativo; "quais ativos..." considera todos.
3. "Subiu/aumento de X%" em 24h → percent_change_24h > X; em 7 dias → percent_change_7d > X; \
em 30 dias → percent_change_30d > X; sem período explícito, use percent_change_24h e diga isso em filters.
4. Perguntas sobre o momento atual e variações do provedor (24h, 7d) usam latest_snapshots; \
médias, evolução ou comparação entre coletas usam snapshot_history.
5. Inclua ORDER BY coerente e LIMIT de no máximo 50.
6. Se o banco não tiver o dado (ex.: TVL, notícias, preço em BRL, previsões), sql=null e explique em unsupported_reason.

Responda SOMENTE com JSON:
{{"metrics": ["coluna"], "filters": ["condição"], "sql": "SELECT ...", "unsupported_reason": null}}

Exemplos:
Pergunta: Quais ativos tiveram alta de preço maior que 10%?
{{"metrics": ["percent_change_24h"], "filters": ["variação 24h > 10% (sem período explícito: 24h)"], \
"sql": "SELECT symbol, name, percent_change_24h FROM latest_snapshots WHERE percent_change_24h > 10 ORDER BY percent_change_24h DESC LIMIT 50", "unsupported_reason": null}}
Pergunta: Quais os 3 maiores ativos por market cap?
{{"metrics": ["market_cap_usd"], "filters": ["top 3 por market cap"], \
"sql": "SELECT symbol, name, market_cap_usd FROM latest_snapshots ORDER BY market_cap_usd DESC LIMIT 3", "unsupported_reason": null}}
Pergunta: Qual o preço médio do ETH nos snapshots armazenados?
{{"metrics": ["price_usd"], "filters": ["ativo ETH, todos os snapshots"], \
"sql": "SELECT symbol, name, AVG(price_usd) AS avg_price_usd, COUNT(*) AS snapshots FROM snapshot_history WHERE symbol = 'ETH' GROUP BY symbol, name LIMIT 1", "unsupported_reason": null}}
Pergunta: Qual o TVL da Uniswap?
{{"metrics": [], "filters": [], "sql": null, "unsupported_reason": "o banco não tem TVL nem dados de protocolos DeFi"}}"""


class TextToQueryService:
    def __init__(self, llm: BaseChatModel, store: MarketDataStore, settings: MarketDataSettings) -> None:
        self.llm = llm
        self.store = store
        self.settings = settings

    def _generate(self, question: str, coverage: Coverage) -> SqlGeneration:
        # Só a dimensão do banco: com a lista de símbolos no prompt, o modelo a copiava como filtro
        # "symbol IN (...)" não pedido e chegou a omitir um ativo (docs/phase-4-evidence.md, 2.2).
        human = (
            f"O banco tem {coverage.assets} ativos e snapshots de {coverage.as_dict()['first_retrieved_at']} "
            f"a {coverage.as_dict()['last_retrieved_at']}.\nPergunta: {question}"
        )
        return ask_json(self.llm, SYSTEM_PROMPT, human, SqlGeneration)

    def run(self, question: str) -> TextToQueryResult:
        question = question.strip()
        coverage = self.store.coverage()
        base = {"question": question, "coverage": coverage.as_dict()}
        if coverage.snapshots == 0:
            return TextToQueryResult(
                status="missing_data",
                message="O banco de mercado ainda não tem snapshots. Colete dados com POST /market-data/ingest.",
                **base,
            )

        _, period_days = find_period(question)
        base["requested_period_days"] = period_days
        if period_days and period_days not in PROVIDER_WINDOWS_DAYS and coverage.span_days < period_days:
            return TextToQueryResult(
                status="insufficient_coverage",
                message=(
                    f"A pergunta pede {period_days} dias, mas os snapshots armazenados cobrem só "
                    f"{coverage.span_days:.2f} dia(s) ({base['coverage']['first_retrieved_at']} a "
                    f"{base['coverage']['last_retrieved_at']}). Não há observações para o período; "
                    "nenhum valor foi estimado."
                ),
                **base,
            )

        try:
            generation = self._generate(question, coverage)
        except (OutputParserException, ValidationError, ValueError) as exc:
            logger.warning("text-to-query generation invalid: %s", exc)
            return TextToQueryResult(
                status="invalid_query", message="O modelo não produziu uma consulta no formato esperado.", **base
            )
        if generation.unsupported_reason or not generation.sql:
            return TextToQueryResult(
                status="unsupported",
                message=f"O banco de mercado não responde a esta pergunta: {generation.unsupported_reason or 'sem consulta aplicável'}.",
                interpretation=generation,
                **base,
            )

        try:
            query = validate_sql(generation.sql, ALLOWED_COLUMNS, self.settings.query_max_rows)
        except UnsafeQueryError as exc:
            logger.warning("text-to-query rejected sql=%r reason=%s", generation.sql, exc)
            return TextToQueryResult(
                status="invalid_query", message=f"Consulta recusada pela validação: {exc}.",
                interpretation=generation, **base,
            )

        conn = self.store.read_only_connection()
        try:
            columns, rows = execute_query(conn, query, ALLOWED_COLUMNS, self.settings.query_timeout_s)
        except QueryExecutionError as exc:
            logger.warning("text-to-query execution failed kind=%s sql=%r", exc.kind, query.sql)
            status: QueryStatus = "invalid_query" if exc.kind == "invalid_query" else "execution_error"
            return TextToQueryResult(
                status=status, message=f"Falha na execução: {exc}.", interpretation=generation,
                sql=query.sql, params=list(query.params), **base,
            )
        finally:
            conn.close()

        result = {
            "interpretation": generation, "sql": query.sql, "params": list(query.params),
            "columns": columns, "rows": rows, "row_count": len(rows), **base,
        }
        if rows:
            return TextToQueryResult(status="ok", message=f"{len(rows)} linha(s) do banco de snapshots.", **result)

        empty_metrics = [c for c in sorted(query.filter_columns & METRIC_COLUMNS) if self.store.count_non_null(c) == 0]
        if empty_metrics:
            return TextToQueryResult(
                status="missing_data",
                message=(
                    f"Nenhum ativo tem {', '.join(empty_metrics)} preenchido no snapshot mais recente "
                    "(o provedor não informa esse dado). Não é possível afirmar que nenhum ativo atende ao filtro."
                ),
                **result,
            )
        return TextToQueryResult(
            status="no_matching_records",
            message="A consulta foi executada e nenhum ativo do banco atende aos critérios.",
            **result,
        )
