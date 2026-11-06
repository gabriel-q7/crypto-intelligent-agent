"""Avaliadores da Fase 5 (assinatura LangSmith: ``(inputs, outputs, reference_outputs) -> dict``).

**Resposta**
| Chave | Tipo | O que mede |
|---|---|---|
| ``correctness`` | LLM-juiz (gemma4:e2b local) + traços obrigatórios | A resposta atende ao critério do exemplo |
| ``relevance`` | LLM-juiz | A resposta trata da pergunta feita |
| ``groundedness`` | Determinístico | Fração dos valores (US$/%) da resposta presentes nas observações reais |
| ``factual_consistency`` | Determinístico | Status da verificação aceitável (agente) ou igual ao esperado (casos de verificação) |
| ``logical_coherence`` | Determinístico | Sem erro lógico (agente) ou o achado lógico esperado foi detectado (casos lógicos) |
| ``uncertainty`` | Determinístico | Comunica limitação quando esperado; não inventa limitação quando não |

**Trajetória** (independente da resposta)
| Chave | O que mede |
|---|---|
| ``tool_selection`` | Categorias esperadas (api/sql/rag) foram usadas (recall) |
| ``required_tools`` | Ferramentas obrigatórias foram chamadas |
| ``no_unnecessary_tools`` | Nenhuma ferramenta proibida foi chamada |
| ``tool_combination`` | Em exemplos multi-categoria, as categorias foram combinadas na mesma execução |
| ``tool_inputs`` | Os ativos esperados aparecem nos argumentos das chamadas |
| ``error_handling`` | O erro esperado ocorreu E a resposta comunicou a limitação |
| ``evidence_used`` | A resposta cita algum valor que veio das ferramentas (quando houve dado numérico) |

Avaliadores que não se aplicam ao exemplo devolvem ``score=None``. Nota do LLM-juiz é opinião de
um modelo pequeno, não prova de correção (docs/langsmith-evaluation.md).
"""

import logging
from collections.abc import Callable

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from app.chains.market_chains import ask_json
from app.intent.classifier import find_assets
from app.services.grounding import extract_figure_readings, is_grounded, observed_numbers
from app.verification.logic import LIMITATION

logger = logging.getLogger(__name__)

NA = None
# Ferramentas cujos números são dados de mercado/cálculo (o "score" dos trechos de RAG não conta).
MARKET_NUMERIC_TOOLS = {"get_market_data", "get_market_details", "get_global_market", "query_market_database",
                        "calculate_percent_change"}


def _r(key: str, score, comment: str = "") -> dict:
    return {"key": key, "score": score, "comment": comment[:500]}


def _agent_mode(inputs: dict) -> bool:
    return inputs.get("mode", "agent") == "agent"


# --------------------------------------------------------------------------- resposta (determinísticos)


def groundedness(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    if not _agent_mode(inputs):
        return _r("groundedness", NA, "caso de verificação: a resposta é plantada")
    observed = observed_numbers([o for o in outputs.get("observations", []) if o.get("tool") in MARKET_NUMERIC_TOOLS])
    figures = extract_figure_readings(outputs.get("answer", ""))
    if not figures:
        return _r("groundedness", NA, "resposta sem valores de mercado")
    ok = [raw for raw, readings in figures
          if any(is_grounded(v, observed) or (raw.endswith("%") and is_grounded(-v, observed)) for v in readings)]
    missing = [raw for raw, _ in figures if raw not in ok]
    return _r("groundedness", len(ok) / len(figures), f"sem evidência: {missing}" if missing else "todos os valores nas observações")


def factual_consistency(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    v = outputs.get("verification", {})
    expected = reference_outputs.get("expected_verification")
    if expected:
        labels = reference_outputs.get("expected_labels", [])
        label_ok = all(label in v.get("labels", []) for label in labels)
        score = float(v.get("status") in expected and label_ok)
        return _r("factual_consistency", score, f"status={v.get('status')} esperado={expected}; labels={v.get('labels')}")
    # Agente: a resposta entregue precisa estar verificada ou só parcialmente sem sustentação.
    good = {"verified": 1.0, "partially_verified": 0.5, "unsupported": 0.25}
    return _r("factual_consistency", good.get(v.get("status"), 0.0), f"status={v.get('status')} (original {v.get('original_status')})")


def logical_coherence(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    found = outputs.get("verification", {}).get("findings", [])
    expected = reference_outputs.get("expected_findings")
    if expected:
        return _r("logical_coherence", float(all(k in found for k in expected)), f"achados={found} esperado={expected}")
    if not _agent_mode(inputs):
        return _r("logical_coherence", NA)
    return _r("logical_coherence", float(not found), f"erros lógicos na resposta final: {found}" if found else "sem erro lógico")


def uncertainty(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    if not _agent_mode(inputs) or "uncertainty_expected" not in reference_outputs:
        return _r("uncertainty", NA)
    hedged = bool(LIMITATION.search(outputs.get("answer", ""))) or outputs.get("answer", "").strip().endswith("?")
    expected = reference_outputs["uncertainty_expected"]
    if expected:
        return _r("uncertainty", float(hedged), "comunicou a limitação" if hedged else "não comunicou a limitação esperada")
    # Sem limitação esperada: só penaliza se a resposta foi tomada por ressalvas e não respondeu.
    return _r("uncertainty", 1.0 if not hedged or outputs.get("status") == "complete" else 0.5,
              "ressalva sem necessidade" if hedged else "")


# --------------------------------------------------------------------------- trajetória


def tool_selection(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    if not _agent_mode(inputs):
        return _r("tool_selection", NA)
    expected = set(reference_outputs.get("expected_categories", []))
    used = set(outputs.get("categories", [])) - {"deterministic"}
    if not expected:
        return _r("tool_selection", float(not used), f"esperado: nenhuma ferramenta; usadas {sorted(used)}")
    return _r("tool_selection", len(expected & used) / len(expected), f"esperado {sorted(expected)}, usado {sorted(used)}")


def required_tools(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    required = reference_outputs.get("required_tools") or []
    if not _agent_mode(inputs) or not required:
        return _r("required_tools", NA)
    called = set(outputs.get("tools", []))
    missing = [t for t in required if t not in called]
    return _r("required_tools", (len(required) - len(missing)) / len(required), f"faltaram {missing}" if missing else "ok")


def no_unnecessary_tools(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    if not _agent_mode(inputs):
        return _r("no_unnecessary_tools", NA)
    forbidden = set(reference_outputs.get("forbidden_tools") or [])
    bad = sorted(forbidden & set(outputs.get("tools", [])))
    calls = len(outputs.get("tools", []))
    return _r("no_unnecessary_tools", float(not bad), f"proibidas chamadas: {bad}; total de chamadas {calls}")


def tool_combination(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    expected = set(reference_outputs.get("expected_categories", []))
    if not _agent_mode(inputs) or len(expected) < 2:
        return _r("tool_combination", NA)
    used = set(outputs.get("categories", []))
    return _r("tool_combination", float(expected <= used), f"esperado {sorted(expected)} juntos; usado {sorted(used)}")


def tool_inputs(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    expected = reference_outputs.get("expected_assets")
    if not _agent_mode(inputs) or not expected:
        return _r("tool_inputs", NA)
    args_text = " ".join(str(v) for s in outputs.get("steps", []) for v in s.get("args", {}).values())
    found = set(find_assets(args_text.upper())) | set(find_assets(args_text))
    hit = [a for a in expected if a in found]
    return _r("tool_inputs", len(hit) / len(expected), f"ativos nos argumentos: {sorted(found)}; esperado {expected}")


def error_handling(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    kinds = reference_outputs.get("expected_error_kinds")
    if not _agent_mode(inputs) or not kinds:
        return _r("error_handling", NA)
    seen = {s.get("error_kind") for s in outputs.get("steps", []) if not s.get("ok")}
    communicated = bool(LIMITATION.search(outputs.get("answer", "")))
    hit = bool(seen & set(kinds))
    score = (0.5 if hit else 0.0) + (0.5 if communicated else 0.0)
    return _r("error_handling", score, f"erros vistos {sorted(k for k in seen if k)}; esperado {kinds}; comunicou={communicated}")


def evidence_used(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    if not _agent_mode(inputs):
        return _r("evidence_used", NA)
    market = [o for o in outputs.get("observations", []) if o.get("tool") in MARKET_NUMERIC_TOOLS]
    observed = observed_numbers(market)
    if not observed:
        return _r("evidence_used", NA, "sem dado numérico de mercado nas observações")
    cited = [raw for raw, readings in extract_figure_readings(outputs.get("answer", ""))
             if any(is_grounded(v, observed) or is_grounded(-v, observed) for v in readings)]
    return _r("evidence_used", float(bool(cited)), f"valores das ferramentas citados: {cited[:5]}")


# --------------------------------------------------------------------------- LLM-juiz


class JudgeScore(BaseModel):
    score: float = Field(ge=0, le=1)
    reason: str = ""


JUDGE_PROMPT = """Você avalia a resposta de um assistente de criptoativos. Dê uma nota de 0 a 1.
{aspect}
Responda SOMENTE com JSON: {{"score": 0.0-1.0, "reason": "uma frase"}}"""

CORRECTNESS = ("Aspecto: CORREÇÃO. A resposta cumpre o CRITÉRIO do exemplo? Valores numéricos não podem ser conferidos "
               "por você: julgue se a resposta faz o que o critério pede (fontes, distinções, limitações).")
RELEVANCE = "Aspecto: RELEVÂNCIA. A resposta trata diretamente da PERGUNTA, sem desviar do assunto?"


def llm_judges(llm: BaseChatModel) -> list[Callable]:
    def judge(key: str, aspect: str):
        def evaluate(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
            if not _agent_mode(inputs):
                return _r(key, NA)
            human = (f"PERGUNTA: {inputs.get('question')}\nCRITÉRIO: {reference_outputs.get('criteria', '')}\n"
                     f"RESPOSTA:\n{outputs.get('answer', '')}")
            try:
                out: JudgeScore = ask_json(llm, JUDGE_PROMPT.format(aspect=aspect), human, JudgeScore)
            except Exception as exc:  # noqa: BLE001 - juiz indisponível não vira nota
                return _r(key, NA, f"juiz indisponível: {type(exc).__name__}")
            score = out.score
            if key == "correctness":
                needed = reference_outputs.get("answer_must_include_any") or []
                if needed and not any(n.lower() in outputs.get("answer", "").lower() for n in needed):
                    score = min(score, 0.5)
                    out.reason += f" | faltou algum de {needed}"
            return _r(key, score, out.reason)

        evaluate.__name__ = key
        return evaluate

    return [judge("correctness", CORRECTNESS), judge("relevance", RELEVANCE)]


DETERMINISTIC = [
    groundedness, factual_consistency, logical_coherence, uncertainty,
    tool_selection, required_tools, no_unnecessary_tools, tool_combination, tool_inputs, error_handling, evidence_used,
]
TRAJECTORY_KEYS = {"tool_selection", "required_tools", "no_unnecessary_tools", "tool_combination", "tool_inputs",
                   "error_handling", "evidence_used"}


def all_evaluators(judge_llm: BaseChatModel | None) -> list[Callable]:
    return [*DETERMINISTIC, *(llm_judges(judge_llm) if judge_llm is not None else [])]
