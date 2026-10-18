"""Benchmark de desempenho e aderência de um ou mais modelos do Ollama.

Para cada modelo:
1. descarrega os modelos em memória (para medir o cold start de forma justa);
2. executa um caso de aquecimento e registra o tempo de carga (cold start);
3. executa cada caso de ``benchmarks/questions.json`` ``--runs`` vezes;
4. salva os resultados em ``benchmarks/results/<data>_<modelo>.json``.

Uso:
    python -m scripts.benchmark                                  # modelo do OLLAMA_MODEL
    python -m scripts.benchmark --models gemma4:e2b qwen2.5:7b --runs 3
    python -m scripts.benchmark --models gemma4:e4b --pull       # baixa se faltar
    python -m scripts.benchmark --models gemma4:e4b --reasoning off
"""

import argparse
import json
import os
import platform
import sys
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import httpx
from ollama import Client, ResponseError

from app.config import get_settings, parse_optional_bool
from app.services.benchmark import (
    BenchmarkCase,
    comparison_table,
    load_cases,
    run_case,
    summarize,
)
from app.services.llm import build_llm

DEFAULT_CASES = Path("benchmarks/questions.json")
DEFAULT_OUTPUT = Path("benchmarks/results")

# Aquecimento com pergunta fora do conjunto: se fosse um dos casos, a execução seguinte
# reaproveitaria o prompt inteiro do cache do Ollama e mediria quase zero de leitura.
WARMUP_CASE = BenchmarkCase(id="aquecimento", question="O que é uma blockchain?")


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Benchmark de modelos do Ollama")
    parser.add_argument("--models", nargs="+", default=[settings.ollama_model])
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Repetições por caso (padrão: 1; com temperatura 0 a variação entre execuções é < 2%%).",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pull", action="store_true", help="Baixa modelos ausentes.")
    parser.add_argument(
        "--reasoning",
        choices=["default", "on", "off"],
        help="Thinking do modelo (padrão: OLLAMA_REASONING).",
    )
    return parser.parse_args()


def ensure_model(client: Client, model: str, pull: bool) -> None:
    installed = {m.model for m in client.list().models}
    if model in installed:
        return
    if not pull:
        sys.exit(f"[erro] modelo {model!r} não está no Ollama. Use --pull ou baixe antes.")
    print(f"Baixando {model}...", flush=True)
    # Download de GBs passa do timeout de inferência: cliente próprio, sem timeout.
    Client(host=get_settings().ollama_base_url, timeout=None).pull(model)


def unload_all(client: Client) -> None:
    """keep_alive=0 descarrega o modelo da memória."""
    for running in client.ps().models:
        client.generate(model=running.model, prompt="", keep_alive=0)


def model_info(client: Client, model: str) -> dict:
    details = client.show(model).details
    return {
        "family": details.family,
        "parameter_size": details.parameter_size,
        "quantization_level": details.quantization_level,
    }


def benchmark_model(
    client: Client, model: str, cases: list[BenchmarkCase], runs: int, reasoning: bool | None
) -> dict:
    settings = replace(get_settings(), ollama_model=model, ollama_reasoning=reasoning)
    llm = build_llm(settings)

    unload_all(client)
    print(f"\n=== {model} (thinking={reasoning}) — aquecimento (cold start) ===", flush=True)
    warmup = run_case(llm, WARMUP_CASE, run=0)
    print(f"carga {warmup.load_s or 0:.1f}s, total {warmup.wall_s:.1f}s", flush=True)

    results = []
    for run in range(1, runs + 1):
        for case in cases:
            result = run_case(llm, case, run)
            results.append(result)
            status = "ok" if result.valid else "INVÁLIDO"
            print(
                f"[{run}/{runs}] {case.id:<22} {result.wall_s:6.1f}s "
                f"{result.tokens_per_s or 0:5.1f} tok/s  {status}",
                flush=True,
            )

    unload_all(client)
    summary = summarize(model, results, cold_start_s=warmup.load_s, reasoning=reasoning)
    return {
        "meta": {
            "model": model,
            "model_info": model_info(client, model),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "ollama_version": httpx.get(f"{settings.ollama_base_url}/api/version").json()["version"],
            "temperature": settings.ollama_temperature,
            "reasoning": reasoning,
            "runs": runs,
            "cases": [c.id for c in cases],
            "host": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
            "warmup": warmup.to_dict(),
        },
        "summary": asdict(summary),
        "results": [r.to_dict() for r in results],
    }


def save(report: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["meta"]["timestamp"].replace(":", "").replace("-", "")
    name = report["meta"]["model"].replace(":", "-").replace("/", "_")
    if report["meta"]["reasoning"] is not None:
        name += "_thinking-" + ("on" if report["meta"]["reasoning"] else "off")
    path = output_dir / f"{stamp}_{name}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return path


def main() -> None:
    args = parse_args()
    settings = get_settings()
    client = Client(host=settings.ollama_base_url, timeout=settings.ollama_request_timeout)
    cases = load_cases(args.cases)
    reasoning = (
        settings.ollama_reasoning
        if args.reasoning is None
        else {"default": None, "on": True, "off": False}[args.reasoning]
    )

    summaries = []
    for model in args.models:
        try:
            ensure_model(client, model, args.pull)
            report = benchmark_model(client, model, cases, args.runs, reasoning)
        except (ResponseError, httpx.HTTPError, ConnectionError) as exc:
            print(f"[erro] {model}: {exc}", file=sys.stderr)
            continue
        print(f"Resultados salvos em {save(report, args.output_dir)}")
        summaries.append(report["summary"])

    if summaries:
        print("\n" + comparison_table(summaries))


if __name__ == "__main__":
    main()
