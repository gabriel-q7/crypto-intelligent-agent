"""Classificação afirmação × evidência: ``entailment``, ``contradiction`` ou ``neutral``.

Duas etapas, nesta ordem:

1. **Checagem numérica determinística** (afirmações com valores): cada valor citado precisa
   existir nas evidências selecionadas (tolerância de 1%, a mesma de ``verify_figures``).
   - todos encontrados → ``entailment`` (confiança 1,0);
   - algum ausente e há evidência do MESMO ativo → ``contradiction`` (a fonte tem o dado e ele
     é outro);
   - algum ausente sem evidência comparável → ``neutral``.
   Números não exigem LLM: um modelo de 2B erra comparações numéricas (lição das Fases 1–4).
2. **NLI com LLM local** (``gemma4:e2b`` via Ollama, decisão do usuário em 2026-11-05 no lugar
   de Transformers): afirmações sem valores. O modelo recebe só a afirmação e as evidências
   selecionadas e devolve JSON validado ``{label, confidence, rationale}``.

Sem evidência selecionada → ``neutral`` com ``method=no_evidence`` (sem chamar o LLM).
"""

import logging

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError

from app.chains.market_chains import ask_json
from app.services.grounding import extract_figure_readings, is_grounded
from app.verification.evidence import normalize
from app.verification.schemas import Claim, ClaimVerification, EvidenceItem, NLILabel

logger = logging.getLogger(__name__)

NLI_PROMPT = """Você é um classificador de inferência textual (NLI). Compare a HIPÓTESE com as EVIDÊNCIAS.

Rótulos:
- "entailment": as evidências afirmam o conteúdo da hipótese (mesmo com outras palavras).
- "contradiction": as evidências afirmam, sobre o MESMO sujeito e a MESMA propriedade, algo incompatível com a hipótese
  (ex.: a hipótese diz que X é feito por A e a evidência diz que X é feito por B).
- "neutral": as evidências não confirmam nem negam. Use neutral quando a hipótese traz um detalhe que as evidências
  simplesmente não mencionam (outra data, outra origem, outro número, outro assunto). Informação AUSENTE não é contradição.

Use SOMENTE as evidências; não use conhecimento próprio. Na dúvida, escolha neutral.

Exemplos (tema fora da base, só para mostrar o critério):
EVIDÊNCIA: "A ponte foi inaugurada em 1974 e liga as duas margens do rio."
- "A ponte liga as duas margens do rio." → entailment
- "A ponte nunca foi inaugurada." → contradiction (mesma propriedade, valor incompatível)
- "A ponte foi projetada por um engenheiro francês." → neutral (a evidência não fala do projetista)
- "A ponte tem 13 km de extensão." → neutral (a evidência não fala da extensão)

Responda SOMENTE com JSON: {"label": "entailment|contradiction|neutral", "confidence": 0.0-1.0, "rationale": "uma frase"}"""


class NLIOutput(BaseModel):
    label: NLILabel
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""


class NLIUnavailableError(RuntimeError):
    """O classificador não respondeu de forma válida (infraestrutura, não contradição)."""


def numeric_check(claim: Claim, evidence: list[EvidenceItem]) -> ClaimVerification | None:
    if claim.kind != "numeric" or not claim.figures:
        return None
    observed = [n for item in evidence for n in item.numbers]
    missing = []
    for raw, readings in extract_figure_readings(claim.text):
        percent = raw.endswith("%")
        if not any(is_grounded(v, observed) or (percent and is_grounded(-v, observed)) for v in readings):
            missing.append(raw)
    ids = [e.id for e in evidence]
    if not missing:
        return ClaimVerification(claim=claim, label="entailment", confidence=1.0, method="numeric_check",
                                 evidence_ids=ids, rationale="todos os valores citados estão nas evidências")
    claim_entities = {normalize(e) for e in claim.entities}
    same_entity = any(claim_entities & {normalize(x) for x in item.entities if x} for item in evidence)
    if same_entity and observed:
        return ClaimVerification(
            claim=claim, label="contradiction", confidence=0.9, method="numeric_check", evidence_ids=ids,
            rationale=f"valores sem correspondência na evidência do mesmo ativo: {', '.join(missing)}",
        )
    return ClaimVerification(claim=claim, label="neutral", confidence=0.6, method="numeric_check", evidence_ids=ids,
                             rationale=f"sem evidência comparável para: {', '.join(missing)}")


class LLMNLIClassifier:
    def __init__(self, llm: BaseChatModel) -> None:
        self.llm = llm

    def classify(self, claim: Claim, evidence: list[EvidenceItem]) -> ClaimVerification:
        premise = "\n".join(f"[{e.id}] {e.text}" for e in evidence)
        human = f"EVIDÊNCIAS:\n{premise}\n\nHIPÓTESE: {claim.text}"
        try:
            out: NLIOutput = ask_json(self.llm, NLI_PROMPT, human, NLIOutput)
        except (OutputParserException, ValidationError, ValueError) as exc:
            raise NLIUnavailableError(f"NLI inválida para {claim.id}: {str(exc)[:160]}") from exc
        return ClaimVerification(claim=claim, label=out.label, confidence=out.confidence, method="llm_nli",
                                 evidence_ids=[e.id for e in evidence], rationale=out.rationale[:300])


def verify_claim(claim: Claim, evidence: list[EvidenceItem], nli: LLMNLIClassifier) -> ClaimVerification:
    if not evidence:
        return ClaimVerification(claim=claim, label="neutral", method="no_evidence",
                                 rationale="nenhuma evidência relevante foi obtida para esta afirmação")
    numeric = numeric_check(claim, evidence)
    if numeric is not None:
        return numeric
    return nli.classify(claim, evidence)
