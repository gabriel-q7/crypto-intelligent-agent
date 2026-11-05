"""Ferramenta Text-to-Query para o agente ReAct: ``query_market_database``.

Entrada: a pergunta em linguagem natural (texto). Saída: JSON no padrão das ferramentas
(``ok``/``error``). ``ok=true`` para ``ok`` e ``no_matching_records`` (resultado real, mesmo
vazio); ``ok=false`` com ``error.kind`` igual ao status nos demais casos (dados ausentes,
cobertura insuficiente, consulta inválida/não suportada, falha de execução).

A fonte declarada é o banco local de snapshots — distinta da CoinPaprika ao vivo — com o
horário do snapshot mais recente.
"""

from langchain_core.tools import Tool

from app.market_data.text_to_query import TextToQueryResult, TextToQueryService
from app.tools.market_tools import INPUT_PREFIX, error_observation, ok_observation, to_json

TOOL_NAME = "query_market_database"
PROVIDER = "SQLite local (snapshots coletados da CoinPaprika)"


def observation_from(result: TextToQueryResult) -> dict:
    coverage = result.coverage or {}
    source = {
        "provider": PROVIDER,
        "endpoint": "market_db",
        "source_updated_at": coverage.get("last_source_updated_at"),
        "retrieved_at": coverage.get("last_retrieved_at"),
    }
    if result.status in {"ok", "no_matching_records"}:
        data = {
            "status": result.status,
            "message": result.message,
            "rows": result.rows,
            "row_count": result.row_count,
            "sql": result.sql,
            # Valores do filtro (ex.: 5 em "> 5%"): citá-los na resposta passa na verificação de números.
            "params": result.params,
            "data_origin": result.data_origin,
            "coverage": coverage,
        }
        return ok_observation(TOOL_NAME, data, source)
    observation = error_observation(TOOL_NAME, result.status, result.message)
    observation["coverage"] = coverage
    return observation


def build_market_db_tool(service: TextToQueryService) -> Tool:
    def run(raw: str) -> str:
        # Mesma limpeza de prefixos ("query: ...") e aspas das ferramentas de mercado.
        question = INPUT_PREFIX.sub("", (raw or "").strip()).strip().strip("'\"`").strip()
        if len(question) < 3:
            return to_json(error_observation(TOOL_NAME, "invalid_argument", "pergunta vazia ou curta demais"))
        return to_json(observation_from(service.run(question[:500])))

    return Tool(
        name=TOOL_NAME,
        func=run,
        description=(
            "Consulta o BANCO LOCAL de snapshots de mercado (SQLite) com uma pergunta em linguagem natural; "
            "gera e executa SQL somente leitura. Use para filtros e rankings entre vários ativos "
            "(ex.: 'quais ativos subiram mais de 10%?', 'top 3 por market cap') ou para perguntas sobre "
            "'nossa base'/'banco de dados'. Dados são snapshots armazenados, não cotação ao vivo. "
            "Entrada: a pergunta completa."
        ),
    )
