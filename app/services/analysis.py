"""Fachada de uso da chain: invoke, batch e stream com tratamento de erros."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import httpx
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from ollama import ResponseError

from app.chains import build_json_chain
from app.errors import AnalysisError, AnalysisOutputError, LLMUnavailableError
from app.schemas import CryptoAnalysisRequest, CryptoQuestion
from app.services.validation import validate_analysis

Question = CryptoQuestion | dict


def translate_error(exc: BaseException) -> AnalysisError | BaseException:
    """Converte exceções de infraestrutura/parsing em erros de domínio."""
    if isinstance(exc, AnalysisError):
        return exc
    if isinstance(exc, OutputParserException):
        return AnalysisOutputError(
            f"O modelo não retornou JSON válido: {exc}", raw_output=exc.llm_output
        )
    if isinstance(exc, (ConnectionError, httpx.HTTPError)):
        return LLMUnavailableError(f"Falha ao conectar ao Ollama: {exc}")
    if isinstance(exc, ResponseError):
        return LLMUnavailableError(f"Ollama retornou erro: {exc}")
    return exc


@contextmanager
def _domain_errors() -> Iterator[None]:
    try:
        yield
    except Exception as exc:
        translated = translate_error(exc)
        if translated is exc:
            raise
        raise translated from exc


def run_analysis(chain: Runnable, question: Question) -> CryptoAnalysisRequest:
    """``invoke``: uma pergunta -> uma análise validada."""
    with _domain_errors():
        return chain.invoke(question)


def run_batch(
    chain: Runnable, questions: list[Question], max_concurrency: int = 2
) -> list[CryptoAnalysisRequest | AnalysisError]:
    """``batch``: várias perguntas em paralelo.

    Uma falha não derruba o lote: o item com erro é retornado como ``AnalysisError``.
    """
    results = chain.batch(
        questions, config={"max_concurrency": max_concurrency}, return_exceptions=True
    )
    output: list[CryptoAnalysisRequest | AnalysisError] = []
    for result in results:
        if isinstance(result, BaseException):
            translated = translate_error(result)
            if not isinstance(translated, AnalysisError):
                raise result
            output.append(translated)
        else:
            output.append(result)
    return output


def stream_analysis(
    llm: BaseChatModel,
    question: Question,
    on_partial: Callable[[dict], None] | None = None,
) -> CryptoAnalysisRequest:
    """``stream``: emite objetos JSON parciais e valida apenas o objeto final.

    Os parciais NÃO são validados (campos obrigatórios ainda podem estar ausentes);
    somente o último objeto acumulado passa pelo schema Pydantic.
    """
    last: dict | None = None
    with _domain_errors():
        for partial in build_json_chain(llm).stream(question):
            last = partial
            if on_partial is not None:
                on_partial(partial)
        if last is None:
            raise AnalysisOutputError("O streaming terminou sem produzir JSON.")
        return validate_analysis(last)
