"""Ancoragem da resposta nos dados reais das ferramentas.

1. ``parse_observation``: lê o JSON devolvido por uma ferramenta.
2. ``findings_from`` / ``sources_from``: montam achados e fontes DIRETAMENTE das observações,
   com os horários da fonte e da consulta — o LLM não escreve esses campos.
3. ``verify_figures``: todo valor monetário ou percentual citado na resposta precisa existir
   nas observações (tolerância de 1% para arredondamento). Frases com valores não confirmados
   são removidas e viram incerteza. É a defesa contra dado de mercado inventado.
"""

import json
import re
from collections.abc import Iterable
from typing import Any

from app.schemas.report import DataSource, Finding

MULTIPLIERS = {
    "k": 1e3, "mil": 1e3,
    "m": 1e6, "mi": 1e6, "milhão": 1e6, "milhões": 1e6, "milhao": 1e6, "milhoes": 1e6,
    "b": 1e9, "bi": 1e9, "bilhão": 1e9, "bilhões": 1e9, "bilhao": 1e9, "bilhoes": 1e9,
    "t": 1e12, "tri": 1e12, "trilhão": 1e12, "trilhões": 1e12, "trilhao": 1e12, "trilhoes": 1e12,
}
_NUMBER = r"-?\d+(?:[.,]\d+)*"
_MULT = r"(?:mil|milh(?:ão|ões|ao|oes)|bilh(?:ão|ões|ao|oes)|trilh(?:ão|ões|ao|oes)|tri|bi|mi|[kmbt])\b"
FIGURE_PATTERN = re.compile(
    rf"(?P<currency>US\$|\$|USD)\s?(?P<n1>{_NUMBER})(?:\s?(?P<m1>{_MULT}))?"
    rf"|(?P<n2>{_NUMBER})\s?(?:(?P<m2>{_MULT})\s?(?:de\s)?)?(?:USD|dólares|dolares)\b"
    rf"|(?P<n3>{_NUMBER})\s?%",
    re.IGNORECASE,
)
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9])")
TOLERANCE = 0.01


def parse_number(raw: str) -> float | None:
    """Aceita formatos pt-BR (81.545,05) e en (81,545.05)."""
    text = raw.strip().rstrip(".,")
    if not text or not any(c.isdigit() for c in text):
        return None
    if "." in text and "," in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text:
        text = text.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", text) else text.replace(",", ".")
    elif "." in text and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _readings(raw_number: str, has_multiplier: bool) -> list[float]:
    """Leituras possíveis. Com multiplicador, "1.638 trilhões" pode ser 1,638 (ponto decimal,
    estilo en) ou 1.638 (milhar, estilo pt): as duas são consideradas."""
    value = parse_number(raw_number)
    if value is None:
        return []
    readings = [value]
    # Um único separador seguido de 3 dígitos: também vale a leitura decimal ("1.638" -> 1,638).
    if has_multiplier and re.fullmatch(r"-?\d{1,3}[.,]\d{3}", raw_number):
        readings.append(float(raw_number.replace(",", ".")))
    return readings


def extract_figure_readings(text: str) -> list[tuple[str, list[float]]]:
    figures = []
    for match in FIGURE_PATTERN.finditer(text):
        raw_number = match.group("n1") or match.group("n2") or match.group("n3")
        multiplier = (match.group("m1") or match.group("m2") or "").lower()
        factor = MULTIPLIERS.get(multiplier, 1.0)
        readings = [r * factor for r in _readings(raw_number, bool(multiplier))]
        if readings:
            figures.append((match.group().strip(), readings))
    return figures


def extract_figures(text: str) -> list[tuple[str, float]]:
    """Primeira leitura de cada valor (para exibição e testes)."""
    return [(raw, readings[0]) for raw, readings in extract_figure_readings(text)]


def parse_observation(observation: Any) -> dict | None:
    text = getattr(observation, "content", observation)
    if isinstance(text, dict):
        return text
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _numbers(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _numbers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _numbers(item)


def observed_numbers(observations: Iterable[dict]) -> list[float]:
    return [n for obs in observations if obs.get("ok") for n in _numbers(obs.get("data"))]


def is_grounded(value: float, observed: list[float]) -> bool:
    return any(abs(value - v) <= max(TOLERANCE * abs(v), 0.01) for v in observed)


def verify_figures(answer: str, observations: list[dict]) -> tuple[str, list[str]]:
    """Remove frases com valores de mercado não confirmados. Retorna (texto, valores removidos)."""
    observed = observed_numbers(observations)
    kept, removed = [], []
    for sentence in SENTENCE_BOUNDARY.split(answer.strip()):
        bad = [
            raw
            for raw, readings in extract_figure_readings(sentence)
            # Percentual: o sinal costuma vir da palavra ("caiu 1,11%"), então compara o módulo.
            # Limitação: inversão de sentido ("subiu" quando caiu) não é detectada.
            if not any(
                is_grounded(v, observed) or (raw.endswith("%") and is_grounded(-v, observed))
                for v in readings
            )
        ]
        if bad:
            removed.extend(bad)
        else:
            kept.append(sentence)
    return " ".join(kept).strip(), removed


def verify_items(items: list[str], observations: list[dict]) -> tuple[list[str], list[str]]:
    """Verifica itens independentes (pontos-chave, incertezas do LLM); descarta os não confirmados."""
    kept, removed = [], []
    for item in items:
        text, bad = verify_figures(item, observations)
        removed.extend(bad)
        if text and not bad:
            kept.append(text)
    return kept, removed


def ranking(observations: Iterable[dict], field: str) -> list[tuple[str, float]]:
    """Ativos ordenados por ``field`` (maior primeiro), calculado sem LLM."""
    rows = [
        (o["data"]["symbol"], o["data"][field])
        for o in observations
        if o.get("ok") and o.get("tool") == "get_market_data" and o["data"].get(field) is not None
    ]
    return sorted(rows, key=lambda row: row[1], reverse=True)


# --------------------------------------------------------------------------- achados e fontes


def format_usd(value: float | None) -> str:
    if value is None:
        return "n/d"
    decimals = 2 if abs(value) >= 1 else 6
    text = f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"US$ {text}"


def format_pct(value: float | None) -> str:
    return "n/d" if value is None else f"{value:+.2f}%".replace(".", ",")


def _finding(obs: dict, statement: str) -> Finding:
    source = obs.get("source") or {}
    return Finding(
        statement=statement,
        source_tool=obs.get("tool", "?"),
        source_updated_at=source.get("source_updated_at"),
        retrieved_at=source.get("retrieved_at"),
    )


def findings_from(observations: Iterable[dict]) -> list[Finding]:
    findings: list[Finding] = []
    for obs in observations:
        if not obs.get("ok"):
            continue
        data, tool = obs.get("data"), obs.get("tool")
        if tool == "get_market_data":
            name = f"{data['name']} ({data['symbol']})"
            findings += [
                _finding(obs, f"{name}: preço {format_usd(data['price_usd'])}"),
                _finding(obs, f"{name}: variação 24h {format_pct(data.get('percent_change_24h'))}, 7d {format_pct(data.get('percent_change_7d'))}"),
                _finding(obs, f"{name}: market cap {format_usd(data.get('market_cap_usd'))}, volume 24h {format_usd(data.get('volume_24h_usd'))}"),
            ]
        elif tool == "get_market_details":
            profile, ohlcv = data["profile"], data["ohlcv_today_usd"]
            findings.append(_finding(obs, f"{profile['name']}: OHLCV do dia — abertura {format_usd(ohlcv['open'])}, máxima {format_usd(ohlcv['high'])}, mínima {format_usd(ohlcv['low'])}, último {format_usd(ohlcv['close'])}"))
            if profile.get("proof_type"):
                findings.append(_finding(obs, f"{profile['name']}: consenso {profile['proof_type']}"))
        elif tool == "get_global_market":
            findings.append(_finding(obs, f"Mercado global: market cap {format_usd(data['market_cap_usd'])}, volume 24h {format_usd(data['volume_24h_usd'])}, dominância BTC {format_pct(data['bitcoin_dominance_percentage']).lstrip('+')}"))
        elif tool == "search_knowledge_base":
            for hit in data:
                findings.append(_finding(obs, f"Base de conhecimento: '{hit['title']}' ({hit['source']})"))
    return findings


def sources_from(observations: Iterable[dict]) -> list[DataSource]:
    sources, seen = [], set()
    for obs in observations:
        if not obs.get("ok") or obs.get("tool") == "search_crypto_asset":
            continue
        source = obs.get("source") or {}
        key = (obs.get("tool"), source.get("endpoint"), source.get("retrieved_at"))
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            DataSource(
                provider=source.get("provider", "?"),
                tool=obs.get("tool", "?"),
                endpoint=source.get("endpoint"),
                source_updated_at=source.get("source_updated_at"),
                retrieved_at=source.get("retrieved_at"),
            )
        )
    return sources
