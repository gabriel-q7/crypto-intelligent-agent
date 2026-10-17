from pydantic import ValidationError

from app.errors import AnalysisOutputError
from app.schemas import CryptoAnalysisRequest


def validate_analysis(data: object) -> CryptoAnalysisRequest:
    """Valida a saída do JsonOutputParser contra o schema final."""
    if not isinstance(data, dict):
        raise AnalysisOutputError(
            f"Saída do modelo deveria ser um objeto JSON, recebido {type(data).__name__}.",
            raw_output=data,
        )
    try:
        return CryptoAnalysisRequest.model_validate(data)
    except ValidationError as exc:
        raise AnalysisOutputError(
            f"Saída do modelo não respeita o schema CryptoAnalysisRequest: {exc}",
            raw_output=data,
        ) from exc
