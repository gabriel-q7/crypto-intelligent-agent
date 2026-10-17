import pytest
from pydantic import ValidationError

from app.schemas import AnalysisIntent, CryptoAnalysisRequest, CryptoQuestion


class TestCryptoQuestion:
    def test_normalizes_assets_and_strips_question(self):
        q = CryptoQuestion(question="  preço do btc?  ", assets=[" btc", "BTC", "eth", ""])
        assert q.question == "preço do btc?"
        assert q.assets == ["BTC", "ETH"]
        assert q.period is None and q.context is None

    @pytest.mark.parametrize("text", ["", "  ", "ab"])
    def test_rejects_short_question(self, text):
        with pytest.raises(ValidationError):
            CryptoQuestion(question=text)


class TestCryptoAnalysisRequest:
    def test_valid_payload(self, valid_output):
        result = CryptoAnalysisRequest.model_validate(valid_output)
        assert result.intent is AnalysisIntent.COMPARISON
        assert result.assets == ["BTC", "ETH"]
        assert result.confidence == 0.8

    def test_intent_is_case_insensitive(self, valid_output):
        valid_output["intent"] = " Comparison "
        assert CryptoAnalysisRequest.model_validate(valid_output).intent is AnalysisIntent.COMPARISON

    def test_optional_fields_default(self):
        result = CryptoAnalysisRequest.model_validate(
            {"intent": "other", "assets": [], "investigation_steps": ["x"], "confidence": 0.1}
        )
        assert result.period is None
        assert result.requested_metrics == result.missing_information == result.assumptions == []

    @pytest.mark.parametrize(
        "field, value",
        [
            ("intent", "buy_signal"),
            ("confidence", 1.5),
            ("confidence", -0.1),
            ("investigation_steps", []),
            ("assets", "BTC"),
        ],
    )
    def test_rejects_invalid_values(self, valid_output, field, value):
        valid_output[field] = value
        with pytest.raises(ValidationError):
            CryptoAnalysisRequest.model_validate(valid_output)

    @pytest.mark.parametrize("field", ["intent", "assets", "investigation_steps", "confidence"])
    def test_rejects_missing_required(self, valid_output, field):
        del valid_output[field]
        with pytest.raises(ValidationError):
            CryptoAnalysisRequest.model_validate(valid_output)
