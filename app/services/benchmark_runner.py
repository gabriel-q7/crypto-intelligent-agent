"""Benchmark de desempenho e aderência de modelos do Ollama (ferramenta de manutenção).

Para cada modelo:
1. descarrega os modelos em memória (para medir o cold start de forma justa);
2. executa um caso de aquecimento e registra o tempo de carga (cold start);
3. executa cada caso de ``benchmarks/questions.json`` ``--runs`` vezes;
4. salva os resultados em ``benchmarks/results/<data>_<modelo>.json``.

Não é exposto pela API HTTP: uma rodada leva de 10 a 40 minutos em CPU.

Uso:
    python -m app.services.benchmark_runner                                  # modelo do OLLAMA_MODEL
    python -m app.services.benchmark_runner --models gemma4:e2b qwen2.5:7b --runs 3
    python -m app.services.benchmark_runner --models gemma4:e4b --pull       # baixa se faltar
    python -m app.services.benchmark_runner --models gemma4:e4b --reasoning off
    python -m app.services.benchmark_runner --compare                        # compara resultados salvos
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

from app.config import get_settings, project_path
from app.services.benchmark import (
    BenchmarkCase,
    comparison_table,
    load_cases,
    run_case,
    summarize,
)
from app.services.llm import build_llm

DEFAULT_CASES = project_path("benchmarks/questions.json")
DEFAULT_OUTPUT = project_path("benchmarks/results")

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
    parser.add_argument(
        "--compare", nargs="*", type=Path, metavar="ARQUIVO",
        help="Só compara resultados salvos (sem arquivos: o mais recente de cada modelo + thinking).",
    )
    return parser.parse_args()


def latest_per_model(directory: Path) -> list[Path]:
    """Resultado mais recente de cada combinação modelo + thinking."""
    latest: dict[tuple, Path] = {}
    # Nomes começam com o timestamp: ordem alfabética = ordem cronológica.
    for path in sorted(directory.glob("*.json")):
        meta = json.loads(path.read_text())["meta"]
        latest[(meta["model"], meta.get("reasoning"))] = path
    return list(latest.values())


def compare(files: list[Path], output_dir: Path) -> None:
    files = files or latest_per_model(output_dir)
    if not files:
        sys.exit(f"Nenhum resultado em {output_dir}.")
    reports = [json.loads(path.read_text()) for path in files]
    print(comparison_table([report["summary"] for report in reports]))
    print("\nFontes: " + ", ".join(str(path) for path in files))


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
    if args.compare is not None:
        compare(args.compare, args.output_dir)
        return
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
