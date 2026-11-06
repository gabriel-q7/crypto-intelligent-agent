"""Dublês da Fase 5: chat com tool calling roteirizado, observações reais de exemplo e verificador roteirizado."""

import json
from collections.abc import Iterator

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.verification.schemas import ClaimVerification, LogicalFinding, VerificationResult

OBS_BTC = {
    "ok": True, "tool": "get_market_data",
    "data": {"id": "btc-bitcoin", "name": "Bitcoin", "symbol": "BTC", "rank": 1, "price_usd": 81545.05,
             "volume_24h_usd": 1.9e10, "market_cap_usd": 1.6e12, "percent_change_1h": -0.37,
             "percent_change_24h": -1.02, "percent_change_7d": -0.84, "percent_change_30d": -6.0},
    "source": {"provider": "CoinPaprika", "endpoint": "/tickers/btc-bitcoin",
               "source_updated_at": "2026-11-05T00:50:17Z", "retrieved_at": "2026-11-05T00:51:00Z"},
}
OBS_ETH = {
    "ok": True, "tool": "get_market_data",
    "data": {"id": "eth-ethereum", "name": "Ethereum", "symbol": "ETH", "rank": 2, "price_usd": 2426.11,
             "volume_24h_usd": 1.1e10, "market_cap_usd": 2.9e11, "percent_change_1h": 0.1,
             "percent_change_24h": 7.27, "percent_change_7d": 4.19, "percent_change_30d": -10.19},
    "source": {"provider": "CoinPaprika", "endpoint": "/tickers/eth-ethereum",
               "source_updated_at": "2026-11-05T00:50:17Z", "retrieved_at": "2026-11-05T00:51:00Z"},
}
OBS_RAG = {
    "ok": True, "tool": "search_knowledge_base",
    "data": [{"source": "blockchain-basics.md", "title": "Fundamentos de blockchain", "chunk_id": "blockchain-basics.md#3",
              "score": 0.30, "content": "Proof of Stake (PoS): validadores bloqueiam moedas como garantia e são escolhidos "
                                        "para propor e atestar blocos. Usado pelo Ethereum desde 2022."}],
    "source": {"provider": "base de conhecimento local (Chroma)", "endpoint": "crypto_knowledge"},
}
OBS_SQL_FAIL = {
    "ok": False, "tool": "query_market_database",
    "error": {"kind": "insufficient_coverage", "message": "A pergunta pede 90 dias, mas os snapshots cobrem 0 dia(s)"},
}


def tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


class ScriptedToolChat(GenericFakeChatModel):
    """Chat falso compatível com ``create_agent``: ``bind_tools`` devolve o próprio modelo e as
    respostas (com ou sem ``tool_calls``) saem na ordem do roteiro."""

    def bind_tools(self, tools, **kwargs):  # noqa: D102
        return self


def scripted(*messages: AIMessage | str) -> ScriptedToolChat:
    items: Iterator = iter([m if isinstance(m, AIMessage) else AIMessage(content=m) for m in messages])
    return ScriptedToolChat(messages=items)


def tool_runner(observations: dict[str, dict]):
    """Função de ferramenta que devolve a observação JSON por chave de argumento."""
    def run(**kwargs) -> str:
        key = next(iter(kwargs.values()), "") if kwargs else ""
        return json.dumps(observations.get(str(key).upper(), observations.get("*", {"ok": False, "tool": "?"})))
    return run


class ScriptedVerifier:
    """Verificador roteirizado: devolve os resultados na ordem e registra as chamadas."""

    def __init__(self, *results: VerificationResult) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str, int]] = []

    def verify(self, question: str, answer: str, observations: list[dict]) -> VerificationResult:
        self.calls.append((question, answer, len(observations)))
        return self.results.pop(0)


def result(status: str, claims: list[ClaimVerification] | None = None, findings: list[LogicalFinding] | None = None,
           instructions: list[str] | None = None) -> VerificationResult:
    return VerificationResult(status=status, claims=claims or [], logical_findings=findings or [],
                              correction_instructions=instructions or ["CORRIJA: exemplo"])
