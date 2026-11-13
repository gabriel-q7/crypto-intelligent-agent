"""Exporta do LangSmith os turnos reais do agente integrado (Fase 5) para análise no relatório.

Lê o projeto de rastreamento (``LANGSMITH_PROJECT``) e grava, em ``docs/evidence/langsmith/``:

- ``turns.jsonl``: um turno por linha — pergunta, status, verificação, correção, ferramentas,
  latência por etapa (pré-consulta, agente, verificação, NLI, coerência, correção), chamadas ao LLM
  e tokens, erros do verificador e a resposta;
- ``summary.json``: agregados (latência, participação de cada etapa, tokens, status) e o inventário
  do projeto (traces raiz por nome, separando os de testes).

"Turno real" = trace ``integrated.turn`` com ao menos uma chamada ``llm`` ao ChatOllama. Os testes
offline usam dublês (``FakeListChatModel``) e, quando o ``.env`` liga o tracing, também geram traces;
eles ficam fora da análise e só entram na contagem do inventário.

Uso (no container, que já tem o ``.env`` e o SDK ``langsmith``):
    docker compose run --rm --no-deps -v ./scripts:/app/scripts -v ./docs:/app/docs \\
        app python scripts/langsmith_export.py
"""

import asyncio
import json
import os
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from langsmith import Client

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "evidence" / "langsmith"
SELECTS = ["id", "name", "run_type", "start_time", "status", "latency_seconds", "metadata", "tags", "inputs",
           "outputs", "error_preview", "total_tokens", "prompt_tokens", "completion_tokens", "trace_id",
           "parent_run_ids"]
STAGES = {
    "integrated.prefetch": "prefetch_s",
    "integrated.agent_run": "agent_s",
    "integrated.correction": "correction_s",
    "verification.claims_nli": "nli_s",
    "verification.logic": "coherence_s",
}
TEST_DOUBLES = {"FakeListChatModel", "RoutingFakeChat", "GenericFakeChatModel"}


async def query(client: Client, project_id: str, **kwargs) -> list:
    return [run async for run in client.runs.query(project_ids=[project_id], selects=SELECTS, page_size=100, **kwargs)]


def parent_of(run) -> str | None:
    parents = run.parent_run_ids or []
    return str(parents[-1]) if parents else None


def summarize_turn(root, runs: list) -> dict | None:
    by_id = {str(r.id): r for r in runs}
    llm = [r for r in runs if r.run_type == "llm"]
    if not any(r.name == "ChatOllama" for r in llm):
        return None  # turno de teste (dublê de LLM)

    def ancestors(run) -> set[str]:
        return {by_id[p].name for p in map(str, run.parent_run_ids or []) if p in by_id}

    stages: dict[str, float] = {}
    for run in runs:
        key = STAGES.get(run.name)
        if key:
            stages[key] = round(stages.get(key, 0.0) + (run.latency_seconds or 0.0), 2)
    stages["verification_s"] = round(sum(r.latency_seconds or 0 for r in runs if r.name == "verification.verify"), 2)

    # A ferramenta estruturada (Fase 5) delega à de entrada única: as duas aparecem; conta só a externa.
    tools = [r for r in runs if r.run_type == "tool" and (by_id.get(parent_of(r)) is None
                                                         or by_id[parent_of(r)].run_type != "tool")]
    agent_llm = [r for r in llm if {"integrated.agent_run", "integrated.correction"} & ancestors(r)]
    verify_llm = [r for r in llm if "verification.verify" in ancestors(r)]
    out = root.outputs or {}
    verification = out.get("verification") or {}
    return {
        "start_time": root.start_time.isoformat(),
        "question": (root.inputs or {}).get("message"),
        "latency_s": round(root.latency_seconds or 0, 2),
        "status": out.get("status"),
        "verification": verification.get("status"),
        "correction_attempted": verification.get("correction_attempted"),
        "claims": Counter(c.get("label") for c in verification.get("claims") or []),
        "verifier_errors": verification.get("verifier_errors") or [],
        "tools": [r.name for r in sorted(tools, key=lambda r: r.start_time)],
        "tool_s": round(sum(r.latency_seconds or 0 for r in tools), 2),
        **stages,
        "llm_calls": len(llm),
        "agent_llm_calls": len(agent_llm),
        "verification_llm_calls": len(verify_llm),
        "tokens": sum(r.total_tokens or 0 for r in llm),
        "agent_tokens": sum(r.total_tokens or 0 for r in agent_llm),
        "verification_tokens": sum(r.total_tokens or 0 for r in verify_llm),
        "model": (root.metadata or {}).get("model"),
        "answer": out.get("answer"),
    }


def stats(values: list[float]) -> dict:
    values = sorted(values)
    if not values:
        return {}
    return {"n": len(values), "min": values[0], "median": round(statistics.median(values), 2),
            "mean": round(statistics.fmean(values), 2), "max": values[-1]}


async def main() -> None:
    client = Client()
    project = client.read_project(project_name=os.environ.get("LANGSMITH_PROJECT", "default"), include_stats=True)
    project_id = str(project.id)
    roots = await query(client, project_id, is_root=True)
    turn_roots = [r for r in roots if r.name == "integrated.turn"]

    turns = []
    for root in sorted(turn_roots, key=lambda r: r.start_time):
        row = summarize_turn(root, await query(client, project_id, trace_id=str(root.trace_id)))
        if row:
            turns.append(row)

    real_ids = {t["start_time"] for t in turns}
    test_roots = [r for r in roots if r.name in TEST_DOUBLES
                  or (r.name == "integrated.turn" and r.start_time.isoformat() not in real_ids)]
    share = [round(t["verification_s"] / t["latency_s"], 3) for t in turns if t["latency_s"]]
    summary = {
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": project.name,
        "project_stats": {"runs": project.run_count, "error_rate": project.error_rate, "tokens": project.total_tokens,
                          "first_run": project.start_time.isoformat() if project.start_time else None,
                          "last_run": project.last_run_start_time.isoformat() if project.last_run_start_time else None},
        "root_traces": len(roots),
        "root_traces_by_name": Counter(r.name for r in roots).most_common(),
        "integrated_turns": len(turn_roots),
        "real_turns": len(turns),
        "test_turns": len(turn_roots) - len(turns),
        "test_double_roots": len(test_roots),
        "latency_s": stats([t["latency_s"] for t in turns]),
        "agent_s": stats([t.get("agent_s", 0) for t in turns]),
        "verification_s": stats([t["verification_s"] for t in turns]),
        "nli_s": stats([t.get("nli_s", 0) for t in turns]),
        "coherence_s": stats([t.get("coherence_s", 0) for t in turns]),
        "prefetch_s": stats([t["prefetch_s"] for t in turns if "prefetch_s" in t]),
        "correction_s": stats([t["correction_s"] for t in turns if "correction_s" in t]),
        "tool_s": stats([t["tool_s"] for t in turns]),
        "verification_share": stats(share),
        "llm_calls": stats([t["llm_calls"] for t in turns]),
        "tokens": stats([t["tokens"] for t in turns]),
        "agent_tokens": stats([t["agent_tokens"] for t in turns]),
        "verification_tokens": stats([t["verification_tokens"] for t in turns]),
        "status": Counter(t["status"] for t in turns),
        "verification": Counter(t["verification"] for t in turns),
        "corrections": sum(bool(t["correction_attempted"]) for t in turns),
        "turns_with_verifier_errors": sum(bool(t["verifier_errors"]) for t in turns),
        "tool_calls": Counter(tool for t in turns for tool in t["tools"]),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "turns.jsonl").open("w", encoding="utf-8") as fh:
        for t in turns:
            fh.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
                                          encoding="utf-8")
    print(f"{len(turns)} turnos reais de {len(turn_roots)} integrated.turn; {len(roots)} traces raiz → {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
