"""Dublês determinísticos para os testes da Fase 3."""

import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class RoutingFakeChat(BaseChatModel):
    """Responde conforme o tipo de prompt (classificação, resumo, análise, conhecimento).

    ``calls`` registra (tipo, conteúdo humano) para os testes inspecionarem o contexto enviado.
    """

    classifier: dict | None = None  # None = JSON inválido (força só regras)
    summary: dict | None = {"summary": "Resumo: o usuário pesquisa BTC e ETH.", "constraints": [], "open_questions": []}
    analysis: dict = {"summary": "Análise baseada nos dados.", "key_points": [], "uncertainties": []}
    knowledge: dict = {"answer": "Resposta conceitual baseada nos trechos.", "used_sources": []}
    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "routing-fake"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        system = str(messages[0].content) if messages else ""
        human = str(messages[-1].content) if messages else ""
        if "Classifique a MENSAGEM ATUAL" in system:
            kind, payload = "classifier", self.classifier
        elif "resume uma conversa" in system:
            kind, payload = "summary", self.summary
        elif "TRECHOS" in human:
            kind, payload = "knowledge", self.knowledge
        else:
            kind, payload = "analysis", self.analysis
        self.calls.append((kind, human))
        content = "inválido" if payload is None else json.dumps(payload, ensure_ascii=False)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def kinds(self) -> list[str]:
        return [k for k, _ in self.calls]
