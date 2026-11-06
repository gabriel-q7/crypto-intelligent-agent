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
    "banco_snapshots": _ex(
        "Fase 4: filtro no banco de snapshots (Text-to-Query)",
        {"question": "Quais ativos da nossa base tiveram alta de preço maior que 2% em 24 horas?", "include_trace": True},
        "Usa query_market_database. Rode antes POST /market-data/ingest.",
    ),
    "conceito_reescrita": _ex(
        "Fase 4: conceito com vocabulário diferente do corpus (Query Rewriting)",
        {"question": "Por que a recompensa dos mineradores cai pela metade?", "include_trace": True},
        "A pergunta não usa 'halving': o Retrieval Agent reescreve a consulta e o trecho da seção Halving sobe para o 1º lugar.",
    ),
    "combinada": _ex(
        "Fase 4: mercado + conceito",
        {"question": "Compare Bitcoin e Ethereum pelas métricas de mercado e explique a diferença entre proof of work e proof of stake.", "include_trace": True},
        "Combina get_market_data e search_knowledge_base.",
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

CREATE_CONVERSATION = {
    "com_titulo": _ex("Nova conversa com título", {"title": "Pesquisa BTC x ETH"}),
    "sem_titulo": _ex("Nova conversa sem título", {}),
}

CONVERSATION_MESSAGE = {
    "a1_comparacao": _ex("Cenário A, turno 1: comparação", {"message": "Compare BTC e ETH no mercado atual"}),
    "a2_periodo": _ex(
        "Cenário A, turno 2: continuação",
        {"message": "Considere os últimos 30 dias"},
        "Envie depois do turno 1: mantém BTC e ETH e acrescenta o período.",
    ),
    "b_defi": _ex("Cenário B: nova dimensão (DeFi)", {"message": "E quanto a DeFi?"}),
    "c_anterior": _ex("Cenário C: resultado anterior", {"message": "Agora compare com o resultado anterior"}),
    "d1_btc7": _ex("Cenário D, turno 1", {"message": "Analise BTC nos últimos 7 dias"}),
    "d2_correcao": _ex("Cenário D, turno 2: correção", {"message": "Eu quis dizer 30 dias, não 7"}),
    "preferencia": _ex(
        "Preferência (memória semântica)",
        {"message": "Prefiro sempre análises com foco em risco"},
        "Extraída em segundo plano pelo Mem0 e recuperada nos próximos turnos desta sessão.",
    ),
    "conceito": _ex("Pergunta conceitual", {"message": "O que é TVL?"}),
}


# --------------------------------------------------------------------------- Fase 4

MARKET_INGEST = {
    "padrao": _ex("Lista padrão (10 ativos)", {}, "Usa MARKET_DATA_ASSETS; consome 10 requisições da CoinPaprika."),
    "escolhidos": _ex("Ativos escolhidos", {"assets": ["btc-bitcoin", "ETH", "solana"]}, "Aceita id, símbolo ou nome."),
}

MARKET_QUERY = {
    "alta": _ex("Alta maior que 2% em 24h", {"question": "Quais ativos tiveram alta de preço maior que 2% nas últimas 24 horas?"}),
    "ranking": _ex("Ranking por market cap", {"question": "Quais são os 3 maiores ativos por market cap no banco?"}),
    "historico": _ex(
        "Período sem cobertura",
        {"question": "Qual ativo mais subiu nos últimos 90 dias?"},
        "Os snapshots não cobrem 90 dias: status insufficient_coverage, sem executar.",
    ),
    "nao_suportado": _ex("Dado inexistente no banco", {"question": "Qual o TVL da Uniswap?"}, "status unsupported."),
}

KNOWLEDGE_RETRIEVE = {
    "reescrita": _ex(
        "Falha de vocabulário (diagnóstico real)",
        {"question": "Por que a recompensa dos mineradores cai pela metade?", "mode": "auto"},
        "Sem a palavra 'halving', o trecho certo fica em 3º; com a reescrita, em 1º.",
    ),
    "parcial": _ex(
        "Termo em inglês (melhora parcial)",
        {"question": "O que é impermanent loss?", "mode": "auto"},
        "O corpus diz 'perda impermanente' num trecho sobre DEX/AMM; a reescrita traz trechos de DeFi, mas não esse.",
    ),
    "adequada": _ex("Pergunta já adequada", {"question": "Explique como funciona proof of stake"}, "Sem reescrita."),
    "comparar": _ex(
        "Forçar reescrita (avaliação)",
        {"question": "O que acontece se eu perder minha seed phrase?", "mode": "always"},
    ),
    "ausente": _ex("Assunto fora do corpus", {"question": "Como funciona a queima de moedas?"}, "Sem documentos relevantes."),
}

SYMBOL = {"btc": _ex("Bitcoin", "BTC"), "todos": _ex("Todos os ativos", None)}


# --------------------------------------------------------------------------- Fase 5

AGENT_MESSAGE = {
    "api": _ex("API (preço atual)", {"message": "Qual é o preço atual do Bitcoin?"}),
    "sql": _ex("SQL (banco de snapshots)", {"message": "Quais ativos da nossa base tiveram alta maior que 2% em 24 horas?"}),
    "rag": _ex("RAG (conceito)", {"message": "Explique como funciona proof of stake."}),
    "api_rag": _ex(
        "API + RAG",
        {"message": "Qual o preço atual do Ethereum e qual mecanismo de consenso ele usa?", "include_trace": True},
    ),
    "api_sql": _ex(
        "API + SQL",
        {"message": "Compare o preço atual do BTC com a média dos snapshots armazenados.", "include_trace": True},
    ),
    "follow_up": _ex("Follow-up", {"message": "E o volume dele nas últimas 24 horas?"}, "Envie depois de uma pergunta sobre um ativo."),
}
