"""Serviço de verificação: afirmações → evidência relevante → NLI → coerência lógica → status.

Faz parte do fluxo normal do agente integrado (``IntegratedAgentService``): toda resposta passa
por aqui antes de ser entregue. Instrumentado com LangSmith (``traceable``); sem
``LANGSMITH_TRACING`` os decoradores não fazem nada.
"""

import logging
import time

from langchain_core.language_models import BaseChatModel
from langsmith import traceable

from app.verification.claims import extract_claims
from app.verification.evidence import build_evidence, select_evidence
from app.verification.logic import CoherenceUnavailableError, llm_findings, rule_findings
from app.verification.nli import LLMNLIClassifier, NLIUnavailableError, verify_claim
from app.verification.policy import aggregate, correction_instructions
from app.verification.schemas import ClaimVerification, EvidenceItem, VerificationResult

logger = logging.getLogger(__name__)


class VerificationService:
    def __init__(self, llm: BaseChatModel, coherence_llm: bool = True, evidence_per_claim: int = 3) -> None:
        self.nli = LLMNLIClassifier(llm)
        self.llm = llm
        self.coherence_llm = coherence_llm
        self.evidence_per_claim = evidence_per_claim

    @traceable(name="verification.claims_nli", run_type="chain")
    def _claims(self, answer: str, evidence: list[EvidenceItem]) -> tuple[list[ClaimVerification], list[str]]:
        results, errors = [], []
        for claim in extract_claims(answer):
            selected = select_evidence(claim, evidence, self.evidence_per_claim)
            try:
                results.append(verify_claim(claim, selected, self.nli))
            except NLIUnavailableError as exc:
                errors.append(str(exc))
            except Exception as exc:  # noqa: BLE001 - Ollama fora do ar etc.: falha de infraestrutura
                errors.append(f"NLI indisponível: {type(exc).__name__}: {str(exc)[:120]}")
        return results, errors

    @traceable(name="verification.logic", run_type="chain")
    def _logic(self, question: str, answer: str, observations: list[dict]):
        findings, errors = rule_findings(question, answer, observations), []
        if self.coherence_llm and answer.strip():
            try:
                findings += llm_findings(self.llm, question, answer)
            except CoherenceUnavailableError as exc:
                errors.append(str(exc))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"coerência indisponível: {type(exc).__name__}: {str(exc)[:120]}")
        return findings, errors

    @traceable(name="verification.verify", run_type="chain")
    def verify(self, question: str, answer: str, observations: list[dict]) -> VerificationResult:
        started = time.perf_counter()
        evidence = build_evidence(observations)
        claims, claim_errors = self._claims(answer, evidence)
        findings, logic_errors = self._logic(question, answer, observations)
        errors = claim_errors + logic_errors
        status = aggregate(claims, findings, errors)
        result = VerificationResult(
            status=status,
            claims=claims,
            logical_findings=findings,
            evidence=evidence,
            issues=[f"{c.claim.id} {c.label}: {c.claim.text[:120]}" for c in claims if c.label != "entailment"]
            + [f"{f.kind} ({f.severity}): {f.description[:160]}" for f in findings],
            correction_instructions=correction_instructions(claims, findings, evidence),
            verifier_errors=errors,
            elapsed_s=round(time.perf_counter() - started, 2),
        )
        logger.info("verification status=%s counts=%s findings=%d errors=%d elapsed_s=%.1f",
                    status, result.counts, len(findings), len(errors), result.elapsed_s)
        return result
