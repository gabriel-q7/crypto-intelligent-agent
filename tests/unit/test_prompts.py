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


def test_build_prompt_input_accepts_dict():
    data = build_prompt_input({"question": "O que é staking?"})
    assert "Ativos informados: não informado" in data["question_block"]


def test_chat_prompt_produces_system_and_human_messages(question):
    messages = ANALYSIS_PROMPT.invoke(build_prompt_input(question)).to_messages()
    assert [m.type for m in messages] == ["system", "human"]
    assert messages[0].content == SYSTEM_INSTRUCTIONS
    assert "Compare BTC e ETH" in messages[1].content
    # Chaves do JSON schema não podem quebrar o template (chaves {} escapadas).
    assert '"properties"' in messages[1].content
