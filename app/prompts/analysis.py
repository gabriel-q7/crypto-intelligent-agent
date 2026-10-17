"""Prompts da análise.

- ``QUESTION_PROMPT`` (PromptTemplate): renderiza a pergunta do usuário em um bloco de texto.
- ``ANALYSIS_PROMPT`` (ChatPromptTemplate): system + exemplos few-shot + pergunta enviada ao modelo.
"""

import json
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

from app.schemas import AnalysisIntent, AnswerStatus, CryptoAnalysisRequest, CryptoQuestion

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
sobre criptomoedas, as transforma em um plano de análise estruturado e dá ao usuário uma resposta \
mínima.

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
mercado necessários; as seguintes calculam e comparam. Lista vazia quando "answer_status" for \
"answered".
7. Use tickers em maiúsculas em "assets" (ex.: BTC, ETH, AAVE), inclusive para ativos citados \
pelo nome (Aave -> AAVE, Solana -> SOL).
8. "intent" deve ser exatamente um destes valores: {", ".join(i.value for i in AnalysisIntent)}. \
"answer_status" deve ser um destes: {", ".join(s.value for s in AnswerStatus)}.
9. "confidence" (0 a 1) mede o quão bem a pergunta está especificada, não uma previsão de mercado:
   - 0.9 a 1.0: ativos, período e critério explícitos, sem premissas relevantes;
   - 0.6 a 0.8: algum critério ou parâmetro precisou ser inferido;
   - abaixo de 0.6: pergunta ambígua ou sem ativos/período identificáveis.
10. Se a pergunta pedir previsão de preço, registre em "assumptions" que o agente não faz \
previsões e planeje apenas a análise de dados históricos.
11. Interprete períodos relativos ("neste ano", "últimos 30 dias") a partir da "Data de referência". \
Nunca altere ou corrija um período informado pelo usuário.
12. Se o período não estiver claro, use null em "period" e registre a ausência em "missing_information".
13. "answer_status":
   - "answered": pergunta conceitual ou educacional (definições, funcionamento de protocolos e \
mecanismos) que pode ser respondida com conhecimento geral estável. NÃO use para avaliar \
desempenho, risco ou tendência de um ativo específico;
   - "requires_market_data": a resposta depende de preços, volumes, TVL, rendimentos ou qualquer \
dado atual ou histórico, inclusive risco ou desempenho de um ativo específico. Exige \
"investigation_steps" e "requested_metrics";
   - "needs_clarification": pergunta ambígua demais para responder ou planejar.
14. "answer": resposta ao usuário em português, de 2 a 4 frases:
   - se "answered", responda diretamente à pergunta;
   - se "requires_market_data", diga explicitamente que nesta fase você não tem dados de mercado \
atualizados e responde só com o conhecimento do modelo; depois explique o que será analisado, \
sem antecipar resultados;
   - se "needs_clarification", diga o que o usuário precisa informar.
   Fatos de protocolo são permitidos (ex.: limite de 21 milhões de BTC), mas NUNCA escreva preços, \
valores em moeda, rendimentos, percentuais ou variações de mercado.
15. Responda SOMENTE com um objeto JSON válido, sem markdown e sem texto fora do JSON."""

QUESTION_PROMPT = PromptTemplate.from_template(
    "Pergunta: {question}\n"
    "Ativos informados: {assets}\n"
    "Período informado: {period}\n"
    "Contexto adicional: {context}\n"
    "Data de referência: {reference_date}"
)

# Exemplos few-shot com ativos diferentes dos casos comuns, para o modelo seguir o padrão sem copiá-lo.
# Mensagens prontas (não templates): as chaves do JSON não precisam de escape.
# 1) Pergunta que depende de dados de mercado.
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
    "answer_status": "requires_market_data",
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
    "answer": (
        "Nesta fase não tenho dados de mercado atualizados e respondo apenas com o conhecimento "
        "do modelo, então não consigo dizer qual dos dois teve melhor desempenho nos últimos 90 "
        "dias. A comparação vai medir retorno, volatilidade e drawdown máximo de SOL e AVAX a "
        "partir do histórico diário de preços em USD."
    ),
    "confidence": 0.8,
}

# 2) Pergunta conceitual: respondida diretamente; fatos de protocolo sim, preços não.
CONCEPT_EXAMPLE_QUESTION = QUESTION_PROMPT.format(
    question="Como funciona o halving do Bitcoin?",
    assets=NOT_INFORMED,
    period=NOT_INFORMED,
    context=NOT_INFORMED,
    reference_date="2026-01-15",
)
CONCEPT_EXAMPLE_ANSWER = {
    "intent": "general_information",
    "answer_status": "answered",
    "assets": ["BTC"],
    "requested_metrics": [],
    "period": None,
    "investigation_steps": [],
    "missing_information": [],
    "assumptions": [],
    "answer": (
        "O halving é um evento programado no protocolo do Bitcoin que reduz pela metade a "
        "recompensa paga aos mineradores por bloco. Ele ocorre a cada 210.000 blocos, "
        "aproximadamente a cada quatro anos, e faz parte do mecanismo que limita a emissão total "
        "a 21 milhões de BTC. O efeito sobre o preço não é garantido e só pode ser avaliado com "
        "dados de mercado."
    ),
    "confidence": 0.9,
}

FEW_SHOT_EXAMPLES = [
    (EXAMPLE_QUESTION, EXAMPLE_ANSWER),
    (CONCEPT_EXAMPLE_QUESTION, CONCEPT_EXAMPLE_ANSWER),
]

ANALYSIS_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_INSTRUCTIONS),
        *(
            message
            for example_question, example_answer in FEW_SHOT_EXAMPLES
            for message in (
                HumanMessage(content=example_question),
                AIMessage(content=json.dumps(example_answer, ensure_ascii=False)),
            )
        ),
        (
            "human",
            "{question_block}\n\n"
            "Produza o plano de análise e a resposta mínima seguindo o formato abaixo.\n"
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
