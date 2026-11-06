"""Fase 5: extração de afirmações, evidência, NLI (numérica e LLM), coerência lógica e política."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.verification.claims import extract_claims
from app.verification.evidence import build_evidence, select_evidence
from app.verification.logic import (
    check_calculations,
    check_comparisons,
    check_direction,
    check_limitations,
    check_period,
    llm_findings,
)
from app.verification.nli import LLMNLIClassifier, NLIUnavailableError, numeric_check, verify_claim
from app.verification.policy import aggregate, cautious_answer, correction_instructions, needs_correction
from app.verification.schemas import Claim, ClaimVerification, LogicalFinding
from app.verification.service import VerificationService
from tests.unit.phase5_fakes import OBS_BTC, OBS_ETH, OBS_RAG, OBS_SQL_FAIL


def nli_reply(label: str, confidence: float = 0.9) -> str:
    return json.dumps({"label": label, "confidence": confidence, "rationale": "teste"})


# --------------------------------------------------------------------------- afirmações


def test_claim_extraction_keeps_facts_and_skips_limitations_and_questions():
    answer = (
        "O **Bitcoin (BTC)** está cotado a US$ 81.545,05 (CoinPaprika). O Ethereum usa Proof of Stake desde 2022; "
        "o ETH subiu 7,27% em 24 horas. Não foi possível obter dados de 90 dias. Quer que eu compare com o SOL? "
        "Lembre-se de que não é recomendação."
    )
    claims = extract_claims(answer)
    assert [c.kind for c in claims] == ["numeric", "conceptual", "numeric"]
    assert claims[0].entities == ["BTC"] and claims[0].figures == ["US$ 81.545,05"]
    assert claims[2].text.startswith("o ETH subiu") and claims[2].entities == ["ETH"]


def test_comparison_sentence_is_conclusion():
    [claim] = extract_claims("O Bitcoin tem market cap maior que o Ethereum segundo os dados.")
    assert claim.kind == "conclusion" and claim.entities == ["BTC", "ETH"]


# --------------------------------------------------------------------------- evidência


def test_evidence_preserves_provenance_and_selects_by_asset():
    evidence = build_evidence([OBS_BTC, OBS_ETH, OBS_RAG, OBS_SQL_FAIL])
    assert [e.kind for e in evidence] == ["api", "api", "rag", "tool_error"]
    assert evidence[0].endpoint == "/tickers/btc-bitcoin" and evidence[0].source_updated_at
    assert evidence[2].metadata["chunk_id"] == "blockchain-basics.md#3"
    [claim] = extract_claims("O ETH está cotado a US$ 2.426,11 agora.")
    selected = select_evidence(claim, evidence, k=2)
    assert selected[0].entities[0] == "ETH" and all(e.kind != "tool_error" for e in selected)
    [concept] = extract_claims("Proof of Stake usa validadores que bloqueiam moedas como garantia.")
    assert select_evidence(concept, evidence)[0].kind == "rag"


# --------------------------------------------------------------------------- NLI


def test_numeric_check_entailment_contradiction_neutral():
    evidence = build_evidence([OBS_BTC])
    [ok] = extract_claims("O BTC está em US$ 81.545,05 e caiu 1,02% em 24 horas.")
    assert numeric_check(ok, evidence).label == "entailment"
    [wrong] = extract_claims("O BTC está em US$ 95.000,00 neste momento.")
    contradiction = numeric_check(wrong, evidence)
    assert contradiction.label == "contradiction" and "95.000,00" in contradiction.rationale
    [other] = extract_claims("O SOL está em US$ 180,25 neste momento.")
    assert numeric_check(other, evidence).label == "neutral"


@pytest.mark.parametrize("label", ["entailment", "contradiction", "neutral"])
def test_llm_nli_labels(label):
    claim = Claim(id="C1", text="O Ethereum usa Proof of Stake.", kind="conceptual", sentence_index=0)
    result = LLMNLIClassifier(FakeListChatModel(responses=[nli_reply(label, 0.8)])).classify(claim, build_evidence([OBS_RAG]))
    assert result.label == label and result.confidence == 0.8 and result.method == "llm_nli"
    assert result.evidence_ids == ["E1"]


def test_invalid_nli_output_is_infrastructure_failure_not_contradiction():
    claim = Claim(id="C1", text="O Ethereum usa Proof of Stake.", kind="conceptual", sentence_index=0)
    with pytest.raises(NLIUnavailableError):
        LLMNLIClassifier(FakeListChatModel(responses=["sem json"])).classify(claim, build_evidence([OBS_RAG]))
    with pytest.raises(NLIUnavailableError):
        LLMNLIClassifier(FakeListChatModel(responses=[json.dumps({"label": "talvez", "confidence": 1})])).classify(
            claim, build_evidence([OBS_RAG]))


def test_claim_without_evidence_is_neutral_without_llm():
    claim = Claim(id="C1", text="Solana usa Proof of History.", kind="conceptual", sentence_index=0)
    result = verify_claim(claim, [], LLMNLIClassifier(FakeListChatModel(responses=["não deveria ser chamado"])))
    assert result.label == "neutral" and result.method == "no_evidence"


# --------------------------------------------------------------------------- coerência lógica


def test_direction_mismatch_detected():
    findings = check_direction("O BTC subiu 1,02% nas últimas 24 horas.", [OBS_BTC])
    assert findings and findings[0].kind == "direction_mismatch" and findings[0].severity == "error"
    assert not check_direction("O BTC caiu 1,02% nas últimas 24 horas.", [OBS_BTC])


def test_inverted_comparison_detected():
    wrong = check_comparisons("O ETH tem market cap maior que o BTC.", [OBS_BTC, OBS_ETH])
    assert wrong and wrong[0].kind == "numeric_comparison"
    assert not check_comparisons("O BTC tem market cap maior que o ETH.", [OBS_BTC, OBS_ETH])
    assert not check_comparisons("Em 24h o BTC teve variação menor que a do ETH.", [OBS_BTC, OBS_ETH])


def test_inconsistent_calculation_detected():
    assert check_calculations("O preço foi de US$ 100,00 para US$ 110,00, alta de 25%.")[0].kind == "calculation"
    assert not check_calculations("O preço foi de US$ 100,00 para US$ 110,00, alta de 10%.")


def test_period_mismatch_and_ignored_limitation():
    assert check_period("Como o BTC foi nos últimos 7 dias?", "O BTC caiu 1,02% em 24 horas.")[0].kind == "period_mismatch"
    assert not check_period("Como o BTC foi nos últimos 7 dias?", "Em 7 dias o BTC caiu 0,84%.")
    assert check_limitations("O SOL foi o que mais subiu em 90 dias.", [OBS_SQL_FAIL])[0].kind == "ignored_limitation"
    assert not check_limitations("Os snapshots não cobrem 90 dias, então não há como responder.", [OBS_SQL_FAIL])


def test_llm_coherence_findings_and_failure():
    reply = json.dumps({"issues": [{"kind": "internal_contradiction", "description": "diz que subiu e depois que caiu"}]})
    [finding] = llm_findings(FakeListChatModel(responses=[reply]), "q", "a")
    assert finding.method == "llm" and finding.severity == "error"
    assert llm_findings(FakeListChatModel(responses=[json.dumps({"issues": []})]), "q", "a") == []


# --------------------------------------------------------------------------- política e serviço


def _cv(label: str, index: int = 0) -> ClaimVerification:
    claim = Claim(id=f"C{index}", text=f"afirmação {index}", kind="conceptual", sentence_index=index)
    return ClaimVerification(claim=claim, label=label, method="llm_nli")


def test_status_aggregation():
    warn = LogicalFinding(kind="period_mismatch", severity="warning", method="rule", description="x")
    err = LogicalFinding(kind="calculation", severity="error", method="rule", description="x")
    assert aggregate([_cv("entailment")], [warn], []) == "verified"
    assert aggregate([_cv("entailment"), _cv("neutral", 1)], [], []) == "partially_verified"
    assert aggregate([_cv("neutral")], [], []) == "unsupported"
    assert aggregate([_cv("entailment"), _cv("contradiction", 1)], [], []) == "contradicted"
    assert aggregate([_cv("entailment")], [err], []) == "contradicted"
    assert aggregate([_cv("entailment")], [], ["NLI indisponível"]) == "verification_failed"
    assert aggregate([], [], []) == "verified"
    ignored = LogicalFinding(kind="ignored_limitation", severity="error", method="rule", description="x")
    assert aggregate([_cv("neutral")], [ignored], []) == "unsupported"  # sem suporte ≠ contradição
    assert aggregate([], [ignored], []) == "unsupported"
    assert [needs_correction(s) for s in ("verified", "partially_verified", "unsupported", "contradicted", "verification_failed")] == [
        False, True, True, True, False]


def test_cautious_answer_removes_contradicted_and_marks_neutral():
    answer = "O BTC está em US$ 95.000,00. O Ethereum usa Proof of Stake. Solana usa Proof of History."
    claims = [c for c in [
        ClaimVerification(claim=extract_claims(answer)[0], label="contradiction", method="numeric_check"),
        ClaimVerification(claim=extract_claims(answer)[1], label="entailment", method="llm_nli"),
        ClaimVerification(claim=extract_claims(answer)[2], label="neutral", method="no_evidence"),
    ]]
    text = cautious_answer(answer, claims, [])
    assert "95.000" not in text.split("Aviso")[0]
    assert "Proof of Stake" in text and "Solana usa Proof of History (não confirmado" in text
    assert "Aviso: a verificação automática não confirmou" in text and "contradizer" in text
    instructions = correction_instructions(claims, [], build_evidence([OBS_BTC]))
    assert instructions[0].startswith("CORRIJA ou REMOVA") and instructions[1].startswith("QUALIFIQUE")


def test_service_end_to_end_with_scripted_llm():
    answer = "O BTC está em US$ 81.545,05 (CoinPaprika). O Ethereum usa Proof of Stake desde 2022."
    llm = FakeListChatModel(responses=[nli_reply("entailment"), json.dumps({"issues": []})])
    result = VerificationService(llm).verify("Preço do BTC e consenso do ETH?", answer, [OBS_BTC, OBS_RAG])
    assert result.status == "verified" and result.counts == {"entailment": 2, "contradiction": 0, "neutral": 0}
    assert [c.method for c in result.claims] == ["numeric_check", "llm_nli"]
    contradicted = VerificationService(FakeListChatModel(responses=[json.dumps({"issues": []})])).verify(
        "Preço do BTC?", "O BTC está em US$ 99.999,00.", [OBS_BTC])
    assert contradicted.status == "contradicted" and contradicted.correction_instructions


def test_service_reports_infrastructure_failure():
    result = VerificationService(FakeListChatModel(responses=["quebrado", "quebrado"])).verify(
        "Consenso do ETH?", "O Ethereum usa Proof of Stake desde 2022.", [OBS_RAG])
    assert result.status == "verification_failed" and result.verifier_errors
    assert not any(c.label == "contradiction" for c in result.claims)


def test_meta_text_headers_and_provenance_are_not_claims():
    answer = (
        "Com base nas ferramentas utilizadas, apresento as informações obtidas.\n"
        "Dados de mercado atuais:\n"
        "- A dominância do Bitcoin é de 57,13%.\n"
        "Este dado foi obtido através da ferramenta e está atualizado em 06/11/2026.\n"
        "A base de snapshots cobre apenas 0,97 dia, então não é possível responder sobre 90 dias.\n"
        "Para prosseguir, por favor, indique o ativo de seu interesse."
    )
    claims = extract_claims(answer)
    assert [c.text for c in claims] == ["A dominância do Bitcoin é de 57,13%"]


def test_sql_row_selected_by_cited_value():
    rows = [{"symbol": s, "name": s, "percent_change_7d": v} for s, v in
            [("BTC", -0.5), ("ETH", -1.2), ("SOL", -4.84), ("TRX", -10.47)]]
    obs = {"ok": True, "tool": "query_market_database", "data": {"status": "ok", "rows": rows, "params": [0]},
           "source": {"provider": "SQLite local", "retrieved_at": "2026-11-06T00:00:00Z"}}
    evidence = build_evidence([obs])
    [claim] = extract_claims("TRON caiu -10,47% em 7 dias no banco.")
    selected = select_evidence(claim, evidence)
    assert "TRX" in selected[0].text
    assert numeric_check(claim, selected).label == "entailment"
