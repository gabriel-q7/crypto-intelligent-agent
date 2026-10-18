"""Benchmark de modelos: latência, throughput e aderência da saída.

Executa as etapas da chain separadamente (prompt -> LLM -> parser -> validação) para
capturar as métricas que o Ollama devolve em ``response_metadata``, que a chain
completa descarta.
"""

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel

from app.errors import AnalysisOutputError
from app.prompts import ANALYSIS_PROMPT, build_prompt_input
from app.schemas import AnswerStatus, CryptoQuestion
from app.services.validation import validate_analysis

NS_PER_S = 1e9


class BenchmarkCase(BaseModel):
    id: str
    question: str
    assets: list[str] = []
    period: str | None = None
    context: str | None = None
    expected_status: AnswerStatus | None = None
    expected_assets: list[str] | None = None

    def to_question(self) -> CryptoQuestion:
        return CryptoQuestion(
            question=self.question, assets=self.assets, period=self.period, context=self.context
        )


def load_cases(path: Path) -> list[BenchmarkCase]:
    return [BenchmarkCase.model_validate(item) for item in json.loads(path.read_text())]


@dataclass
class RunResult:
    case_id: str
    run: int
    wall_s: float
    valid: bool
    status_ok: bool | None = None
    assets_ok: bool | None = None
    load_s: float | None = None
    prompt_tokens: int | None = None
    prompt_eval_s: float | None = None
    output_tokens: int | None = None
    eval_s: float | None = None
    error: str | None = None
    output: dict | None = None

    @property
    def tokens_per_s(self) -> float | None:
        if self.output_tokens and self.eval_s:
            return self.output_tokens / self.eval_s
        return None

    def to_dict(self) -> dict:
        return {**asdict(self), "tokens_per_s": self.tokens_per_s}


def _seconds(metadata: dict, key: str) -> float | None:
    value = metadata.get(key)
    return value / NS_PER_S if value is not None else None


def run_case(llm: BaseChatModel, case: BenchmarkCase, run: int) -> RunResult:
    """Executa um caso e mede tempo de parede e métricas do Ollama."""
    prompt = ANALYSIS_PROMPT.invoke(build_prompt_input(case.to_question()))
    started = perf_counter()
    message = llm.invoke(prompt)
    wall_s = perf_counter() - started

    meta = message.response_metadata or {}
    result = RunResult(
        case_id=case.id,
        run=run,
        wall_s=wall_s,
        valid=False,
        load_s=_seconds(meta, "load_duration"),
        prompt_tokens=meta.get("prompt_eval_count"),
        prompt_eval_s=_seconds(meta, "prompt_eval_duration"),
        output_tokens=meta.get("eval_count"),
        eval_s=_seconds(meta, "eval_duration"),
    )
    try:
        analysis = validate_analysis(JsonOutputParser().invoke(message))
    except (OutputParserException, AnalysisOutputError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"[:500]
        return result

    result.valid = True
    result.output = analysis.model_dump(mode="json")
    if case.expected_status is not None:
        result.status_ok = analysis.answer_status is case.expected_status
    if case.expected_assets is not None:
        result.assets_ok = set(analysis.assets) == {a.upper() for a in case.expected_assets}
    return result


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _rate(flags: list[bool | None]) -> float | None:
    known = [f for f in flags if f is not None]
    return sum(known) / len(known) if known else None


def _percentile(values: list[float], pct: float) -> float | None:
    """Percentil por vizinho mais próximo (suficiente para poucas amostras)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


def _ratio(numerators: list[float | None], denominators: list[float | None]) -> float | None:
    """Taxa agregada (soma/soma): não é distorcida por execuções curtas, como acertos de cache."""
    pairs = [(n, d) for n, d in zip(numerators, denominators) if n and d]
    total = sum(d for _, d in pairs)
    return sum(n for n, _ in pairs) / total if total else None


@dataclass
class ModelSummary:
    model: str
    reasoning: bool | None
    runs: int
    cold_start_s: float | None
    wall_mean_s: float | None
    wall_median_s: float | None
    wall_p95_s: float | None
    prompt_tokens_mean: float | None
    prompt_tokens_per_s: float | None
    output_tokens_mean: float | None
    output_tokens_per_s: float | None
    valid_rate: float | None
    status_accuracy: float | None
    assets_accuracy: float | None
    per_case: dict[str, dict] = field(default_factory=dict)


def summarize(
    model: str,
    results: list[RunResult],
    cold_start_s: float | None,
    reasoning: bool | None = None,
) -> ModelSummary:
    walls = [r.wall_s for r in results]
    per_case = {
        case_id: {
            "wall_mean_s": _mean([r.wall_s for r in runs]),
            "valid_rate": _rate([r.valid for r in runs]),
            "status_accuracy": _rate([r.status_ok for r in runs]),
            "assets_accuracy": _rate([r.assets_ok for r in runs]),
        }
        for case_id in dict.fromkeys(r.case_id for r in results)
        if (runs := [r for r in results if r.case_id == case_id])
    }
    return ModelSummary(
        model=model,
        reasoning=reasoning,
        runs=len(results),
        cold_start_s=cold_start_s,
        wall_mean_s=_mean(walls),
        wall_median_s=statistics.median(walls) if walls else None,
        wall_p95_s=_percentile(walls, 95),
        prompt_tokens_mean=_mean([r.prompt_tokens for r in results if r.prompt_tokens]),
        prompt_tokens_per_s=_ratio(
            [r.prompt_tokens for r in results], [r.prompt_eval_s for r in results]
        ),
        output_tokens_mean=_mean([r.output_tokens for r in results if r.output_tokens]),
        output_tokens_per_s=_ratio(
            [r.output_tokens for r in results], [r.eval_s for r in results]
        ),
        valid_rate=_rate([r.valid for r in results]),
        status_accuracy=_rate([r.status_ok for r in results]),
        assets_accuracy=_rate([r.assets_ok for r in results]),
        per_case=per_case,
    )


REASONING_LABELS = {None: "padrão", True: "ligado", False: "desligado"}

COMPARISON_COLUMNS = [
    ("Modelo", "model", "{}"),
    ("Thinking", "reasoning", None),
    ("Cold start (s)", "cold_start_s", "{:.1f}"),
    ("Latência média (s)", "wall_mean_s", "{:.1f}"),
    ("Mediana (s)", "wall_median_s", "{:.1f}"),
    ("p95 (s)", "wall_p95_s", "{:.1f}"),
    ("Tokens prompt", "prompt_tokens_mean", "{:.0f}"),
    ("Prompt tok/s", "prompt_tokens_per_s", "{:.1f}"),
    ("Tokens saída", "output_tokens_mean", "{:.0f}"),
    ("Saída tok/s", "output_tokens_per_s", "{:.1f}"),
    ("JSON válido", "valid_rate", "{:.0%}"),
    ("answer_status ok", "status_accuracy", "{:.0%}"),
    ("assets ok", "assets_accuracy", "{:.0%}"),
]


def comparison_table(summaries: list[dict]) -> str:
    """Tabela Markdown comparando resumos (dicts de ``ModelSummary``)."""
    header = "| " + " | ".join(title for title, _, _ in COMPARISON_COLUMNS) + " |"
    divider = "|" + "---|" * len(COMPARISON_COLUMNS)
    rows = [
        "| "
        + " | ".join(
            REASONING_LABELS[summary.get(key)]
            if fmt is None
            else "—"
            if summary.get(key) is None
            else fmt.format(summary[key])
            for _, key, fmt in COMPARISON_COLUMNS
        )
        + " |"
        for summary in summaries
    ]
    return "\n".join([header, divider, *rows])
