"""Extração de afirmações verificáveis da resposta (determinística).

A resposta é dividida em frases (e frases longas com ``;`` em partes). Ficam de fora:
- perguntas e frases curtas demais;
- declarações de limitação ou de incerteza ("não foi possível obter…", "não há dados…"), que são
  justamente a qualificação desejada e não fatos a provar;
- recomendações, especulação explícita e meta-texto ("lembre-se…", "posso ajudar…").

Cada afirmação recebe o tipo (``numeric`` se cita valor; ``conclusion`` se compara ou conclui;
``conceptual`` caso contrário) e as entidades (ativos) citadas, usadas na seleção de evidência.
O texto original da resposta é preservado: a afirmação guarda o índice da frase.
"""

import re

from app.intent.classifier import find_assets
from app.services.grounding import SENTENCE_BOUNDARY, extract_figure_readings
from app.verification.schemas import Claim

MAX_CLAIMS = 8
NON_FACTUAL = re.compile(
    # limitações e incerteza (a qualificação desejada, não fato a provar)
    r"n[aã]o (foi poss[ií]vel|[eé] poss[ií]vel|h[aá] (dados|informa|registro|documento)|tenho|encontrei|consegui|"
    r"cobre|retornou|existe|est[aá] dispon)|cobrem? apenas|indispon[ií]ve|insuficiente|"
    r"lembre-se|lembrando|recomend|n[aã]o (é|constitui) (uma )?recomenda|"
    r"talvez|possivelmente|pode ser que|n[aã]o fa[cç]o previs|nenhum valor foi estimado|aviso:|observa[cç][aã]o:|"
    # meta-texto e pedidos ao usuário
    r"posso ajudar|quer que eu|deseja que|se quiser|por favor|indique|informe (o|qual|a)|"
    r"com base nas? (ferramentas?|informa[cç][oõ]es|dados)|apresento|segue(m)? (abaixo|as)|em resumo",
    re.IGNORECASE,
)
# Frase sem valor que só diz de onde/quando veio o dado ("obtido via X, atualizado em ...").
PROVENANCE_ONLY = re.compile(r"\b(obtid[oa]s?|atualizad[oa]s? em|fonte|ferramenta|coinpaprika|snapshots?)\b", re.IGNORECASE)
CONCLUSION = re.compile(
    r"\b(maior|menor|superior|inferior|acima|abaixo|mais que|menos que|supera|superou|portanto|logo|"
    r"por isso|enquanto|ao passo|melhor|pior|diferen[cç]a)\b",
    re.IGNORECASE,
)
MARKDOWN = re.compile(r"[*_`#>]+")
BULLET = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+", re.MULTILINE)


def _sentences(answer: str) -> list[str]:
    text = BULLET.sub("", MARKDOWN.sub("", answer or ""))
    parts: list[str] = []
    for block in re.split(r"\n+", text):
        if block.strip().endswith(":"):  # cabeçalho ("Dados de mercado atuais:")
            continue
        for sentence in SENTENCE_BOUNDARY.split(block.strip()):
            for piece in sentence.split(";"):
                piece = piece.strip(" .:-")
                if piece:
                    parts.append(piece)
    return parts


def extract_claims(answer: str, limit: int = MAX_CLAIMS) -> list[Claim]:
    claims: list[Claim] = []
    for index, sentence in enumerate(_sentences(answer)):
        if len(sentence.split()) < 4 or sentence.endswith("?") or NON_FACTUAL.search(sentence):
            continue
        figures = [raw for raw, readings in extract_figure_readings(sentence) if readings]
        if not figures and PROVENANCE_ONLY.search(sentence) and not find_assets(sentence):
            continue
        kind = "numeric" if figures else ("conclusion" if CONCLUSION.search(sentence) else "conceptual")
        claims.append(Claim(
            id=f"C{len(claims) + 1}", text=sentence, kind=kind, sentence_index=index,
            figures=figures, entities=find_assets(sentence),
        ))
        if len(claims) >= limit:
            break
    return claims


def sentences(answer: str) -> list[str]:
    return _sentences(answer)
