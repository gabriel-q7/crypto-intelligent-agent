"""Alvo da avaliação: executa um exemplo do dataset no sistema real e devolve saídas estruturadas.

Modos (``inputs.mode``):
- ``agent``: turno completo do agente integrado numa sessão nova (``IntegratedAgentService``),
  com mensagens de preparação (``setup``) gravadas no SQLite e, se ``restart``, um novo serviço
  e um novo ``ConversationStore`` sobre o mesmo arquivo antes da pergunta;
- ``verify``: avalia a CAMADA DE VERIFICAÇÃO com uma resposta plantada. As ferramentas listadas
  em ``evidence_tools`` são executadas de verdade (CoinPaprika, Chroma, cálculo) e marcadores como
  ``{BTC.price_usd_fmt}`` são preenchidos com os valores reais antes da verificação.
"""

import json
import re
import time

from app.api.container import AppContainer
from app.integrated.agent import build_integrated_agent
from app.integrated.history import SessionHistoryAdapter
from app.integrated.service import IntegratedAgentService
from app.integrated.tools import TOOL_CATEGORY, structured_tools
from app.agents.factory import build_agent_tools
from app.persistence.sqlite_store import ConversationStore
from app.services.grounding import format_pct, format_usd, parse_observation

PLACEHOLDER = re.compile(r"\{([A-Z]+)\.([a-z0-9_]+)\}")
SETUP_ACK = "Entendido."


def _fill(answer: str, observations: list[dict]) -> str:
    tickers = {o["data"]["symbol"]: o["data"] for o in observations if o.get("ok") and o.get("tool") == "get_market_data"}

    def value(match: re.Match) -> str:
        data = tickers.get(match.group(1), {})
        key = match.group(2)
        change = data.get("percent_change_24h") or 0.0
        return {
            "price_usd_fmt": format_usd(data.get("price_usd")),
            "opposite_verb": "subiu" if change < 0 else "caiu",
            "abs_change_24h_fmt": format_pct(abs(change)).lstrip("+"),
        }.get(key, match.group(0))

    return PLACEHOLDER.sub(value, answer)


class EvaluationTarget:
    def __init__(self, container: AppContainer) -> None:
        self.container = container
        self.tools = {t.name: t for t in structured_tools(
            build_agent_tools(container.coinpaprika, container.knowledge, container.retrieval, container.text_to_query))}

    def _fresh_service(self) -> IntegratedAgentService:
        """'Reinício': novo store e novo serviço sobre os mesmos arquivos (SQLite e Mem0/Chroma)."""
        c = self.container
        store = ConversationStore(c.conversation_store.db_path)
        return IntegratedAgentService(
            build_integrated_agent(c.tool_llm, list(self.tools.values())), c.integrated.verifier, store,
            SessionHistoryAdapter(store, c.memory_backend, c.conversation_settings), c.conversation,
            model_name=c.settings.ollama_model,
        )

    def __call__(self, inputs: dict) -> dict:
        started = time.perf_counter()
        out = self._agent(inputs) if inputs.get("mode", "agent") == "agent" else self._verify(inputs)
        out["elapsed_s"] = round(time.perf_counter() - started, 1)
        return out

    def _agent(self, inputs: dict) -> dict:
        service = self.container.integrated
        session = service.store.create_session("avaliação fase 5")
        for text in inputs.get("setup", []):
            service.store.append_message(session.id, "user", text)
            service.store.append_message(session.id, "assistant", SETUP_ACK)
        if inputs.get("restart"):
            service = self._fresh_service()
        reply = service.handle(session.id, inputs["question"], tags=["evaluation"])
        v = reply.verification
        observations = [parse_observation(s.output) or {} for s in reply.trajectory]
        return {
            "answer": reply.answer,
            "status": reply.status,
            "tools": [s.tool for s in reply.trajectory],
            "categories": sorted({s.category for s in reply.trajectory}),
            "steps": [{"tool": s.tool, "args": s.args, "ok": s.ok, "error_kind": s.error_kind} for s in reply.trajectory],
            "observations": observations,
            "verification": {
                "status": v.status, "original_status": v.original_status, "corrected": v.correction_attempted,
                "corrected_passed": v.corrected_passed, "labels": [c.label for c in v.claims],
                "findings": [f.kind for f in v.logical_findings if f.severity == "error"], "errors": v.verifier_errors,
            },
            "original_answer": reply.original_answer,
            "session_id": session.id,
        }

    def _verify(self, inputs: dict) -> dict:
        observations = []
        for call in inputs.get("evidence_tools", []):
            raw = self.tools[call["tool"]].invoke(call.get("args", {}))
            observations.append(parse_observation(raw) or json.loads(raw))
        answer = _fill(inputs["answer"], observations)
        v = self.container.integrated.verifier.verify(inputs["question"], answer, observations)
        return {
            "answer": answer,
            "status": "n/a",
            "tools": [c["tool"] for c in inputs.get("evidence_tools", [])],
            "categories": sorted({TOOL_CATEGORY[c["tool"]] for c in inputs.get("evidence_tools", [])}),
            "steps": [],
            "observations": observations,
            "verification": {
                "status": v.status, "original_status": None, "corrected": False, "corrected_passed": None,
                "labels": [c.label for c in v.claims], "findings": [f.kind for f in v.logical_findings if f.severity == "error"],
                "errors": v.verifier_errors,
            },
            "original_answer": None,
        }
