"""Resultado estruturado da verificação de respostas (Fase 5).

Semântica dos status gerais (``VerificationStatus``):

| Status | Quando |
|---|---|
| ``verified`` | Toda afirmação factual foi sustentada (entailment) e não há achado lógico de severidade ``error`` |
| ``partially_verified`` | Há afirmações sustentadas e outras ``neutral`` (sem evidência suficiente), sem contradição nem erro lógico |
| ``unsupported`` | Nenhuma afirmação foi sustentada (todas ``neutral``) ou não havia evidência; nada contradito. Limitação ignorada (ferramenta sem dados e resposta afirmativa) também cai aqui, ou em ``partially_verified`` |
| ``contradicted`` | Ao menos uma afirmação contradiz a evidência OU há erro lógico (sentido, comparação, cálculo, contradição interna) |
| ``verification_failed`` | A verificação não pôde rodar (LLM indisponível, saída inválida). NÃO significa que a resposta esteja errada; significa que ela não foi verificada |

``neutral`` nunca é tratado como contradição: quer dizer "a evidência não decide".
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NLILabel = Literal["entailment", "contradiction", "neutral"]
VerificationStatus = Literal["verified", "partially_verified", "unsupported", "contradicted", "verification_failed"]
EvidenceKind = Literal["api", "sql", "rag", "calc", "tool_error"]


class EvidenceItem(BaseModel):
    """Evidência real de execução (observação de ferramenta), nunca texto do próprio agente."""

    id: str
    kind: EvidenceKind
    tool: str
    text: str
    provider: str | None = None
    endpoint: str | None = None
    source_updated_at: datetime | None = None
    retrieved_at: datetime | None = None
    entities: list[str] = Field(default=[], description="Símbolos/nomes de ativos que a evidência cobre.")
    numbers: list[float] = Field(default=[], description="Valores numéricos da observação (para checagem determinística).")
    metadata: dict = {}


class Claim(BaseModel):
    id: str
    text: str
    kind: Literal["numeric", "conceptual", "conclusion"]
    sentence_index: int
    figures: list[str] = []
    entities: list[str] = []


class ClaimVerification(BaseModel):
    claim: Claim
    label: NLILabel
    confidence: float | None = Field(default=None, ge=0, le=1)
    method: Literal["numeric_check", "llm_nli", "no_evidence"]
    evidence_ids: list[str] = []
    rationale: str = ""


class LogicalFinding(BaseModel):
    kind: Literal[
        "internal_contradiction",
        "numeric_comparison",
        "direction_mismatch",
        "calculation",
        "period_mismatch",
        "ignored_limitation",
        "unsupported_conclusion",
    ]
    severity: Literal["error", "warning"]
    method: Literal["rule", "llm"]
    description: str
    claim_ids: list[str] = []


class VerificationResult(BaseModel):
    status: VerificationStatus
    claims: list[ClaimVerification] = []
    logical_findings: list[LogicalFinding] = []
    evidence: list[EvidenceItem] = Field(default=[], description="Evidências usadas (resumidas).")
    issues: list[str] = []
    correction_instructions: list[str] = []
    correction_attempted: bool = False
    corrected_passed: bool | None = None
    original_status: VerificationStatus | None = Field(default=None, description="Status da 1ª resposta, se houve correção.")
    verifier_errors: list[str] = []
    elapsed_s: float = 0.0

    @property
    def counts(self) -> dict[str, int]:
        result = {"entailment": 0, "contradiction": 0, "neutral": 0}
        for item in self.claims:
            result[item.label] += 1
        return result

    def summary(self) -> dict:
        return {"status": self.status, **self.counts, "logical_errors": sum(f.severity == "error" for f in self.logical_findings)}
