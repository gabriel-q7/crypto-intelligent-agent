"""Título automático da sessão: limpeza da saída do modelo e fallback."""

from datetime import UTC, datetime

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.conversation.title import TITLE_MAX_CHARS, clean_title, fallback_title, generate_title
from app.persistence.sqlite_store import StoredMessage


def msg(seq: int, role: str, content: str) -> StoredMessage:
    return StoredMessage("s", seq, role, content, {}, datetime.now(UTC))


TURN = [msg(1, "user", "Qual é o preço atual do Bitcoin em dólar agora mesmo, por favor?"), msg(2, "assistant", "US$ 81 mil.")]


@pytest.mark.parametrize("raw, expected", [
    ('{"title": "Halving do Bitcoin."}', "Halving do Bitcoin"),
    ('{"resposta": "x"}', ""),
    ('"Preço atual do Bitcoin."', "Preço atual do Bitcoin"),
    ("Título: Staking no Ethereum", "Staking no Ethereum"),
    ("## **Comparação BTC x ETH**\nExplicação extra", "Comparação BTC x ETH"),
    ("<think>pensando...</think>\nStablecoins e riscos", "Stablecoins e riscos"),
])
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


def test_clean_title_truncates():
    assert len(clean_title("palavra " * 30)) == TITLE_MAX_CHARS


def test_generate_title_uses_model():
    assert generate_title(FakeListChatModel(responses=["Preço do Bitcoin"]), TURN) == "Preço do Bitcoin"


@pytest.mark.parametrize("llm", [None, FakeListChatModel(responses=["  "]), FakeListChatModel(responses=['""'])])
def test_generate_title_falls_back_to_first_question(llm):
    title = generate_title(llm, TURN)
    assert title == fallback_title(TURN[0].content) and title.startswith("Qual é o preço") and title.endswith("…")


def test_generate_title_survives_model_error():
    class Boom(FakeListChatModel):
        def _call(self, *args, **kwargs):
            raise RuntimeError("ollama caiu")

    assert generate_title(Boom(responses=["x"]), TURN) == fallback_title(TURN[0].content)
