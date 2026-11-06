"""Agregação do status, decisão de correção, feedback estruturado e resposta cautelosa.

| Resultado | Comportamento |
|---|---|
| Afirmações sustentadas e resposta coerente (``verified``) | Entrega normal |
| Algumas ``neutral`` (``partially_verified``) | 1 correção pedindo para qualificar ou remover as não sustentadas |
| Nenhuma sustentada (``unsupported``) | 1 correção pedindo para remover ou qualificar |
| Contradição ou erro lógico (``contradicted``) | 1 correção com a evidência que contradiz |
| Falha da verificação (``verification_failed``) | Sem correção; a resposta sai marcada como "não verificada" |

Se a correção ainda falhar, ``cautious_answer`` remove as frases contraditas, marca as não
sustentadas e acrescenta um aviso com o que ficou sem resolver. Nunca há segunda correção.
"""

from app.verification.claims import sentences
from app.verification.schemas import ClaimVerification, EvidenceItem, LogicalFinding, VerificationStatus

UNVERIFIED_MARK = "(não confirmado pelas fontes consultadas)"


# Achados lógicos que indicam falta de sustentação, não contradição com a evidência.
UNSUPPORTED_FINDINGS = {"ignored_limitation"}


def aggregate(
    claims: list[ClaimVerification], findings: list[LogicalFinding], errors: list[str]
) -> VerificationStatus:
    if errors:
        return "verification_failed"
    errors_found = [f for f in findings if f.severity == "error"]
    if any(c.label == "contradiction" for c in claims) or any(f.kind not in UNSUPPORTED_FINDINGS for f in errors_found):
        return "contradicted"
    entailed = sum(c.label == "entailment" for c in claims)
    if claims and entailed == len(claims):
        status: VerificationStatus = "verified"
    elif claims:
        status = "partially_verified" if entailed else "unsupported"
    else:
        status = "verified"  # nada factual foi afirmado (só limitações, perguntas ou recusas)
    if errors_found and status == "verified":
        # Ex.: ferramenta sem dados e resposta afirmativa sem admitir a limitação.
        status = "partially_verified" if entailed else "unsupported"
    return status


def needs_correction(status: VerificationStatus) -> bool:
    return status in {"contradicted", "unsupported", "partially_verified"}


def _evidence_text(ids: list[str], evidence: dict[str, EvidenceItem], limit: int = 300) -> str:
    return " | ".join(f"[{i}] {evidence[i].text[:limit]}" for i in ids if i in evidence) or "nenhuma evidência"


def correction_instructions(
    claims: list[ClaimVerification], findings: list[LogicalFinding], evidence: list[EvidenceItem]
) -> list[str]:
    by_id = {e.id: e for e in evidence}
    out = []
    for c in claims:
        if c.label == "contradiction":
            out.append(f"CORRIJA ou REMOVA: \"{c.claim.text}\" contradiz a evidência ({c.rationale}). "
                       f"Evidência: {_evidence_text(c.evidence_ids, by_id)}")
        elif c.label == "neutral":
            out.append(f"QUALIFIQUE ou REMOVA: \"{c.claim.text}\" não é sustentado pelas ferramentas desta conversa "
                       f"({c.rationale}).")
    for f in findings:
        verb = "CORRIJA" if f.severity == "error" else "REVEJA"
        out.append(f"{verb} ({f.kind}): {f.description}")
    return out


def correction_message(instructions: list[str]) -> str:
    items = "\n".join(f"- {i}" for i in instructions)
    return (
        "Revisão automática da sua resposta anterior encontrou problemas:\n"
        f"{items}\n\n"
        "Reescreva a resposta final usando SOMENTE as observações das ferramentas já obtidas nesta conversa "
        "(chame outra ferramenta só se faltar um dado indispensável). Não repita afirmações contraditas; "
        "afirmações sem evidência devem ser removidas ou apresentadas como não confirmadas. Mantenha fontes e horários."
    )


def cautious_answer(answer: str, claims: list[ClaimVerification], findings: list[LogicalFinding]) -> str:
    """Versão conservadora da resposta quando a correção não resolveu tudo (determinística)."""
    contradicted = {c.claim.sentence_index for c in claims if c.label == "contradiction"}
    neutral = {c.claim.sentence_index for c in claims if c.label == "neutral"}
    kept = []
    for index, sentence in enumerate(sentences(answer)):
        if index in contradicted:
            continue
        kept.append(f"{sentence} {UNVERIFIED_MARK}" if index in neutral else sentence)
    body = ". ".join(kept).strip()
    if body and not body.endswith("."):
        body += "."
    problems = [f"afirmação removida por contradizer as fontes: \"{c.claim.text[:120]}\"" for c in claims if c.label == "contradiction"]
    problems += [f"{f.kind}: {f.description[:160]}" for f in findings if f.severity == "error"]
    notice = "Aviso: a verificação automática não confirmou toda a resposta."
    if problems:
        notice += " Pendências: " + "; ".join(problems) + "."
    return f"{body}\n\n{notice}".strip() if body else notice
