"""Serviço de conversa da Fase 3: um turno = interpretar → montar contexto → executar → persistir.

Fluxo de ``ConversationService.handle``:
1. trava por sessão (turnos da mesma sessão são serializados; sessões diferentes, paralelas);
2. grava a mensagem do usuário no SQLite (fonte da verdade);
3. classifica a mensagem (regras + LLM) e resolve o contexto ativo da sessão;
4. recupera memórias da sessão (Mem0/Chroma, filtradas por ``run_id``) e monta o contexto com
   orçamento de tokens (janela + resumo + memórias);
5. executa com as chains LCEL da Fase 2 (dados atuais da CoinPaprika) ou pede esclarecimento;
6. grava resposta, análise e contexto ativo; indexa a análise como memória episódica;
7. manutenção (resumo + extração semântica) em segundo plano.

Não usa APIs depreciadas: nada de ``initialize_agent``, ``RouterChain`` ou ``ConversationBufferMemory``.
"""

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from app.chains.market_chains import (
    build_comparison_chain,
    build_defi_chain,
    build_knowledge_chain,
    build_market_analysis_chain,
)
from app.config import ConversationSettings
from app.conversation.context_builder import build_context
from app.conversation.schemas import ContextUsage, ConversationReply, ResolvedEntities
from app.intent.classifier import classify_message, rule_signals
from app.intent.resolver import active_context_from, resolve
from app.intent.schemas import ConversationIntent as I
from app.intent.schemas import ResolvedRequest
from app.knowledge.store import KnowledgeHit
from app.memory.long_term import MemoryBackend, MemoryHit
from app.memory.short_term import ConversationSummarizer, render_summary
from app.persistence.sqlite_store import ConversationStore, StoredAnalysis
from app.schemas.report import CryptoReport, ReportStatus
from app.services.grounding import findings_from, format_pct, format_usd, sources_from
from app.tools.coinpaprika import CoinPaprikaClient
from app.tools.market_tools import observe_ticker

logger = logging.getLogger(__name__)

DATA_INTENTS = {I.MARKET_ANALYSIS, I.ASSET_COMPARISON, I.HISTORICAL_ANALYSIS, I.PREVIOUS_RESULT_REFERENCE}
CONTEXT_INTENTS = DATA_INTENTS | {I.DEFI_ANALYSIS}


def _latest_retrieved(report: CryptoReport) -> datetime | None:
    stamps = [s.retrieved_at for s in report.data_sources if s.retrieved_at]
    return max(stamps) if stamps else None


class ConversationService:
    def __init__(
        self,
        store: ConversationStore,
        memory: MemoryBackend,
        llm: BaseChatModel,
        client: CoinPaprikaClient,
        knowledge_search: Callable[[str], list[KnowledgeHit]],
        settings: ConversationSettings,
        background: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store, self.memory, self.llm, self.client = store, memory, llm, client
        self.settings = settings
        self.summarizer = ConversationSummarizer(llm, settings)
        self.clock = clock
        self.chains: dict[I, Runnable] = {
            I.MARKET_ANALYSIS: build_market_analysis_chain(llm, client),
            I.HISTORICAL_ANALYSIS: build_market_analysis_chain(llm, client),
            I.ASSET_COMPARISON: build_comparison_chain(llm, client),
            I.DEFI_ANALYSIS: build_defi_chain(),
            I.GENERAL_RESEARCH: build_knowledge_chain(llm, knowledge_search),
        }
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory") if background else None
        self.pending: list[Future] = []

    # ------------------------------------------------------------------ infraestrutura

    def _lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.Lock())

    def close(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=True)

    def wait_maintenance(self) -> None:
        """Usado em testes e no desligamento: espera resumos e extrações em andamento."""
        for future in list(self.pending):
            future.result()
        self.pending.clear()

    # ------------------------------------------------------------------ turno

    def handle(self, session_id: str, message: str) -> ConversationReply:
        self.store.require_session(session_id)
        with self._lock(session_id):
            user = self.store.append_message(session_id, "user", message)
            session = self.store.require_session(session_id)
            active = session.active_context or {}
            recent = self.store.list_messages(session_id, last=self.settings.window_messages)
            history_lines = "\n".join(f"{m.role}: {m.content[:200]}" for m in recent[:-1][-4:])

            extracted = classify_message(message, self.llm, active, history_lines)
            resolved = resolve(extracted, active, self.store.list_analyses(session_id))
            self.store.update_message_metadata(session_id, user.seq, {
                "intent": extracted.intent.value, "assets": extracted.assets, "is_correction": extracted.is_correction,
            })

            memories, memory_note = self._recall(session_id, message, resolved)
            summary = self.store.get_summary(session_id)
            context = build_context(
                message, active if not resolved.new_topic else None, render_summary(summary),
                recent, memories, self.settings.context_budget_tokens,
            )

            limitations = [memory_note] if memory_note else []
            report, analysis = None, None
            preference_only = is_preference_statement(message)
            if preference_only:
                # Declaração de preferência/restrição: registrada (memória semântica, em segundo plano)
                # e confirmada; não dispara nova análise nem muda o pedido em andamento.
                answer, status = (
                    "Anotado: vou considerar essa preferência nas próximas análises desta conversa.", "complete"
                )
            elif resolved.clarification_question:
                answer, status = resolved.clarification_question, "needs_clarification"
            else:
                report = self._execute(session_id, message, resolved, context.text)
                answer, status = report.answer, report.status.value
                limitations += report.uncertainties
                if resolved.effective_intent in DATA_INTENTS and report.data_sources:
                    analysis = self._save_analysis(session_id, user.seq, resolved, report)

            assistant = self.store.append_message(session_id, "assistant", answer, {
                "intent": resolved.intent.value, "effective_intent": resolved.effective_intent.value,
                "assets": resolved.assets, "protocols": resolved.protocols, "period": resolved.period,
                "analysis_id": analysis.id if analysis else None,
                "data_retrieved_at": analysis.data_retrieved_at.isoformat() if analysis and analysis.data_retrieved_at else None,
                "clarification": bool(resolved.clarification_question),
            })
            if not preference_only and not resolved.clarification_question and resolved.effective_intent in CONTEXT_INTENTS:
                self.store.set_active_context(session_id, active_context_from(resolved))

        self._schedule_maintenance(session_id, message, rule_signals(message))
        return ConversationReply(
            session_id=session_id,
            user_seq=user.seq,
            assistant_seq=assistant.seq,
            answer=answer,
            status=status,
            intent=resolved.intent,
            effective_intent=resolved.effective_intent,
            entities=ResolvedEntities(
                assets=resolved.assets, protocols=resolved.protocols, networks=resolved.networks,
                metrics=resolved.metrics, period=resolved.period, period_days=resolved.period_days,
            ),
            context_sources=dict(resolved.context_sources),
            referenced_analyses=[resolved.referenced_analysis_id] if resolved.referenced_analysis_id else [],
            retrieved_memories=context.memory_refs,
            data_sources=report.data_sources if report else [],
            missing_information=resolved.missing_information,
            limitations=list(dict.fromkeys(limitations)),
            clarification_needed=bool(resolved.clarification_question),
            analysis_id=analysis.id if analysis else None,
            report=report,
            context_usage=ContextUsage(
                tokens=context.tokens, budget_tokens=self.settings.context_budget_tokens,
                included=context.included, dropped=context.dropped,
            ),
        )

    # ------------------------------------------------------------------ etapas

    def _recall(self, session_id: str, message: str, resolved: ResolvedRequest) -> tuple[list[MemoryHit], str | None]:
        query = " ".join([message, *resolved.assets, resolved.period or ""]).strip()
        try:
            return self.memory.search(session_id, query, self.settings.memory_top_k), None
        except Exception as exc:  # noqa: BLE001 - memória longa é auxiliar: falha não derruba o turno
            logger.warning("memory recall failed session=%s: %s", session_id, exc)
            return [], f"memória de longo prazo indisponível neste turno ({type(exc).__name__})"

    def _execute(self, session_id: str, message: str, resolved: ResolvedRequest, context: str) -> CryptoReport:
        intent = resolved.effective_intent
        if intent == I.PREVIOUS_RESULT_REFERENCE:
            return self._compare_with_previous(session_id, message, resolved)
        inputs = {
            "question": message,
            "assets": resolved.assets,
            "period_days": resolved.period_days,
            "conversation_context": context,
            "decisions": [f"intenção={resolved.intent.value} → execução={intent.value}"],
            "observations": [],
            "uncertainties": [],
        }
        report = self.chains[intent].invoke(inputs)
        if intent == I.DEFI_ANALYSIS and (resolved.assets or resolved.period):
            preserved = ", ".join(filter(None, [", ".join(resolved.assets), resolved.period]))
            report = report.model_copy(update={"answer": f"{report.answer} Contexto mantido para quando a fonte existir: {preserved}."})
        return report

    def _save_analysis(self, session_id: str, seq: int, resolved: ResolvedRequest, report: CryptoReport) -> StoredAnalysis:
        analysis = self.store.save_analysis(
            session_id, seq, resolved.effective_intent.value,
            request={"assets": resolved.assets, "period": resolved.period, "period_days": resolved.period_days,
                     "metrics": resolved.metrics},
            report={"report": report.model_dump(mode="json")},
            data_retrieved_at=_latest_retrieved(report),
        )
        self._index_analysis(analysis)
        return analysis

    def _index_analysis(self, analysis: StoredAnalysis) -> None:
        """Memória episódica sem LLM. SQLite já tem o registro; o Chroma é melhor esforço."""
        report = analysis.report.get("report", {})
        stamp = analysis.data_retrieved_at.isoformat(timespec="minutes") if analysis.data_retrieved_at else "?"
        text = (
            f"Análise {analysis.id} ({analysis.intent}) de {', '.join(analysis.request.get('assets', []))}"
            f"{' para ' + analysis.request['period'] if analysis.request.get('period') else ''}, dados de {stamp}: "
            f"{report.get('answer', '')[:300]}"
        )
        try:
            self.memory.add_episode(analysis.session_id, text, {
                "category": "episodic", "doc_type": "analysis", "source_ref": f"analysis:{analysis.id}",
            })
            self.store.mark_indexed(analysis.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("episode indexing failed analysis=%s (fica pendente): %s", analysis.id, exc)

    def _compare_with_previous(self, session_id: str, message: str, resolved: ResolvedRequest) -> CryptoReport:
        """Resultado anterior (histórico) × dados atuais, com a comparação feita pelo código."""
        previous = self.store.get_analysis(session_id, resolved.referenced_analysis_id or "")
        if previous is None:  # o resolver só chega aqui com id da própria sessão; defesa extra
            return CryptoReport(question=message, mode="conversation:previous_result", status=ReportStatus.FAILED,
                                answer="A análise anterior não foi encontrada nesta conversa.")
        observations = []
        for asset in resolved.assets:
            try:
                observations.append(observe_ticker(self.client, self.client.resolve_asset(asset).id))
            except Exception as exc:  # noqa: BLE001
                observations.append({"ok": False, "tool": "get_market_data", "error": {"kind": "network", "message": str(exc)}})
        current = {o["data"]["symbol"]: o for o in observations if o.get("ok")}
        prev_report = previous.report.get("report", {})
        prev_prices = {
            f["statement"].split("(")[1].split(")")[0]: f["statement"]
            for f in prev_report.get("findings", [])
            if ": preço " in f["statement"] and "(" in f["statement"]
        }
        age = self.clock() - (previous.data_retrieved_at or previous.created_at)
        age_text = f"{age.total_seconds() / 3600:.1f} h" if age.total_seconds() >= 3600 else f"{int(age.total_seconds() // 60)} min"
        stale = age.total_seconds() / 60 >= self.settings.stale_after_minutes
        stamp = (previous.data_retrieved_at or previous.created_at).strftime("%Y-%m-%d %H:%M UTC")

        lines, uncertainties = [], [f"o resultado anterior ({previous.id}) é HISTÓRICO: dados de {stamp}, há {age_text}"]
        for symbol in resolved.assets:
            obs = current.get(symbol)
            before = prev_prices.get(symbol)
            if obs is None:
                uncertainties.append(f"{symbol}: dado atual não obtido; não há comparação")
                continue
            now_price = obs["data"]["price_usd"]
            text = f"{symbol}: agora {format_usd(now_price)} (24h {format_pct(obs['data'].get('percent_change_24h'))})"
            if before:
                then_price = float(before.split("US$ ")[1].replace(".", "").replace(",", "."))
                delta = (now_price - then_price) / then_price * 100 if then_price else 0.0
                text += f"; no resultado anterior {format_usd(then_price)} → diferença {format_pct(delta)}"
            else:
                text += "; o resultado anterior não tinha o preço deste ativo"
            lines.append(text)
        failed = not current
        answer = (
            f"Comparação com o resultado anterior {previous.id} (dados de {stamp}, há {age_text}"
            f"{'; NÃO são dados atuais' if stale else ''}) e com dados atuais da CoinPaprika. " + ". ".join(lines) + "."
            if lines else "Não foi possível obter os dados atuais para comparar com o resultado anterior."
        )
        return CryptoReport(
            question=message,
            mode="conversation:previous_result",
            status=ReportStatus.FAILED if failed else (ReportStatus.PARTIAL if len(current) < len(resolved.assets) else ReportStatus.COMPLETE),
            answer=answer,
            findings=findings_from(observations),
            data_sources=sources_from(observations),
            uncertainties=uncertainties,
            decision_summary=[f"referência à análise {previous.id}; dados atuais buscados para {', '.join(resolved.assets)}"],
        )

    # ------------------------------------------------------------------ manutenção

    def _schedule_maintenance(self, session_id: str, message: str, signals) -> None:
        if self._executor is None:
            self._maintain(session_id, message, signals)
        else:
            self.pending = [f for f in self.pending if not f.done()]
            self.pending.append(self._executor.submit(self._maintain, session_id, message, signals))

    def _maintain(self, session_id: str, message: str, signals) -> None:
        """Resumo, reindexação pendente e extração semântica. Falhas são registradas, não propagadas."""
        if self.store.get_session(session_id) is None:
            return  # sessão apagada enquanto a tarefa esperava
        try:
            if summary := self.summarizer.maybe_update(self.store, session_id):
                self.memory.replace_episode(session_id, "summary", f"Resumo da sessão: {summary.summary}", {
                    "category": "episodic", "source_ref": f"summary:{session_id}:v{summary.version}",
                })
            for analysis in self.store.pending_index(session_id):
                self._index_analysis(analysis)
            # Extração semântica só quando a fala traz restrição, correção ou preferência.
            if self.settings.memory_extraction and (signals.correction or _has_preference(message)):
                kept = self.memory.extract_from_user(session_id, [message])
                logger.info("semantic memories kept session=%s count=%d", session_id, len(kept))
        except Exception as exc:  # noqa: BLE001
            logger.warning("maintenance failed session=%s: %s", session_id, exc)

    def delete_session(self, session_id: str) -> bool:
        with self._lock(session_id):
            deleted = self.store.delete_session(session_id)
        if deleted:
            try:
                self.memory.delete_session(session_id)
            except Exception as exc:  # noqa: BLE001 - SQLite já apagou; o Chroma fica para limpeza manual
                logger.error("memory deletion failed session=%s: %s", session_id, exc)
        return deleted


_REQUEST_WORDS = ("compar", "analis", "analyz", "qual ", "quais", "como ", "mostre", "show", "what", "how ", "preço", "price")


def is_preference_statement(message: str) -> bool:
    """Fala que só declara preferência/restrição (sem pedir análise): "Prefiro sempre foco em risco"."""
    text = message.lower()
    return _has_preference(message) and "?" not in text and not any(w in text for w in _REQUEST_WORDS)


def _has_preference(message: str) -> bool:
    text = message.lower()
    return any(k in text for k in ("prefiro", "sempre", "nunca", "quero que", "considere sempre", "i prefer", "always", "never"))

