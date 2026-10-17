"""Demonstra a interface Runnable: invoke (LCEL e @chain), batch e stream.

Uso:
    python -m scripts.demo_runnables [invoke|batch|stream|all]
"""

import argparse
import json

from app.chains import build_imperative_chain, build_lcel_chain
from app.errors import AnalysisError
from app.schemas import CryptoQuestion
from app.services.analysis import run_analysis, run_batch, stream_analysis
from app.services.llm import build_llm
from scripts._common import fail, print_analysis

SAMPLE = CryptoQuestion(
    question="Vale a pena comparar o desempenho de BTC e ETH no último trimestre?",
    assets=["btc", "eth"],
    period="último trimestre",
)

BATCH = [
    CryptoQuestion(question="Qual o risco de manter SOL em carteira?", assets=["SOL"]),
    CryptoQuestion(question="Como evoluiu o TVL do Aave neste ano?", period="2026"),
    CryptoQuestion(question="O que é staking de ETH?"),
]


def demo_invoke(llm) -> None:
    for name, builder in (("LCEL (|)", build_lcel_chain), ("@chain", build_imperative_chain)):
        print(f"\n=== invoke — {name} ===")
        print_analysis(run_analysis(builder(llm), SAMPLE))


def demo_batch(llm) -> None:
    print("\n=== batch — LCEL ===")
    for question, result in zip(BATCH, run_batch(build_lcel_chain(llm), BATCH)):
        print(f"\n> {question.question}")
        if isinstance(result, AnalysisError):
            print(f"[erro] {type(result).__name__}: {result}")
        else:
            print_analysis(result)


def demo_stream(llm) -> None:
    print("\n=== stream — JSON parcial (não validado) ===")
    count = 0

    def on_partial(partial: dict) -> None:
        nonlocal count
        count += 1
        # Mostra só as chaves já presentes para não poluir a saída.
        print(f"  parcial #{count}: chaves={list(partial)}")

    result = stream_analysis(llm, SAMPLE, on_partial=on_partial)
    print(f"\n=== stream — objeto final validado ({count} parciais) ===")
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


DEMOS = {"invoke": demo_invoke, "batch": demo_batch, "stream": demo_stream}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("demo", nargs="?", choices=[*DEMOS, "all"], default="all")
    args = parser.parse_args()

    llm = build_llm()
    selected = DEMOS.values() if args.demo == "all" else [DEMOS[args.demo]]
    try:
        for demo in selected:
            demo(llm)
    except AnalysisError as exc:
        fail(exc)


if __name__ == "__main__":
    main()
