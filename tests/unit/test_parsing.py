import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import JsonOutputParser

from app.errors import AnalysisOutputError
from app.services.analysis import translate_error
from app.services.validation import validate_analysis


def test_json_parser_handles_markdown_fence():
    raw = '```json\n{"intent": "other", "confidence": 0.5}\n```'
    assert JsonOutputParser().invoke(AIMessage(content=raw)) == {
        "intent": "other",
        "confidence": 0.5,
    }


def test_json_parser_rejects_non_json():
    with pytest.raises(OutputParserException):
        JsonOutputParser().invoke(AIMessage(content="BTC está em alta hoje."))


def test_validate_analysis_ok(valid_output):
    assert validate_analysis(valid_output).assets == ["BTC", "ETH"]


@pytest.mark.parametrize("data", [["a"], "texto", None, 42])
def test_validate_analysis_rejects_non_object(data):
    with pytest.raises(AnalysisOutputError) as info:
        validate_analysis(data)
    assert info.value.raw_output == data


def test_validate_analysis_wraps_validation_error(valid_output):
    valid_output["confidence"] = 7
    with pytest.raises(AnalysisOutputError, match="CryptoAnalysisRequest") as info:
        validate_analysis(valid_output)
    assert info.value.raw_output == valid_output


def test_translate_parser_exception():
    exc = OutputParserException("bad", llm_output="not json")
    translated = translate_error(exc)
    assert isinstance(translated, AnalysisOutputError)
    assert translated.raw_output == "not json"


def test_translate_keeps_unknown_errors():
    exc = KeyError("x")
    assert translate_error(exc) is exc
