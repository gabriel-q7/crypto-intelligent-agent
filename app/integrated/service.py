"""Turno do agente integrado (Fase 5): histórico → agente → verificação → (1 correção) → entrega.

1. trava por sessão (a mesma da Fase 3) e grava a mensagem do usuário no SQLite;
2. ``SessionHistoryAdapter`` monta o histórico (resumo + memórias Mem0 + janela);
3. ``create_agent`` escolhe e executa as ferramentas (API, SQL, RAG, cálculo);
4. ``VerificationService`` verifica a resposta contra as observações reais;
5. se a política pedir, **uma** correção: o agente recebe as próprias observações (sem repetir
   chamadas) e o feedback estruturado; a nova resposta é verificada de novo;
6. se ainda falhar, sai uma resposta cautelosa (frases contraditas removidas, não sustentadas
   marcadas, aviso com as pendências);
7. grava a resposta (uma vez), a análise (se houve dado de mercado) e agenda a manutenção da Fase 3.
"""

import hashlib
import logging
import time

from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from langsmith import traceable, tracing_context

from app.conversation.chatbot import ConversationService
from app.integrated.agent import AgentRun, run_agent
from app.integrated.history import SessionHistoryAdapter
from app.integrated.schemas import AgentReply
from app.persistence.sqlite_store import ConversationStore
from app.schemas.report import CryptoReport, ReportStatus
from app.services.grounding import findings_from, sources_from
from app.verification.policy import cautious_answer, correction_message, needs_correction
from app.verification.schemas import VerificationResult
from app.verification.service import VerificationService

logger = logging.getLogger(__name__)

MARKET_TOOLS = {"get_market_data", "get_market_details", "get_global_market", "query_market_database"}
MAX_CORRECTIONS = 1


def session_tag(session_id: str) -> str:
    """Identificador para os traces sem expor o id real da sessão."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:12]


class IntegratedAgentService:
    def __init__(
        self,
        agent: Runnable,
        verifier: VerificationService,
        store: ConversationStore,
        history: SessionHistoryAdapter,
        conversation: ConversationService,
        model_name: str | None = None,
    ) -> None:
        self.agent, self.verifier, self.store = agent, verifier, store
        self.history, self.conversation, self.model_name = history, conversation, model_name

    # ------------------------------------------------------------------ etapas rastreadas

    @traceable(name="integrated.agent_run", run_type="chain")
    def _run(self, history, message, extra=()) -> AgentRun:
        return run_agent(self.agent, history, message, extra=extra)

    @traceable(name="integrated.correction", run_type="chain")
    def _correct(self, history, message: str, first: AgentRun, verification: VerificationResult) -> AgentRun:
        previous = first.messages[len(history) + 1:]  # chamadas, observações e resposta do 1º passo
        feedback = HumanMessage(content=correction_message(verification.correction_instructions))
        return self._run(history, message, extra=[*previous, feedback])

    # ------------------------------------------------------------------ turno

    def handle(self, session_id: str, message: str, tags: list[str] | None = None) -> AgentReply:
        """Ponto de entrada: abre o trace do turno com metadados (sessão em hash, nunca o id real)."""
        metadata = {"phase": 5, "engine": "integrated_agent", "session": session_tag(session_id), "model": self.model_name}
        with tracing_context(metadata=metadata, tags=["phase5", *(tags or [])]):
            return self._handle(session_id, message)

    @traceable(name="integrated.turn", run_type="chain")
    def _handle(self, session_id: str, message: str) -> AgentReply:
        started = time.perf_counter()
        self.store.require_session(session_id)
        with self.conversation.session_lock(session_id):
            user = self.store.append_message(session_id, "user", message, {"engine": "agent"})
            bundle = self.history.load(session_id, message, before_seq=user.seq)
            first = self._run(bundle.messages, message)
            verification = self.verifier.verify(message, first.answer, first.observations)

            final, answer, original = first, first.answer, None
            observations = list(first.observations)
            if needs_correction(verification.status):
                corrected = self._correct(bundle.messages, message, first, verification)
                observations += corrected.observations  # novas chamadas, se houve, entram na evidência
                second = self.verifier.verify(message, corrected.answer, observations)
                original_status = verification.status
                passed = second.status == "verified"
                original = first.answer
                final = AgentRun(answer=corrected.answer, steps=first.steps + _renumber(corrected.steps, len(first.steps)),
                                 observations=observations, messages=corrected.messages)
                answer = corrected.answer if passed or second.status == "verification_failed" else cautious_answer(
                    corrected.answer, second.claims, second.logical_findings)
                verification = second.model_copy(update={
                    "correction_attempted": True, "corrected_passed": passed, "original_status": original_status,
                })
            if not answer.strip():
                answer = "Não foi possível gerar uma resposta para esta pergunta."
            if verification.status == "verification_failed":
                answer += "\n\nAviso: a verificação automática não pôde ser concluída; esta resposta não foi verificada."

            status = self._status(final, verification)
            limitations = [f"{s.tool}: {s.error_kind} — {s.error_message}" for s in final.steps if not s.ok]
            limitations += verification.verifier_errors
            if bundle.note:
                limitations.append(bundle.note)
            analysis_id = self._save_analysis(session_id, user.seq, message, answer, final, status)
            assistant = self.store.append_message(session_id, "assistant", answer, {
                "engine": "agent", "tools": [s.tool for s in final.steps], "verification": verification.summary(),
                "corrected": verification.correction_attempted, "analysis_id": analysis_id,
            })
        self.conversation.schedule_maintenance(session_id, message)
        return AgentReply(
            session_id=session_id, user_seq=user.seq, assistant_seq=assistant.seq, answer=answer, status=status,
            verification=verification, original_answer=original, tools_used=[s.tool for s in final.steps],
            trajectory=final.steps, findings=findings_from(final.observations),
            data_sources=sources_from(final.observations), limitations=list(dict.fromkeys(limitations)),
            history_messages=len(bundle.messages), retrieved_memories=bundle.memory_refs, analysis_id=analysis_id,
            elapsed_s=round(time.perf_counter() - started, 1),
        )

    @staticmethod
    def _status(run: AgentRun, verification: VerificationResult) -> str:
        market = [s for s in run.steps if s.tool in MARKET_TOOLS]
        if market and not any(s.ok for s in market):
            return "failed"
        if any(not s.ok for s in run.steps) or verification.status != "verified":
            return "partial"
        return "complete"

    def _save_analysis(self, session_id: str, seq: int, message: str, answer: str, run: AgentRun, status: str) -> str | None:
        """Episódio da Fase 3 (resultado anterior + Mem0) quando houve dado de mercado real."""
        market_obs = [o for o in run.observations if o.get("ok") and o.get("tool") in {"get_market_data", "query_market_database"}]
        if not market_obs:
            return None
        report = CryptoReport(
            question=message, mode="integrated_agent", status=ReportStatus(status), answer=answer,
            findings=findings_from(run.observations), data_sources=sources_from(run.observations), model=self.model_name,
        )
        assets = sorted({o["data"]["symbol"] for o in market_obs if o.get("tool") == "get_market_data"})
        stamps = [s.retrieved_at for s in report.data_sources if s.retrieved_at]
        analysis = self.store.save_analysis(
            session_id, seq, "integrated_agent", request={"assets": assets, "period": None, "period_days": None, "metrics": []},
            report={"report": report.model_dump(mode="json")}, data_retrieved_at=max(stamps) if stamps else None,
        )
        self.conversation.index_analysis(analysis)
        return analysis.id


def _renumber(steps, offset: int):
    return [s.model_copy(update={"order": s.order + offset}) for s in steps]
