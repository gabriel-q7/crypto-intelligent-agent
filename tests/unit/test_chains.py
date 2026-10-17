import json

import pytest
from langchain_core.runnables import Runnable, RunnableLambda

from app.chains import (
    build_analysis_chain,
    build_imperative_chain,
    build_json_chain,
    build_lcel_chain,
)
from app.errors import AnalysisOutputError, LLMUnavailableError
from app.schemas import CryptoAnalysisRequest
from app.services.analysis import run_analysis, run_batch, stream_analysis
from tests.conftest import VALID_OUTPUT, fake_llm

BUILDERS = [build_lcel_chain, build_imperative_chain]
VALID_JSON = json.dumps(VALID_OUTPUT, ensure_ascii=False)


@pytest.mark.parametrize("builder", BUILDERS, ids=["lcel", "imperative"])
class TestBothCompositions:
    def test_is_runnable(self, builder, valid_llm):
        assert isinstance(builder(valid_llm), Runnable)

    def test_invoke_returns_validated_model(self, builder, valid_llm, question):
        result = builder(valid_llm).invoke(question)
        assert isinstance(result, CryptoAnalysisRequest)
        assert result.assets == ["BTC", "ETH"]

    def test_invoke_accepts_dict(self, builder, valid_llm):
        result = builder(valid_llm).invoke({"question": "Compare BTC e ETH"})
        assert isinstance(result, CryptoAnalysisRequest)

    def test_batch(self, builder, valid_llm, question):
        results = builder(valid_llm).batch([question, question])
        assert len(results) == 2
        assert all(isinstance(r, CryptoAnalysisRequest) for r in results)

    def test_stream_yields_final_validated_model(self, builder, valid_llm, question):
        chunks = list(builder(valid_llm).stream(question))
        # A validação final só ocorre com o objeto completo: um único chunk validado.
        assert len(chunks) == 1
        assert isinstance(chunks[0], CryptoAnalysisRequest)

    def test_non_json_output_raises_domain_error(self, builder, question):
        chain = builder(fake_llm("Não sei responder."))
        with pytest.raises(AnalysisOutputError):
            run_analysis(chain, question)

    def test_schema_violation_raises_domain_error(self, builder, question):
        chain = builder(fake_llm(json.dumps({**VALID_OUTPUT, "intent": "buy_now"})))
        with pytest.raises(AnalysisOutputError) as info:
            run_analysis(chain, question)
        assert info.value.raw_output["intent"] == "buy_now"

    def test_connection_error_raises_llm_unavailable(self, builder, question):
        def offline(_):
            raise ConnectionError("connection refused")

        with pytest.raises(LLMUnavailableError):
            run_analysis(builder(RunnableLambda(offline)), question)


def test_both_compositions_produce_same_result(question):
    lcel = build_lcel_chain(fake_llm(VALID_JSON)).invoke(question)
    imperative = build_imperative_chain(fake_llm(VALID_JSON)).invoke(question)
    assert lcel == imperative


def test_llm_receives_system_and_human_messages(question):
    seen = []

    def capture(prompt_value):
        seen.append(prompt_value.to_messages())
        return VALID_JSON

    build_lcel_chain(RunnableLambda(capture)).invoke(question)
    assert [m.type for m in seen[0]] == ["system", "human"]


def test_build_analysis_chain_selects_composition(valid_llm):
    assert build_analysis_chain(valid_llm).get_name() != build_analysis_chain(
        valid_llm, "imperative"
    ).get_name()
    with pytest.raises(ValueError, match="Composição desconhecida"):
        build_analysis_chain(valid_llm, "graph")


def test_json_chain_streams_partial_objects(question):
    partials = list(build_json_chain(fake_llm(VALID_JSON)).stream(question))
    assert len(partials) > 1
    assert partials[-1] == VALID_OUTPUT
    # Parciais intermediários são incompletos e não satisfazem o schema.
    assert set(partials[0]) < set(VALID_OUTPUT)


def test_stream_analysis_validates_final_object(question):
    partials = []
    result = stream_analysis(fake_llm(VALID_JSON), question, on_partial=partials.append)
    assert len(partials) > 1
    assert result == CryptoAnalysisRequest.model_validate(VALID_OUTPUT)


def test_stream_analysis_rejects_invalid_final_object(question):
    payload = json.dumps({k: v for k, v in VALID_OUTPUT.items() if k != "confidence"})
    with pytest.raises(AnalysisOutputError):
        stream_analysis(fake_llm(payload), question)


def test_stream_analysis_without_json_output(question):
    with pytest.raises(AnalysisOutputError):
        stream_analysis(fake_llm("sem json aqui"), question)


def test_run_batch_isolates_failures(question):
    llm = fake_llm(VALID_JSON, "resposta inválida", VALID_JSON)
    results = run_batch(build_lcel_chain(llm), [question] * 3, max_concurrency=1)
    assert isinstance(results[0], CryptoAnalysisRequest)
    assert isinstance(results[1], AnalysisOutputError)
    assert isinstance(results[2], CryptoAnalysisRequest)
