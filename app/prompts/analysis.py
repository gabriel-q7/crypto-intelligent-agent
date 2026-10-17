"""Prompts da análise.

- ``QUESTION_PROMPT`` (PromptTemplate): renderiza a pergunta do usuário em um bloco de texto.
- ``ANALYSIS_PROMPT`` (ChatPromptTemplate): system + exemplo few-shot + pergunta enviada ao modelo.
"""

import json
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

from app.schemas import AnalysisIntent, CryptoAnalysisRequest, CryptoQuestion

NOT_INFORMED = "não informado"

# Vocabulário sugerido (não restritivo) para facilitar o mapeamento para APIs nas próximas etapas.
METRIC_VOCABULARY = (
    "price",
    "price_history",
    "return_pct",
    "volatility",
    "max_drawdown",
    "volume",
    "market_cap",
    "tvl",
    "correlation",
)

SYSTEM_INSTRUCTIONS = f"""Você é o Crypto Intelligence Agent, um analista que INTERPRETA perguntas \
sobre criptomoedas e as transforma em um plano de análise estruturado.

Regras obrigatórias:
1. Você NÃO tem acesso a dados de mercado, APIs, internet ou ferramentas.
2. NUNCA invente preços, volumes, market cap, TVL, variações percentuais, datas de eventos \
ou qualquer dado atual ou histórico. Não escreva números de mercado.
3. "requested_metrics": dados de mercado que uma etapa futura buscará em APIs. Use identificadores \
snake_case em inglês, preferindo: {", ".join(METRIC_VOCABULARY)}. Inclua os dados de base \
necessários para calcular as demais métricas (ex.: price_history para return_pct e volatility).
4. "missing_information": somente o que o USUÁRIO deixou de informar e nenhuma API resolve \
(critério de comparação, horizonte, perfil de risco, moeda). NÃO coloque dados de mercado aqui. \
Lista vazia se a pergunta estiver completa.
5. "assumptions": toda premissa adotada para preencher lacunas, inclusive critérios inferidos \
(ex.: "comparação por retorno e volatilidade, pois o critério não foi informado"), moeda de cotação \
e granularidade dos dados.
6. "investigation_steps": etapas ordenadas e executáveis. A primeira etapa obtém os dados de \
mercado necessários; as seguintes calculam e comparam.
7. Use tickers em maiúsculas em "assets" (ex.: BTC, ETH, AAVE), inclusive para ativos citados \
pelo nome (Aave -> AAVE, Solana -> SOL).
8. "intent" deve ser exatamente um destes valores: {", ".join(i.value for i in AnalysisIntent)}.
9. "confidence" (0 a 1) mede o quão bem a pergunta está especificada, não uma previsão de mercado:
   - 0.9 a 1.0: ativos, período e critério explícitos, sem premissas relevantes;
   - 0.6 a 0.8: algum critério ou parâmetro precisou ser inferido;
   - abaixo de 0.6: pergunta ambígua ou sem ativos/período identificáveis.
10. Se a pergunta pedir previsão de preço, registre em "assumptions" que o agente não faz \
previsões e planeje apenas a análise de dados históricos.
11. Interprete períodos relativos ("neste ano", "últimos 30 dias") a partir da "Data de referência". \
Nunca altere ou corrija um período informado pelo usuário.
12. Se o período não estiver claro, use null em "period" e registre a ausência em "missing_information".
13. Responda SOMENTE com um objeto JSON válido, sem markdown e sem texto fora do JSON."""

QUESTION_PROMPT = PromptTemplate.from_template(
    "Pergunta: {question}\n"
    "Ativos informados: {assets}\n"
    "Período informado: {period}\n"
    "Contexto adicional: {context}\n"
    "Data de referência: {reference_date}"
)

# Exemplo few-shot com ativos diferentes dos casos comuns, para o modelo seguir o padrão sem copiá-lo.
# Mensagens prontas (não templates): as chaves do JSON não precisam de escape.
EXAMPLE_QUESTION = QUESTION_PROMPT.format(
    # Ativos só no texto: ensina a extraí-los da pergunta, não do campo "Ativos informados".
    question="Compare SOL e AVAX",
    assets=NOT_INFORMED,
    period="últimos 90 dias",
    context=NOT_INFORMED,
    reference_date="2026-01-15",
)
EXAMPLE_ANSWER = {
    "intent": "comparison",
    "assets": ["SOL", "AVAX"],
    "requested_metrics": ["price_history", "return_pct", "volatility", "max_drawdown"],
    "period": "últimos 90 dias",
    "investigation_steps": [
        "Obter price_history diário de SOL e AVAX em USD nos últimos 90 dias",
        "Calcular return_pct, volatility e max_drawdown de cada ativo",
        "Comparar desempenho e risco entre SOL e AVAX",
    ],
    "missing_information": [],
    "assumptions": [
        "comparação por retorno e risco, pois o critério não foi informado",
        "cotação em USD",
        "preços de fechamento diários",
        "período contado até a data da consulta",
    ],
    "confidence": 0.8,
}

ANALYSIS_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_INSTRUCTIONS),
        HumanMessage(content=EXAMPLE_QUESTION),
        AIMessage(content=json.dumps(EXAMPLE_ANSWER, ensure_ascii=False)),
        (
            "human",
            "{question_block}\n\n"
            "Produza o plano de análise seguindo o formato abaixo.\n"
            "{format_instructions}",
        ),
    ]
)


def get_format_instructions() -> str:
    return JsonOutputParser(pydantic_object=CryptoAnalysisRequest).get_format_instructions()


def build_prompt_input(
    question: CryptoQuestion | dict, reference_date: date | None = None
) -> dict[str, str]:
    """Converte a pergunta nas variáveis esperadas por ``ANALYSIS_PROMPT``."""
    if not isinstance(question, CryptoQuestion):
        question = CryptoQuestion.model_validate(question)

    question_block = QUESTION_PROMPT.format(
        question=question.question,
        assets=", ".join(question.assets) or NOT_INFORMED,
        period=question.period or NOT_INFORMED,
        context=question.context or NOT_INFORMED,
        # Sem isso o modelo assume a data do treino e "corrige" períodos recentes.
        reference_date=(reference_date or date.today()).isoformat(),
    )
    return {"question_block": question_block, "format_instructions": get_format_instructions()}
