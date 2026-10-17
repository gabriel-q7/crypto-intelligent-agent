"""Prompts da análise.

- ``QUESTION_PROMPT`` (PromptTemplate): renderiza a pergunta do usuário em um bloco de texto.
- ``ANALYSIS_PROMPT`` (ChatPromptTemplate): mensagens system + human enviadas ao modelo.
"""

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

from app.schemas import AnalysisIntent, CryptoAnalysisRequest, CryptoQuestion

NOT_INFORMED = "não informado"

SYSTEM_INSTRUCTIONS = f"""Você é o Crypto Intelligence Agent, um analista que INTERPRETA perguntas \
sobre criptomoedas e as transforma em um plano de análise estruturado.

Regras obrigatórias:
1. Você NÃO tem acesso a dados de mercado, APIs, internet ou ferramentas.
2. NUNCA invente preços, volumes, market cap, TVL, variações percentuais, datas de eventos \
ou qualquer dado atual ou histórico. Não escreva números de mercado.
3. Quando um dado for necessário, liste-o em "requested_metrics" e, se faltar para responder, \
em "missing_information".
4. Registre em "assumptions" toda premissa adotada (ex.: moeda de cotação USD).
5. Use tickers em maiúsculas em "assets" (ex.: BTC, ETH). Inclua ativos citados na pergunta.
6. "intent" deve ser exatamente um destes valores: {", ".join(i.value for i in AnalysisIntent)}.
7. "confidence" é um número entre 0 e 1 que mede o quão clara está a pergunta, não uma previsão de mercado.
8. Se o período não estiver claro, use null em "period" e registre a ausência em "missing_information".
9. Responda SOMENTE com um objeto JSON válido, sem markdown e sem texto fora do JSON."""

QUESTION_PROMPT = PromptTemplate.from_template(
    "Pergunta: {question}\n"
    "Ativos informados: {assets}\n"
    "Período informado: {period}\n"
    "Contexto adicional: {context}"
)

ANALYSIS_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_INSTRUCTIONS),
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


def build_prompt_input(question: CryptoQuestion | dict) -> dict[str, str]:
    """Converte a pergunta nas variáveis esperadas por ``ANALYSIS_PROMPT``."""
    if not isinstance(question, CryptoQuestion):
        question = CryptoQuestion.model_validate(question)

    question_block = QUESTION_PROMPT.format(
        question=question.question,
        assets=", ".join(question.assets) or NOT_INFORMED,
        period=question.period or NOT_INFORMED,
        context=question.context or NOT_INFORMED,
    )
    return {"question_block": question_block, "format_instructions": get_format_instructions()}
