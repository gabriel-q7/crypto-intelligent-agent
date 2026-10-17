import json
import sys

from app.errors import AnalysisError
from app.schemas import CryptoAnalysisRequest


def print_analysis(result: CryptoAnalysisRequest) -> None:
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


def fail(exc: AnalysisError) -> None:
    print(f"[erro] {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)
