"""Chain de análise implementada de duas formas equivalentes.

Fluxo: CryptoQuestion -> prompt -> ChatOllama -> JsonOutputParser -> CryptoAnalysisRequest

- ``build_lcel_chain`` (padrão): composição declarativa com o operador ``|``.
- ``build_imperative_chain``: composição imperativa com o decorador ``@chain``.

Ver docs/composition-decision.md para a justificativa da escolha.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import Runnable, RunnableLambda, chain

from app.prompts import ANALYSIS_PROMPT, build_prompt_input
from app.schemas import CryptoAnalysisRequest, CryptoQuestion
from app.services.validation import validate_analysis


def build_json_chain(llm: BaseChatModel) -> Runnable[CryptoQuestion | dict, dict]:
    """Chain LCEL até o JSON bruto (sem validação final).

    Usada no streaming: o JsonOutputParser emite dicts parciais conforme os tokens chegam.
    """
    return (
        RunnableLambda(build_prompt_input, name="prepare_input")
        | ANALYSIS_PROMPT
        | llm
        | JsonOutputParser()
    )


def build_lcel_chain(llm: BaseChatModel) -> Runnable[CryptoQuestion | dict, CryptoAnalysisRequest]:
    """Composição padrão (LCEL)."""
    return build_json_chain(llm) | RunnableLambda(validate_analysis, name="validate_analysis")


def build_imperative_chain(
    llm: BaseChatModel,
) -> Runnable[CryptoQuestion | dict, CryptoAnalysisRequest]:
    """Mesma chain, escrita de forma imperativa com ``@chain``."""
    parser = JsonOutputParser()

    @chain
    def crypto_analysis_imperative(question: CryptoQuestion | dict) -> CryptoAnalysisRequest:
        prompt_input = build_prompt_input(question)
        messages = ANALYSIS_PROMPT.invoke(prompt_input)
        message = llm.invoke(messages)
        data = parser.invoke(message)
        return validate_analysis(data)

    return crypto_analysis_imperative


COMPOSITIONS = {
    "lcel": build_lcel_chain,
    "imperative": build_imperative_chain,
}
DEFAULT_COMPOSITION = "lcel"


def build_analysis_chain(
    llm: BaseChatModel, composition: str = DEFAULT_COMPOSITION
) -> Runnable[CryptoQuestion | dict, CryptoAnalysisRequest]:
    try:
        builder = COMPOSITIONS[composition]
    except KeyError:
        raise ValueError(
            f"Composição desconhecida: {composition!r}. Use uma de {sorted(COMPOSITIONS)}."
        ) from None
    return builder(llm)
