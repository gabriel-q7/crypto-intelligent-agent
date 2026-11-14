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
import re

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError

from app.chains.market_chains import ask_json
from app.services.grounding import extract_figure_readings, is_grounded
from app.intent.classifier import find_assets
from app.verification.claims import COLLECTIVE
from app.verification.evidence import normalize, uncovered_assets
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


STAKE = re.compile(r"proof[- ]of[- ]stake|\b(d?pos)\b|prova de participa", re.IGNORECASE)
WORK = re.compile(r"proof[- ]of[- ]work|\bpow\b|prova de trabalho", re.IGNORECASE)
CLAUSE = re.compile(r"\benquanto\b|;|\bj[aá]\b|\bao passo que\b", re.IGNORECASE)


def consensus_family(text: str | None) -> set[str]:
    families = set()
    if text and STAKE.search(text):
        families.add("stake")
    if text and WORK.search(text):
        families.add("work")
    return families


def consensus_check(claim: Claim, evidence: list[EvidenceItem]) -> tuple[ClaimVerification | None, bool]:
    """Consenso (PoS × PoW) de cada ativo da afirmação contra o ``proof_type`` do perfil da CoinPaprika.

    A NLI de 2B aprovou "Ambas utilizam Proof of Stake" para Solana e Bitcoin com o perfil do BTC
    (``Proof of Work``) na evidência, e reprovou "Solana usa DPoS" (Fase 5, seção 5.11). A família do
    consenso é um fato estruturado: dá para conferir sem LLM. Devolve (contradição ou None, consistente)."""
    real: dict[str, set[str]] = {}
    for item in evidence:
        families = consensus_family(item.metadata.get("proof_type")) if item.tool == "get_asset_profile" else set()
        for asset in find_assets(" ".join(e for e in item.entities if e)) if families else []:
            real[asset] = families
    if not real:
        return None, False
    pairs: list[tuple[str, set[str]]] = []
    stated = consensus_family(claim.text)
    if COLLECTIVE.search(claim.text) and len(stated) == 1 and not find_assets(claim.text):
        pairs = [(asset, stated) for asset in claim.entities]
    else:
        for clause in CLAUSE.split(claim.text):
            assets, families = find_assets(clause), consensus_family(clause)
            if len(assets) == 1 and len(families) == 1:
                pairs.append((assets[0], families))
    checked = [(asset, families) for asset, families in pairs if asset in real]
    wrong = [asset for asset, families in checked if not families & real[asset]]
    if wrong:
        detail = ", ".join(f"{a} é Proof of {'Work' if 'work' in real[a] else 'Stake'}" for a in wrong)
        return ClaimVerification(claim=claim, label="contradiction", confidence=1.0, method="consensus_check",
                                 evidence_ids=[e.id for e in evidence],
                                 rationale=f"consenso diverge do perfil da CoinPaprika: {detail}"), False
    return None, bool(checked) and len(checked) == len(pairs)


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
    contradiction, consistent = consensus_check(claim, evidence)
    if contradiction is not None:
        return contradiction
    # Sem evidência sobre algum ativo da afirmação, o LLM aprovaria pelo que fala dos outros.
    if missing := uncovered_assets(claim, evidence):
        return ClaimVerification(claim=claim, label="neutral", method="no_evidence",
                                 evidence_ids=[e.id for e in evidence],
                                 rationale=f"nenhuma evidência selecionada fala de {', '.join(missing)}")
    result = nli.classify(claim, evidence)
    if consistent and result.label == "contradiction":
        # O consenso confere com o perfil; a contradição do LLM não se sustenta por regra (ex.: "DPoS" × "PoS").
        return result.model_copy(update={"label": "neutral", "rationale": "consenso confere com o perfil da "
                                         f"CoinPaprika; contradição do LLM não confirmada ({result.rationale[:160]})"})
    return result
