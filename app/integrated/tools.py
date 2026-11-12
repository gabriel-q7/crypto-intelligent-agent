"""Ferramentas do agente integrado (Fase 5) com argumentos estruturados para tool calling nativo.

Cada ferramenta só **adapta a assinatura**: recebe argumentos tipados (o modelo preenche um JSON)
e delega para a ferramenta de entrada única já existente (Fases 2 e 4). Não há regra de negócio
nova aqui. Toda chamada devolve a mesma observação JSON (``ok``/``error``/``source``).

| Categoria | Ferramentas |
|---|---|
| API (CoinPaprika ao vivo) | search_crypto_asset, get_market_data, get_market_details, get_global_market |
| SQL (snapshots SQLite) | query_market_database |
| RAG (Chroma + Query Rewriting) | search_knowledge_base |
| Determinística | calculate_percent_change |
"""

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

TOOL_CATEGORY = {
    "search_crypto_asset": "api",
    "get_market_data": "api",
    "get_market_details": "api",
    "get_global_market": "api",
    "query_market_database": "sql",
    "search_knowledge_base": "rag",
    "calculate_percent_change": "deterministic",
}


class AssetArgs(BaseModel):
    asset: str = Field(description="Símbolo, nome ou id CoinPaprika de UM ativo (ex.: 'BTC', 'ethereum', 'sol-solana').")


class SearchArgs(BaseModel):
    query: str = Field(description="Nome ou símbolo a identificar (ex.: 'solana').")


class GlobalArgs(BaseModel):
    pass


class QuestionArgs(BaseModel):
    question: str = Field(description="A pergunta completa do usuário, sem resumir.")


class PercentArgs(BaseModel):
    old_value: float = Field(description="Valor inicial (já obtido de outra ferramenta).")
    new_value: float = Field(description="Valor final (já obtido de outra ferramenta).")


DESCRIPTIONS = {
    "search_crypto_asset": "Identifica criptoativos na CoinPaprika (id, nome, símbolo, rank).",
    "get_market_data": (
        "Dados de mercado ATUAIS (ao vivo, CoinPaprika) de UM ativo: preço em USD, volume 24h, market cap, "
        "variações 1h/24h/7d/30d e ATH, com horário de atualização. Chame uma vez por ativo."
    ),
    "get_market_details": "OHLCV do dia e perfil (tipo, consenso, tags) de UM ativo, ao vivo (CoinPaprika).",
    "get_global_market": "Market cap total, volume 24h e dominância do BTC, ao vivo (CoinPaprika).",
    "query_market_database": (
        "Consulta o BANCO LOCAL de snapshots de mercado já coletados (SQLite) com uma pergunta em linguagem "
        "natural; gera SQL somente leitura. Use para filtros e rankings entre vários ativos, médias e "
        "comparações com coletas anteriores ('nossa base', 'snapshots', 'histórico armazenado'). "
        "São dados armazenados, NÃO cotação ao vivo."
    ),
    "search_knowledge_base": (
        "Busca CONCEITOS em documentos locais (blockchain, PoW/PoS, Bitcoin, Ethereum, Solana, contratos "
        "inteligentes, finalidade, comparação técnica entre blockchains, staking, DeFi, TVL, stablecoins, métricas, "
        "riscos), com reescrita de consulta. Sem preços. Use para toda explicação conceitual e para comparar "
        "redes (X vs Y), inclusive quando a pergunta também pede dados de mercado."
    ),
    "calculate_percent_change": "Calcula a variação percentual entre dois valores já obtidos de outras ferramentas.",
}


def structured_tools(base_tools: list[BaseTool]) -> list[StructuredTool]:
    """Adapta as ferramentas de entrada única (``build_agent_tools``) para tool calling."""
    by_name = {t.name: t for t in base_tools}

    def delegate(name: str, text: str) -> str:
        return by_name[name].run(text)

    specs = {
        "search_crypto_asset": (SearchArgs, lambda query: delegate("search_crypto_asset", query)),
        "get_market_data": (AssetArgs, lambda asset: delegate("get_market_data", asset)),
        "get_market_details": (AssetArgs, lambda asset: delegate("get_market_details", asset)),
        "get_global_market": (GlobalArgs, lambda: delegate("get_global_market", "global")),
        "query_market_database": (QuestionArgs, lambda question: delegate("query_market_database", question)),
        "search_knowledge_base": (QuestionArgs, lambda question: delegate("search_knowledge_base", question)),
        "calculate_percent_change": (
            PercentArgs,
            lambda old_value, new_value: delegate("calculate_percent_change", f"{old_value}, {new_value}"),
        ),
    }
    tools = []
    for name, (schema, func) in specs.items():
        if name not in by_name:
            continue
        tools.append(StructuredTool.from_function(func=func, name=name, description=DESCRIPTIONS[name], args_schema=schema))
    return tools
