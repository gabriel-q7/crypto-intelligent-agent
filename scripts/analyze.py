"""CLI: interpreta uma pergunta sobre criptomoedas e imprime o JSON validado.

Exemplo:
    python -m scripts.analyze "Compare BTC e ETH" --assets BTC ETH --period "últimos 30 dias"
"""

import argparse
from dataclasses import replace

from app.chains import COMPOSITIONS, DEFAULT_COMPOSITION, build_analysis_chain
from app.config import get_settings
from app.errors import AnalysisError
from app.schemas import CryptoQuestion
from app.services.analysis import run_analysis
from app.services.llm import build_llm
from scripts._common import fail, print_analysis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crypto Intelligence Agent — etapa 1")
    parser.add_argument("question", help="Pergunta em linguagem natural.")
    parser.add_argument("--assets", nargs="*", default=[], help="Tickers, ex.: BTC ETH.")
    parser.add_argument("--period", help="Período, ex.: 'últimos 30 dias'.")
    parser.add_argument("--context", help="Contexto adicional.")
    parser.add_argument(
        "--composition",
        choices=sorted(COMPOSITIONS),
        default=DEFAULT_COMPOSITION,
        help="Forma de composição da chain (padrão: lcel).",
    )
    parser.add_argument("--model", help="Sobrescreve OLLAMA_MODEL nesta execução.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    question = CryptoQuestion(
        question=args.question, assets=args.assets, period=args.period, context=args.context
    )
    settings = get_settings()
    if args.model:
        settings = replace(settings, ollama_model=args.model)
    chain = build_analysis_chain(build_llm(settings), args.composition)
    try:
        print_analysis(run_analysis(chain, question))
    except AnalysisError as exc:
        fail(exc)


if __name__ == "__main__":
    main()
