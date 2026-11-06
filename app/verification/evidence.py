"""Evidências a partir das observações reais das ferramentas, e seleção das relevantes por afirmação.

Cada observação vira um ou mais ``EvidenceItem`` com texto legível (para a NLI), números
(para a checagem determinística), entidades e a proveniência (fonte, endpoint, horários).
A resposta do agente nunca entra como evidência.
"""

import re
import unicodedata

from app.integrated.tools import TOOL_CATEGORY
from app.services.grounding import extract_figure_readings, format_pct, format_usd
from app.verification.schemas import Claim, EvidenceItem

WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "sobre entre quando porque desde ainda tambem segundo atual atuais dados valor valores mercado usd "
    "horas ultimas ultimos coinpaprika fonte atualizado atualizada variacao".split()
)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", (text or "").lower()).encode("ascii", "ignore").decode()


def content_words(text: str) -> set[str]:
    return {w for w in WORD.findall(normalize(text)) if len(w) >= 5 and w not in STOPWORDS}


def _numbers(value) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers(v)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers(v)]
    return []


def _source(obs: dict) -> dict:
    src = obs.get("source") or {}
    return {
        "provider": src.get("provider"),
        "endpoint": src.get("endpoint"),
        "source_updated_at": src.get("source_updated_at"),
        "retrieved_at": src.get("retrieved_at"),
    }


def _ticker_text(d: dict) -> str:
    parts = [
        f"{d.get('name')} ({d.get('symbol')}): preço {format_usd(d.get('price_usd'))}",
        f"variação 1h {format_pct(d.get('percent_change_1h'))}",
        f"variação 24h {format_pct(d.get('percent_change_24h'))}",
        f"variação 7d {format_pct(d.get('percent_change_7d'))}",
        f"variação 30d {format_pct(d.get('percent_change_30d'))}",
        f"market cap {format_usd(d.get('market_cap_usd'))}",
        f"volume 24h {format_usd(d.get('volume_24h_usd'))}",
        f"rank {d.get('rank')}",
    ]
    if d.get("ath_price_usd"):
        parts.append(f"máxima histórica (ATH) {format_usd(d['ath_price_usd'])}")
    return "; ".join(parts)


def build_evidence(observations: list[dict]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []

    def add(kind, tool, text, obs, entities=(), numbers=(), metadata=None):
        items.append(EvidenceItem(
            id=f"E{len(items) + 1}", kind=kind, tool=tool, text=text[:1500], entities=list(entities),
            numbers=list(numbers), metadata=metadata or {}, **_source(obs),
        ))

    for obs in observations:
        tool = obs.get("tool", "?")
        if not obs.get("ok"):
            err = obs.get("error") or {}
            add("tool_error", tool, f"Falha em {tool}: {err.get('kind')} — {err.get('message')}", obs,
                metadata={"error_kind": err.get("kind")})
            continue
        data = obs.get("data")
        category = TOOL_CATEGORY.get(tool, "api")
        if tool == "get_market_data":
            add("api", tool, _ticker_text(data), obs, [data.get("symbol", ""), data.get("name", "")], _numbers(data))
        elif tool == "get_market_details":
            profile, ohlcv = data.get("profile", {}), data.get("ohlcv_today_usd", {})
            text = (f"{profile.get('name')} ({profile.get('symbol')}): consenso {profile.get('proof_type')}; tipo "
                    f"{profile.get('type')}; OHLCV do dia: abertura {format_usd(ohlcv.get('open'))}, máxima "
                    f"{format_usd(ohlcv.get('high'))}, mínima {format_usd(ohlcv.get('low'))}, último {format_usd(ohlcv.get('close'))}")
            add("api", tool, text, obs, [profile.get("symbol", ""), profile.get("name", "")], _numbers(data))
        elif tool == "get_global_market":
            text = (f"Mercado global: market cap {format_usd(data.get('market_cap_usd'))}, volume 24h "
                    f"{format_usd(data.get('volume_24h_usd'))}, dominância do BTC {format_pct(data.get('bitcoin_dominance_percentage')).lstrip('+')}")
            add("api", tool, text, obs, ["BTC", "Bitcoin"], _numbers(data))
        elif tool == "query_market_database":
            rows = data.get("rows") or []
            stamp = (obs.get("source") or {}).get("retrieved_at")
            if not rows:
                add("sql", tool, f"Banco de snapshots: {data.get('message')} (SQL: {data.get('sql')})", obs,
                    metadata={"status": data.get("status")})
            for row in rows[:20]:
                label = row.get("symbol") or row.get("name") or ""
                values = ", ".join(f"{k}={v}" for k, v in row.items() if v is not None)
                add("sql", tool, f"Snapshot armazenado (coleta mais recente {stamp}): {values}", obs,
                    [label, row.get("name", "")], _numbers(row) + _numbers(data.get("params")),
                    {"status": data.get("status"), "sql": data.get("sql")})
        elif tool == "search_knowledge_base":
            for hit in data or []:
                add("rag", tool, hit.get("content", ""), obs, [], [],
                    {"source": hit.get("source"), "title": hit.get("title"), "chunk_id": hit.get("chunk_id"),
                     "score": hit.get("score")})
        elif tool == "calculate_percent_change":
            add("calc", tool, f"Cálculo local: de {data.get('old')} para {data.get('new')} = "
                f"{data.get('percent_change'):.4f}% (diferença {data.get('absolute_change')})", obs, [], _numbers(data))
        elif tool == "search_crypto_asset":
            continue  # identificação de ativo: não sustenta afirmação de mercado
        else:
            add(category if category in {"api", "sql", "rag"} else "api", tool, str(data)[:1500], obs, [], _numbers(data))
    return items


def select_evidence(claim: Claim, evidence: list[EvidenceItem], k: int = 3) -> list[EvidenceItem]:
    """As ``k`` evidências mais relevantes para a afirmação (não compara tudo com tudo)."""
    claim_words = content_words(claim.text)
    claim_values = [abs(v) for _, readings in extract_figure_readings(claim.text) for v in readings[:1]]
    claim_entities = {normalize(e) for e in claim.entities}
    scored = []
    for item in evidence:
        if item.kind == "tool_error":
            continue
        score = 0.0
        entities = {normalize(e) for e in item.entities if e}
        if claim_entities & entities:
            score += 3
        elif claim_entities and entities:
            score -= 2  # evidência de outro ativo
        score += len(claim_words & content_words(item.text))
        # Valor citado presente nesta evidência: a linha certa do SQL / o ativo certo.
        if claim_values and any(abs(v - abs(n)) <= max(0.01 * abs(n), 0.01) for v in claim_values for n in item.numbers):
            score += 3
        if claim.kind == "numeric" and item.kind in {"api", "sql", "calc"}:
            score += 1
        if claim.kind != "numeric" and item.kind == "rag":
            score += 1
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:k]]
