"""Erros de domínio expostos pela aplicação."""


class AnalysisError(Exception):
    """Base para falhas ao produzir uma análise."""


class AnalysisOutputError(AnalysisError):
    """O modelo respondeu, mas a saída não é JSON válido ou não respeita o schema."""

    def __init__(self, message: str, raw_output: object = None) -> None:
        super().__init__(message)
        self.raw_output = raw_output


class LLMUnavailableError(AnalysisError):
    """Não foi possível comunicar com o Ollama."""
