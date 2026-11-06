"""Avaliação da Fase 5 (manutenção, como o benchmark): dataset → alvo real → avaliadores → resultados.

Uso (dentro do container da aplicação, com Ollama e a API de dados acessíveis):
    python -m app.evaluation.runner --upload                     # cria/atualiza o dataset no LangSmith
    python -m app.evaluation.runner --run --prefix fase5-inicial # experimento no LangSmith
    python -m app.evaluation.runner --local --prefix teste       # mesmo fluxo sem LangSmith
    ... --ids api-01 rag-02                                      # só alguns exemplos

O dataset vive em ``evaluation/phase5_dataset.json`` (fonte versionada). O upload é idempotente:
o id de cada exemplo no LangSmith é um UUID5 derivado do id local. Resultados vão para
``docs/evidence/phase-5/eval-<prefixo>.json`` com o link do experimento quando houver.
"""

import argparse
import json
import logging
import statistics
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from app.config import PROJECT_ROOT
from app.evaluation.evaluators import TRAJECTORY_KEYS, all_evaluators
from app.evaluation.target import EvaluationTarget

logger = logging.getLogger(__name__)

DATASET_FILE = PROJECT_ROOT / "evaluation" / "phase5_dataset.json"
RESULTS_DIR = PROJECT_ROOT / "docs" / "evidence" / "phase-5"
NAMESPACE = uuid.UUID("6f1b8a9e-5c2d-4f3a-9b7e-0d4c2a1e8f55")


def load_dataset(ids: list[str] | None = None) -> dict:
    data = json.loads(DATASET_FILE.read_text())
    if ids:
        data["examples"] = [e for e in data["examples"] if e["id"] in ids]
    return data


def dataset_name(data: dict) -> str:
    return f"{data['name']}-v{data['version']}"


def example_uuid(example_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, example_id))


def upload(data: dict) -> str:
    from langsmith import Client

    client = Client()
    name = dataset_name(data)
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(name, description=data["description"])
    existing = {str(e.id) for e in client.list_examples(dataset_id=dataset.id)}
    new = [e for e in data["examples"] if example_uuid(e["id"]) not in existing]
    if new:
        client.create_examples(
            dataset_id=dataset.id,
            ids=[example_uuid(e["id"]) for e in new],
            inputs=[e["inputs"] for e in new],
            outputs=[e["outputs"] for e in new],
            metadata=[{"example_id": e["id"], "category": e["category"]} for e in new],
        )
    print(f"dataset {name}: {len(existing)} existentes, {len(new)} enviados")
    return name


def _summarize(rows: list[dict]) -> dict:
    by_key: dict[str, list[float]] = defaultdict(list)
    by_cat: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        for key, ev in row["scores"].items():
            if ev["score"] is None:
                continue
            by_key[key].append(float(ev["score"]))
            by_cat[row["category"]][key].append(float(ev["score"]))
    mean = lambda xs: round(statistics.fmean(xs), 3) if xs else None  # noqa: E731
    overall = {k: mean(v) for k, v in sorted(by_key.items())}
    trajectory = [s for k, v in by_key.items() if k in TRAJECTORY_KEYS for s in v]
    return {
        "overall": overall,
        "trajectory_mean": mean(trajectory),
        "answer_mean": mean([s for k, v in by_key.items() if k not in TRAJECTORY_KEYS for s in v]),
        "by_category": {c: {k: mean(v) for k, v in sorted(d.items())} for c, d in sorted(by_cat.items())},
        "n_examples": len(rows),
    }


def _save(prefix: str, payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"eval-{prefix}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return path


def run_local(data: dict, target: EvaluationTarget, evaluators, prefix: str) -> dict:
    rows = []
    for example in data["examples"]:
        started = time.perf_counter()
        try:
            outputs = target(example["inputs"])
            error = None
        except Exception as exc:  # noqa: BLE001 - erro do alvo vira linha com erro, não derruba a avaliação
            logger.exception("example %s failed", example["id"])
            outputs, error = {"answer": "", "tools": [], "categories": [], "steps": [], "observations": [], "verification": {}}, repr(exc)
        scores = {}
        for evaluator in evaluators:
            result = evaluator(example["inputs"], outputs, example["outputs"])
            scores[result["key"]] = {"score": result["score"], "comment": result["comment"]}
        rows.append({"id": example["id"], "category": example["category"], "inputs": example["inputs"],
                     "outputs": {k: v for k, v in outputs.items() if k != "observations"}, "error": error,
                     "scores": scores, "elapsed_s": round(time.perf_counter() - started, 1)})
        print(f"{example['id']}: {json.dumps({k: v['score'] for k, v in scores.items()})}", flush=True)
    return {"rows": rows, "summary": _summarize(rows)}


def run_langsmith(data: dict, target: EvaluationTarget, evaluators, prefix: str, metadata: dict) -> dict:
    from langsmith import evaluate

    name = upload(data)
    ids = {example_uuid(e["id"]) for e in data["examples"]}
    from langsmith import Client

    examples = [e for e in Client().list_examples(dataset_name=name) if str(e.id) in ids]
    results = evaluate(
        target, data=examples, evaluators=evaluators, experiment_prefix=prefix,
        max_concurrency=1, metadata=metadata, description=f"Fase 5 — {prefix}",
    )
    rows = []
    by_uuid = {example_uuid(e["id"]): e for e in data["examples"]}
    for item in results:
        example = by_uuid.get(str(item["example"].id), {})
        run = item["run"]
        scores = {r.key: {"score": r.score, "comment": r.comment} for r in item["evaluation_results"]["results"]}
        outputs = dict(run.outputs or {})
        outputs.pop("observations", None)
        rows.append({"id": example.get("id"), "category": example.get("category"), "inputs": example.get("inputs"),
                     "outputs": outputs, "error": run.error, "scores": scores, "run_id": str(run.id),
                     "trace_id": str(run.trace_id)})
    url = None
    try:
        url = results._manager._experiment.url  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return {"rows": rows, "summary": _summarize(rows), "experiment_name": results.experiment_name, "experiment_url": url}


def main() -> None:
    parser = argparse.ArgumentParser(description="Avaliação da Fase 5")
    parser.add_argument("--upload", action="store_true", help="cria/atualiza o dataset no LangSmith")
    parser.add_argument("--run", action="store_true", help="experimento no LangSmith")
    parser.add_argument("--local", action="store_true", help="mesmo fluxo sem LangSmith")
    parser.add_argument("--prefix", default="fase5")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--no-judge", action="store_true", help="sem LLM-juiz (só avaliadores determinísticos)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    data = load_dataset(args.ids)
    if args.upload and not (args.run or args.local):
        upload(data)
        return

    from app.api.container import AppContainer

    container = AppContainer(background_maintenance=False)
    target = EvaluationTarget(container)
    evaluators = all_evaluators(None if args.no_judge else container.llm)
    metadata = {"model": container.settings.ollama_model, "dataset_version": data["version"],
                "judge": None if args.no_judge else container.settings.ollama_model,
                "started_at": datetime.now(UTC).isoformat(timespec="seconds")}
    try:
        if args.run:
            payload = run_langsmith(data, target, evaluators, args.prefix, metadata)
        else:
            payload = run_local(data, target, evaluators, args.prefix)
    finally:
        container.close()
    payload.update({"prefix": args.prefix, "metadata": metadata, "dataset": dataset_name(data)})
    path = _save(args.prefix, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"resultados: {path}" + (f"\nexperimento: {payload.get('experiment_url')}" if payload.get("experiment_url") else ""))


if __name__ == "__main__":
    main()
