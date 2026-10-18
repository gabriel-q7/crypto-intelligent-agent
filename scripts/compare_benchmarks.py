"""Compara resultados de benchmark salvos em ``benchmarks/results``.

Sem argumentos, usa o resultado mais recente de cada modelo (e configuração de thinking).

Uso:
    python -m scripts.compare_benchmarks
    python -m scripts.compare_benchmarks benchmarks/results/a.json benchmarks/results/b.json
"""

import argparse
import json
from pathlib import Path

from app.services.benchmark import comparison_table

RESULTS_DIR = Path("benchmarks/results")


def latest_per_model(directory: Path) -> list[Path]:
    """Resultado mais recente de cada combinação modelo + thinking."""
    latest: dict[tuple, Path] = {}
    # Nomes começam com o timestamp: ordem alfabética = ordem cronológica.
    for path in sorted(directory.glob("*.json")):
        meta = json.loads(path.read_text())["meta"]
        latest[(meta["model"], meta.get("reasoning"))] = path
    return list(latest.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args()

    files = args.files or latest_per_model(RESULTS_DIR)
    if not files:
        raise SystemExit(f"Nenhum resultado em {RESULTS_DIR}. Rode python -m scripts.benchmark.")

    reports = [json.loads(path.read_text()) for path in files]
    print(comparison_table([report["summary"] for report in reports]))
    print("\nFontes: " + ", ".join(str(path) for path in files))


if __name__ == "__main__":
    main()
