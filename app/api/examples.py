"""Exemplos exibidos no Swagger UI (/docs): requisições reais e válidas, por cenário.

O primeiro exemplo de cada dicionário é o pré-preenchido no "Try it out".
"""

from fastapi.openapi.models import Example


def _ex(summary: str, value: object, description: str | None = None) -> Example:
    example: Example = {"summary": summary, "value": value}
    if description:
        example["description"] = description
    return example


ANALYZE = {
    "comparacao": _ex(
        "Comparação com ativos e período",
        {"question": "Compare BTC e ETH", "assets": ["BTC", "ETH"], "period": "últimos 30 dias", "composition": "lcel"},
        "Depende de dados de mercado: devolve o plano de análise e avisa que não há dados atualizados.",
    ),
    "conceitual": _ex(
        "Pergunta conceitual (composição @chain)",
        {"question": "O que é staking de ETH?", "composition": "imperative"},
        "Respondida com o conhecimento do modelo (answer_status=answered).",
    ),
    "previsao": _ex(
        "Pedido de previsão e preço atual",
        {"question": "BTC vai subir amanhã? Qual o preço atual?"},
        "O modelo não faz previsões nem informa preços: registra a limitação.",
    ),
    "com_contexto": _ex(
        "Risco com contexto do usuário",
        {
            "question": "Qual o risco de manter SOL em carteira?",
            "assets": ["SOL"],
            "period": "próximos 6 meses",
            "context": "Perfil conservador, investimento de longo prazo",
        },
    ),
}

ANALYZE_BATCH = {
    "tres_perguntas": _ex(
        "Três perguntas de tipos diferentes",
        {
            "questions": [
                {"question": "Qual o risco de manter SOL em carteira?", "assets": ["SOL"]},
                {"question": "Como evoluiu o TVL do Aave neste ano?", "period": "2026"},
                {"question": "O que é staking de ETH?"},
            ]
        },
        "Falhas são isoladas por item (ok=false no item que falhar).",
    ),
}

ANALYZE_STREAM = {
    "comparacao": _ex(
        "Comparação (stream NDJSON)",
        {"question": "Compare BTC e ETH", "assets": ["BTC", "ETH"], "period": "últimos 30 dias"},
        "Eventos 'partial' (não validados) e, no fim, 'final' validado pelo schema.",
    ),
    "conceitual": _ex("Pergunta conceitual", {"question": "O que é uma exchange descentralizada?"}),
}

AGENT = {
    "preco_atual": _ex(
        "Preço atual (chama a CoinPaprika)",
        {"question": "Qual o preço atual do Bitcoin e a variação em 24 horas?", "session_id": "demo", "include_trace": True},
        "O agente usa get_market_data; a resposta cita valor, fonte e horário reais.",
    ),
    "continuacao": _ex(
        "Continuação na mesma sessão (memória)",
        {"question": "E o Ethereum?", "session_id": "demo"},
        "Envie depois de 'preco_atual': o histórico da sessão dá o contexto, e o agente consulta os dados de novo.",
    ),
    "comparacao": _ex(
        "Comparação de dois ativos",
        {"question": "Compare ETH e SOL nas últimas 24 horas", "include_trace": True},
    ),
    "detalhes": _ex(
        "Métricas adicionais (OHLCV do dia e perfil)",
        {"question": "Qual a máxima e a mínima do Ethereum hoje e qual o seu mecanismo de consenso?"},
    ),
    "mercado_global": _ex("Visão do mercado", {"question": "Qual é a dominância atual do Bitcoin no mercado cripto?"}),
    "conceito": _ex(
        "Conceito (base de conhecimento)",
        {"question": "O que é TVL e quais são suas limitações?"},
        "Usa search_knowledge_base; não traz preços.",
    ),
    "informacao_ausente": _ex(
        "Informação ausente",
        {"question": "Esse ativo está caro?"},
        "Sem ativo identificado: o agente pede a informação em vez de chutar.",
    ),
}

ROUTE = {
    "comparacao_paralela": _ex(
        "Comparação (chain paralela)",
        {"question": "Compare BTC, ETH e SOL nas últimas 24 horas", "include_trace": True},
        "Busca os ativos e o mercado global em paralelo; conclusão comparativa calculada no código.",
    ),
    "mercado_sequencial": _ex(
        "Análise de mercado (chain sequencial)",
        {"question": "Como está o preço do Ethereum hoje?"},
        "identificar ativos → buscar dados → validar → gerar análise.",
    ),
    "ativos_explicitos": _ex(
        "Ativos explícitos (pula a extração)",
        {"question": "Compare o desempenho em 24 horas", "assets": ["btc", "sol"]},
    ),
    "conhecimento": _ex(
        "Conhecimento (Chroma)",
        {"question": "O que é staking de ETH e quais são seus riscos?"},
    ),
    "defi_indisponivel": _ex(
        "DeFi (indisponível nesta fase)",
        {"question": "Como evoluiu o TVL do Aave neste mês?"},
        "Retorna status=unavailable: o DeFiLlama ainda não foi integrado.",
    ),
    "router_legado": _ex(
        "RouterChain legado",
        {"question": "Compare BTC e ETH", "router": "legacy"},
        "Classificação com LLMRouterChain (langchain-classic); mesmo despacho do roteador moderno.",
    ),
}

KNOWLEDGE_QUERY = {
    "tvl": _ex("TVL", "o que é TVL"),
    "halving": _ex("Halving", "halving do bitcoin"),
    "staking": _ex("Riscos do staking", "riscos do staking de ETH"),
    "stablecoins": _ex("Stablecoins", "tipos de stablecoins"),
}

COIN_ID = {
    "bitcoin": _ex("Bitcoin", "btc-bitcoin"),
    "ethereum": _ex("Ethereum", "eth-ethereum"),
    "solana": _ex("Solana", "sol-solana"),
}

SESSION_ID = {"demo": _ex("Sessão dos exemplos do /agent", "demo")}
