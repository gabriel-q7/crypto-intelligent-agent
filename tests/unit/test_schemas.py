import pytest
from pydantic import ValidationError

from app.schemas import AnalysisIntent, AnswerStatus, CryptoAnalysisRequest, CryptoQuestion
from app.schemas.analysis import MARKET_DATA_NOTICE


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
            {
                "intent": "other",
                "assets": [],
                "answer_status": "answered",
                "answer": "Resposta.",
                "confidence": 0.1,
            }
        )
        assert result.period is None
        assert result.requested_metrics == result.missing_information == result.assumptions == []
        assert result.investigation_steps == []

    def test_answered_question_may_have_no_steps(self, valid_output):
        valid_output.update(answer_status="Answered", investigation_steps=[])
        result = CryptoAnalysisRequest.model_validate(valid_output)
        assert result.answer_status is AnswerStatus.ANSWERED

    @pytest.mark.parametrize("status", ["requires_market_data", "needs_clarification"])
    def test_unanswered_question_requires_steps(self, valid_output, status):
        valid_output.update(answer_status=status, investigation_steps=[], requested_metrics=[])
        with pytest.raises(ValidationError, match="investigation_steps"):
            CryptoAnalysisRequest.model_validate(valid_output)

    def test_missing_steps_are_derived_from_metrics(self, valid_output):
        valid_output.update(
            answer_status="requires_market_data",
            investigation_steps=[],
            requested_metrics=["price"],
            assets=["btc"],
        )
        result = CryptoAnalysisRequest.model_validate(valid_output)
        assert result.investigation_steps == ["Obter price de BTC"]
        assert "derivada automaticamente" in result.assumptions[-1]

    @pytest.mark.parametrize(
        "answer",
        [
            "O BTC custa US$ 65.000 hoje.",
            "ETH está cotado a $3200.",
            "Vale cerca de 350 mil reais.",
            "Tem TVL de 2 bilhões de dólares.",
            "Negocia perto de 2.500 USD.",
            "O staking rende cerca de 4% ao ano.",
            "Subiu 3,5 % na semana.",
        ],
    )
    def test_answer_market_figures_are_replaced_by_notice(self, valid_output, answer):
        valid_output["answer"] = answer
        result = CryptoAnalysisRequest.model_validate(valid_output)
        assert result.answer == MARKET_DATA_NOTICE
        assert "valores de mercado removidos" in result.assumptions[-1]

    def test_only_sentences_with_market_figures_are_removed(self, valid_output):
        valid_output["answer"] = (
            "Staking é travar ETH para validar blocos. Hoje rende cerca de 4% ao ano. "
            "Há risco de slashing."
        )
        result = CryptoAnalysisRequest.model_validate(valid_output)
        assert result.answer == (
            f"Staking é travar ETH para validar blocos. Há risco de slashing. {MARKET_DATA_NOTICE}"
        )
        assert result.assumptions[-1].endswith("4%")

    @pytest.mark.parametrize(
        "answer",
        [
            "O halving ocorre a cada 210.000 blocos e limita a emissão a 21 milhões de BTC.",
            "Para ser validador são necessários 32 ETH.",
        ],
    )
    def test_answer_allows_protocol_facts(self, valid_output, answer):
        valid_output["answer"] = answer
        result = CryptoAnalysisRequest.model_validate(valid_output)
        assert result.answer == answer
        assert result.assumptions == valid_output["assumptions"]

    @pytest.mark.parametrize(
        "field, value",
        [
            ("intent", "buy_signal"),
            ("confidence", 1.5),
            ("confidence", -0.1),
            ("answer_status", "maybe"),
            ("answer", ""),
            ("assets", "BTC"),
        ],
    )
    def test_rejects_invalid_values(self, valid_output, field, value):
        valid_output[field] = value
        with pytest.raises(ValidationError):
            CryptoAnalysisRequest.model_validate(valid_output)

    @pytest.mark.parametrize(
        "field", ["intent", "assets", "answer_status", "answer", "confidence"]
    )
    def test_rejects_missing_required(self, valid_output, field):
        del valid_output[field]
        with pytest.raises(ValidationError):
            CryptoAnalysisRequest.model_validate(valid_output)
