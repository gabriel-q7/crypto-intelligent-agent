from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

from app.prompts import (
    ANALYSIS_PROMPT,
    QUESTION_PROMPT,
    SYSTEM_INSTRUCTIONS,
    build_prompt_input,
)
from app.schemas import AnalysisIntent


def test_prompt_types():
    assert isinstance(QUESTION_PROMPT, PromptTemplate)
    assert isinstance(ANALYSIS_PROMPT, ChatPromptTemplate)
    assert set(ANALYSIS_PROMPT.input_variables) == {"question_block", "format_instructions"}


def test_system_instructions_forbid_inventing_market_data():
    text = SYSTEM_INSTRUCTIONS.lower()
    assert "nunca invente" in text
    for term in ("preços", "volumes", "dado atual"):
        assert term in text
    assert "somente com um objeto json" in text


def test_system_instructions_list_all_intents():
    for intent in AnalysisIntent:
        assert intent.value in SYSTEM_INSTRUCTIONS


def test_build_prompt_input_renders_question(question):
    data = build_prompt_input(question)
    block = data["question_block"]
    assert "Pergunta: Compare BTC e ETH" in block
    assert "Ativos informados: BTC, ETH" in block
    assert "Período informado: últimos 30 dias" in block
    assert "Contexto adicional: não informado" in block
    assert "investigation_steps" in data["format_instructions"]


def test_build_prompt_input_includes_reference_date(question):
    from datetime import date

    block = build_prompt_input(question, reference_date=date(2026, 10, 17))["question_block"]
    assert "Data de referência: 2026-10-17" in block
    assert f"Data de referência: {date.today().isoformat()}" in build_prompt_input(question)[
        "question_block"
    ]


def test_build_prompt_input_accepts_dict():
    data = build_prompt_input({"question": "O que é staking?"})
    assert "Ativos informados: não informado" in data["question_block"]


def test_chat_prompt_produces_system_and_human_messages(question):
    messages = ANALYSIS_PROMPT.invoke(build_prompt_input(question)).to_messages()
    assert [m.type for m in messages] == ["system", "human", "ai", "human", "ai", "human"]
    assert messages[0].content == SYSTEM_INSTRUCTIONS
    assert "Compare BTC e ETH" in messages[-1].content
    # Chaves do JSON schema não podem quebrar o template (chaves {} escapadas).
    assert '"properties"' in messages[-1].content


def test_few_shot_examples_are_valid_analyses():
    from app.prompts.analysis import FEW_SHOT_EXAMPLES
    from app.schemas import AnswerStatus, CryptoAnalysisRequest

    messages = ANALYSIS_PROMPT.invoke(build_prompt_input({"question": "teste"})).to_messages()
    ai_messages = [m for m in messages if m.type == "ai"]
    examples = [CryptoAnalysisRequest.model_validate_json(m.content) for m in ai_messages]
    assert examples == [CryptoAnalysisRequest.model_validate(a) for _, a in FEW_SHOT_EXAMPLES]
    # Um exemplo de cada caso principal: depende de dados e conceitual respondido.
    assert {e.answer_status for e in examples} == {
        AnswerStatus.REQUIRES_MARKET_DATA,
        AnswerStatus.ANSWERED,
    }


def test_system_instructions_define_answer():
    from app.schemas import AnswerStatus

    for status in AnswerStatus:
        assert status.value in SYSTEM_INSTRUCTIONS
    assert "NUNCA escreva preços" in SYSTEM_INSTRUCTIONS


def test_system_instructions_separate_metrics_from_missing_information():
    from app.prompts.analysis import METRIC_VOCABULARY

    for metric in METRIC_VOCABULARY:
        assert metric in SYSTEM_INSTRUCTIONS
    assert "NÃO coloque dados de mercado aqui" in SYSTEM_INSTRUCTIONS
    assert "A primeira etapa obtém os dados de" in SYSTEM_INSTRUCTIONS
