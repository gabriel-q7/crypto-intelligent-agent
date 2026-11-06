"""Coerência lógica da resposta, separada da NLI factual.

Regras determinísticas (preferidas sempre que há valor estruturado):
- **direction_mismatch**: "X subiu/caiu N%" com sinal oposto ao da evidência do mesmo ativo
  (inversão que ``verify_figures`` não pega, porque compara o módulo);
- **numeric_comparison**: "A maior/menor que B" numa métrica cujos valores reais dizem o contrário;
- **calculation**: frase com dois valores em US$ e um percentual que não corresponde a (b-a)/a;
- **period_mismatch**: a pergunta pede um período e a resposta só usa métricas de outra janela;
- **ignored_limitation**: houve falha de ferramenta ou cobertura insuficiente e a resposta não
  menciona limitação alguma.

LLM (``gemma4:e2b``, JSON validado) para o que regra não cobre: contradição interna entre frases
e conclusão que não decorre do que foi dito. Achados do LLM entram como ``warning``, exceto
``internal_contradiction`` (``error``), porque o modelo pequeno produz falso positivo.
"""

import logging
import re

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError

from app.chains.market_chains import ask_json
from app.intent.classifier import find_assets, find_period
from app.services.grounding import extract_figure_readings
from app.verification.claims import sentences
from app.verification.schemas import LogicalFinding

logger = logging.getLogger(__name__)

UP = re.compile(r"\b(subiu|subiram|alta|valoriz\w*|ganho|avan[cç]\w*|aumento|aumentou|cresceu|positiv\w*)\b", re.I)
DOWN = re.compile(r"\b(caiu|ca[ií]ram|queda|desvaloriz\w*|perda|recuo|recuou|diminuiu|negativ\w*|baixa)\b", re.I)
GREATER = re.compile(r"\b(maior|superior|acima|mais alt\w*|supera\w*|à frente)\b", re.I)
LESSER = re.compile(r"\b(menor|inferior|abaixo|mais baix\w*)\b", re.I)
LIMITATION = re.compile(
    r"n[aã]o (foi poss[ií]vel|[eé] poss[ií]vel|h[aá]|consegui|encontrei|cobre|retornou|existe|est[aá] dispon)|"
    r"indispon[ií]vel|limita[cç]|"
    r"insuficiente|sem dados|falh|apenas|somente|n[aã]o cobrem|n[aã]o abrange|aus[eê]ncia",
    re.I,
)
METRICS = {
    "percent_change_24h": re.compile(r"24\s*h|24 horas|di[aá]ri", re.I),
    "percent_change_7d": re.compile(r"\b7\s*d|7 dias|semana", re.I),
    "percent_change_30d": re.compile(r"\b30\s*d|30 dias|m[eê]s", re.I),
    "market_cap_usd": re.compile(r"market ?cap|capitaliza", re.I),
    "volume_24h_usd": re.compile(r"volume", re.I),
    "price_usd": re.compile(r"pre[cç]o|cota", re.I),
}
PERIOD_FIELD = {1: "percent_change_24h", 7: "percent_change_7d", 30: "percent_change_30d"}


def _asset_values(observations: list[dict]) -> dict[str, dict]:
    values: dict[str, dict] = {}
    for obs in observations:
        if not obs.get("ok"):
            continue
        if obs.get("tool") == "get_market_data":
            data = obs["data"]
            values[data.get("symbol", "").upper()] = data
        elif obs.get("tool") == "query_market_database":
            for row in obs["data"].get("rows") or []:
                if row.get("symbol"):
                    values.setdefault(str(row["symbol"]).upper(), {}).update(row)
    return values


def _metric_in(sentence: str) -> str | None:
    for field, pattern in METRICS.items():
        if pattern.search(sentence):
            return field
    return None


def check_direction(answer: str, observations: list[dict]) -> list[LogicalFinding]:
    values = _asset_values(observations)
    findings = []
    for sentence in sentences(answer):
        assets = [a for a in find_assets(sentence) if a in values]
        if len(assets) != 1:
            continue
        up, down = bool(UP.search(sentence)), bool(DOWN.search(sentence))
        if up == down:
            continue
        metric = _metric_in(sentence)
        field = metric if metric and metric.startswith("percent") else "percent_change_24h"
        real = values[assets[0]].get(field)
        if real is None or abs(real) < 0.01:
            continue
        if (up and real < 0) or (down and real > 0):
            findings.append(LogicalFinding(
                kind="direction_mismatch", severity="error", method="rule",
                description=f"'{sentence[:120]}' diz que {assets[0]} {'subiu' if up else 'caiu'}, mas {field} = {real:+.2f}%",
            ))
    return findings


def check_comparisons(answer: str, observations: list[dict]) -> list[LogicalFinding]:
    values = _asset_values(observations)
    findings = []
    for sentence in sentences(answer):
        assets = [a for a in find_assets(sentence) if a in values]
        field = _metric_in(sentence)
        if len(assets) != 2 or not field:
            continue
        greater, lesser = bool(GREATER.search(sentence)), bool(LESSER.search(sentence))
        if greater == lesser:
            continue
        a, b = assets
        va, vb = values[a].get(field), values[b].get(field)
        if va is None or vb is None or va == vb:
            continue
        claims_a_greater = greater  # "A maior que B" (ordem de citação)
        if claims_a_greater != (va > vb):
            findings.append(LogicalFinding(
                kind="numeric_comparison", severity="error", method="rule",
                description=f"'{sentence[:120]}' compara {a} e {b} em {field}, mas os dados são {a}={va:g} e {b}={vb:g}",
            ))
    return findings


def check_calculations(answer: str) -> list[LogicalFinding]:
    findings = []
    for sentence in sentences(answer):
        figures = extract_figure_readings(sentence)
        money = [r[0] for raw, r in figures if not raw.endswith("%")]
        pct = [r[0] for raw, r in figures if raw.endswith("%")]
        if len(money) != 2 or len(pct) != 1 or money[0] == 0:
            continue
        expected = (money[1] - money[0]) / abs(money[0]) * 100
        if abs(abs(expected) - abs(pct[0])) > max(0.05 * abs(expected), 0.1):
            findings.append(LogicalFinding(
                kind="calculation", severity="error", method="rule",
                description=f"'{sentence[:120]}': de {money[0]:g} para {money[1]:g} a variação é {expected:+.2f}%, não {pct[0]:g}%",
            ))
    return findings


def check_period(question: str, answer: str) -> list[LogicalFinding]:
    _, days = find_period(question)
    wanted = PERIOD_FIELD.get(days or 0)
    if not wanted:
        return []
    used = {f for f, p in METRICS.items() if f.startswith("percent") and p.search(answer)}
    if used and wanted not in used and not LIMITATION.search(answer):
        return [LogicalFinding(
            kind="period_mismatch", severity="warning", method="rule",
            description=f"a pergunta pede {days} dia(s), mas a resposta usa só {', '.join(sorted(used))} sem explicar",
        )]
    return []


def check_limitations(answer: str, observations: list[dict]) -> list[LogicalFinding]:
    failures = [o for o in observations if not o.get("ok")]
    if failures and not LIMITATION.search(answer):
        kinds = ", ".join(sorted({f"{o.get('tool')}:{(o.get('error') or {}).get('kind')}" for o in failures}))
        return [LogicalFinding(
            kind="ignored_limitation", severity="error", method="rule",
            description=f"ferramentas falharam ou não tinham dados ({kinds}) e a resposta não menciona a limitação",
        )]
    return []


def rule_findings(question: str, answer: str, observations: list[dict]) -> list[LogicalFinding]:
    return [
        *check_direction(answer, observations),
        *check_comparisons(answer, observations),
        *check_calculations(answer),
        *check_period(question, answer),
        *check_limitations(answer, observations),
    ]


COHERENCE_PROMPT = """Você revisa a COERÊNCIA LÓGICA de uma resposta (não verifica fatos externos).
Aponte apenas problemas claros:
- "internal_contradiction": duas frases da resposta afirmam coisas incompatíveis entre si;
- "unsupported_conclusion": uma conclusão não decorre do que a própria resposta apresentou.
Não aponte estilo, tamanho nem falta de detalhes. Se não houver problema claro, devolva lista vazia.
Responda SOMENTE com JSON: {"issues": [{"kind": "internal_contradiction|unsupported_conclusion", "description": "..."}]}"""


class CoherenceIssue(BaseModel):
    kind: str
    description: str = Field(max_length=400)


class CoherenceOutput(BaseModel):
    issues: list[CoherenceIssue] = []


class CoherenceUnavailableError(RuntimeError):
    pass


def llm_findings(llm: BaseChatModel, question: str, answer: str) -> list[LogicalFinding]:
    try:
        out: CoherenceOutput = ask_json(llm, COHERENCE_PROMPT, f"PERGUNTA: {question}\n\nRESPOSTA:\n{answer}", CoherenceOutput)
    except (OutputParserException, ValidationError, ValueError) as exc:
        raise CoherenceUnavailableError(f"checagem de coerência inválida: {str(exc)[:160]}") from exc
    findings = []
    for issue in out.issues[:3]:
        kind = issue.kind if issue.kind in {"internal_contradiction", "unsupported_conclusion"} else "unsupported_conclusion"
        findings.append(LogicalFinding(
            kind=kind, severity="error" if kind == "internal_contradiction" else "warning", method="llm",
            description=issue.description,
        ))
    return findings

