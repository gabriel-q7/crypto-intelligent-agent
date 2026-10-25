"""Callback LangChain que registra um rastro observável da execução.

Registra decisões (qual ferramenta, com qual entrada), chamadas de ferramenta (duração,
sucesso/erro), chamadas ao LLM (duração, tokens) e erros. Não registra o "Thought" do
modelo: o rastro mostra O QUE foi feito, não o raciocínio privado.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger("app.trace")


def _short(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class TraceEvent:
    kind: str
    name: str
    detail: str = ""
    duration_ms: float | None = None
    ok: bool | None = None
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds"))


class TraceCallbackHandler(BaseCallbackHandler):
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self._started: dict[UUID, tuple[str, float]] = {}

    # ------------------------------------------------------------------ util
    def _record(self, event: TraceEvent, level: int = logging.INFO) -> None:
        self.events.append(event)
        logger.log(
            level,
            "trace kind=%s name=%s ok=%s duration_ms=%s detail=%s",
            event.kind, event.name, event.ok,
            None if event.duration_ms is None else round(event.duration_ms), event.detail,
        )

    def _elapsed(self, run_id: UUID) -> tuple[str, float | None]:
        name, started = self._started.pop(run_id, ("?", None))
        return name, None if started is None else (time.perf_counter() - started) * 1000

    def to_dicts(self) -> list[dict]:
        return [asdict(e) for e in self.events]

    # ------------------------------------------------------------------ agente
    def on_agent_action(self, action: AgentAction, *, run_id: UUID, **kwargs: Any) -> None:
        self._record(TraceEvent("decision", action.tool, f"input={_short(action.tool_input, 120)}"))

    def on_agent_finish(self, finish: AgentFinish, *, run_id: UUID, **kwargs: Any) -> None:
        self._record(TraceEvent("finish", "agent", _short(finish.return_values.get("output", ""), 160)))

    # ------------------------------------------------------------------ ferramentas
    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID, **kwargs: Any) -> None:
        self._started[run_id] = (serialized.get("name") or kwargs.get("name") or "tool", time.perf_counter())

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        name, elapsed = self._elapsed(run_id)
        text = getattr(output, "content", output)
        ok, detail = None, _short(text)
        try:
            payload = json.loads(text)
            ok = bool(payload.get("ok"))
            if not ok:
                detail = f"error={payload.get('error', {}).get('kind')}: {_short(payload.get('error', {}).get('message', ''), 150)}"
        except (TypeError, ValueError, AttributeError):
            pass
        self._record(TraceEvent("tool", name, detail, elapsed, ok), logging.INFO if ok is not False else logging.WARNING)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        name, elapsed = self._elapsed(run_id)
        self._record(TraceEvent("tool", name, f"{type(error).__name__}: {_short(error)}", elapsed, False), logging.ERROR)

    # ------------------------------------------------------------------ LLM
    def on_chat_model_start(self, serialized: dict, messages: list, *, run_id: UUID, **kwargs: Any) -> None:
        self._started[run_id] = ("llm", time.perf_counter())

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID, **kwargs: Any) -> None:
        self._started[run_id] = ("llm", time.perf_counter())

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        _, elapsed = self._elapsed(run_id)
        usage = ""
        try:
            message = response.generations[0][0].message  # type: ignore[attr-defined]
            meta = getattr(message, "usage_metadata", None) or {}
            usage = f"input_tokens={meta.get('input_tokens')} output_tokens={meta.get('output_tokens')}"
        except (IndexError, AttributeError):
            pass
        self._record(TraceEvent("llm", "llm", usage, elapsed, True))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        _, elapsed = self._elapsed(run_id)
        self._record(TraceEvent("llm", "llm", f"{type(error).__name__}: {_short(error)}", elapsed, False), logging.ERROR)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._record(TraceEvent("error", "chain", f"{type(error).__name__}: {_short(error)}", ok=False), logging.ERROR)
