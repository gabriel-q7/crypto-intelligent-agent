"""Classificação de intenção e extração de entidades da mensagem atual.

Duas fontes combinadas:
- **regras determinísticas** (pt e en): ativos por alias, períodos, correções, referência a
  resultado anterior, DeFi, comparação, pergunta conceitual e marcadores de continuação;
- **LLM em modo JSON** (``ExtractedRequest``), com o contexto ativo e as últimas mensagens.

O que a regra detecta explicitamente prevalece sobre o LLM: na Fase 2 o modelo pequeno
errou ordenações e classificações que regras simples acertam. O LLM cobre o que as regras
não reconhecem (ex.: ativo fora da lista de aliases, intenção em frase incomum).

Só são extraídas entidades da mensagem ATUAL; herdar do contexto é papel do resolver.
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from pydantic import ValidationError

from app.intent.schemas import ConversationIntent, ExtractedRequest
from app.services.llm import with_transient_retry

logger = logging.getLogger(__name__)

ASSET_ALIASES: dict[str, str] = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "ether": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "cardano": "ADA", "ada": "ADA",
    "xrp": "XRP", "ripple": "XRP",
    "dogecoin": "DOGE", "doge": "DOGE",
    "bnb": "BNB", "binance coin": "BNB",
    "avalanche": "AVAX", "avax": "AVAX",
    "polkadot": "DOT", "dot": "DOT",
    "chainlink": "LINK", "link": "LINK",
    "litecoin": "LTC", "ltc": "LTC",
    "tron": "TRX", "trx": "TRX",
    "polygon": "POL", "matic": "POL", "pol": "POL",
    "tether": "USDT", "usdt": "USDT",
    "usdc": "USDC", "usd coin": "USDC",
    "dai": "DAI",
    "aave": "AAVE", "uniswap": "UNI", "uni": "UNI",
}
PROTOCOL_NAMES = {"aave", "uniswap", "lido", "makerdao", "maker", "curve", "compound", "pancakeswap", "gmx"}
NETWORK_NAMES = {"ethereum", "solana", "arbitrum", "optimism", "base", "polygon", "bsc", "avalanche", "tron"}
METRIC_PATTERNS = {
    "price": r"\b(preco|cotacao|price|vale|valor)\b",
    "volume": r"\bvolume\b",
    "market_cap": r"\b(market ?cap|capitalizacao)\b",
    "tvl": r"\btvl\b",
    "volatility": r"\b(volatilidade|volatility|risco|risk)\b",
    "change": r"\b(variacao|variou|change|desempenho|performance|subiu|caiu)\b",
}

_UNITS = {"dia": 1, "dias": 1, "day": 1, "days": 1, "semana": 7, "semanas": 7, "week": 7, "weeks": 7,
          "mes": 30, "meses": 30, "month": 30, "months": 30, "ano": 365, "anos": 365, "year": 365, "years": 365}
PERIOD = re.compile(r"\b(\d{1,4})\s*(dias?|days?|semanas?|weeks?|mes(?:es)?|months?|anos?|years?)\b")
PERIOD_SHORT = re.compile(r"\b(24\s*h(?:oras)?|24\s*hours?|7d|30d|90d)\b")
CORRECTION = re.compile(
    r"\b(quis dizer|eu quis|na verdade|corrigindo|correcao|errei|me enganei|i meant|actually|correction)\b"
    r"|\b(nao|not)\s+\d+\b"
)
PREVIOUS_REF = re.compile(
    r"(resultado anterior|analise anterior|ultima analise|resultado de antes|comparacao anterior|com o anterior"
    r"|previous (result|analysis|comparison)|earlier (result|analysis)|last (result|analysis)|o que (voce )?(fez|mostrou) antes)"
)
DEFI = re.compile(r"\b(defi|tvl|protocolos?|liquidez|pools?|yield|lending|emprestimos?|dex)\b")
CONCEPT = re.compile(r"^\s*(o que (e|sao|significa)|como funciona|explique|defina|qual a diferenca|what is|what are|how does|explain|define)\b")
COMPARE = re.compile(r"\b(compar\w*|versus|vs\.?|melhor que|better than)\b|\s+x\s+")
HISTORICAL = re.compile(r"\b(historico|historical|evolucao|evoluiu|desde|since|serie|tendencia|trend)\b")
ANAPHORA = re.compile(r"\b(eles|elas|os dois|as duas|ambos|ambas|esses|essas|them|both|these|those)\b")
FOLLOW_UP = re.compile(
    r"^\s*(e\s|e quanto|e o\b|e a\b|e os\b|e as\b|considere|consider|what about|how about|and\s|agora|now\s|mas\s|but\s|tambem|also)"
)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def find_assets(text: str) -> list[str]:
    """Ativos citados na mensagem, na ordem em que aparecem (aliases conhecidos)."""
    norm = normalize(text)
    found: list[tuple[int, str]] = []
    for alias, symbol in ASSET_ALIASES.items():
        for match in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", norm):
            # "sol"/"link"/"dot"/"uni" são palavras comuns: só contam em maiúsculas no texto original.
            if alias in {"sol", "link", "dot", "uni", "pol", "dai"} and not re.search(rf"\b{alias.upper()}\b", text):
                continue
            found.append((match.start(), symbol))
    ordered = [symbol for _, symbol in sorted(found)]
    return list(dict.fromkeys(ordered))


def find_period(text: str) -> tuple[str | None, int | None]:
    norm = normalize(text)
    if match := PERIOD.search(norm):
        amount, unit = int(match.group(1)), match.group(2)
        days = amount * _UNITS[unit]
        return f"{amount} {unit}", days
    if match := PERIOD_SHORT.search(norm):
        token = match.group(1).replace(" ", "")
        days = {"7d": 7, "30d": 30, "90d": 90}.get(token, 1)
        return ("24 horas" if days == 1 else f"{days} dias"), days
    return None, None


@dataclass
class RuleSignals:
    assets: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    period: str | None = None
    period_days: int | None = None
    correction: bool = False
    previous_reference: bool = False
    defi: bool = False
    concept: bool = False
    comparison: bool = False
    historical: bool = False
    follow_up_marker: bool = False


def rule_signals(message: str) -> RuleSignals:
    norm = normalize(message)
    period, days = find_period(message)
    words = set(re.findall(r"[a-z]+", norm))
    return RuleSignals(
        assets=find_assets(message),
        protocols=sorted(PROTOCOL_NAMES & words),
        networks=sorted(NETWORK_NAMES & words - {"ethereum", "solana", "avalanche", "polygon"} if find_assets(message) else NETWORK_NAMES & words),
        metrics=[name for name, pattern in METRIC_PATTERNS.items() if re.search(pattern, norm)],
        period=period,
        period_days=days,
        correction=bool(CORRECTION.search(norm)),
        previous_reference=bool(PREVIOUS_REF.search(norm)),
        defi=bool(DEFI.search(norm)) or bool(PROTOCOL_NAMES & words),
        concept=bool(CONCEPT.search(norm)),
        comparison=bool(COMPARE.search(norm)),
        historical=bool(HISTORICAL.search(norm)),
        follow_up_marker=bool(FOLLOW_UP.search(norm)),
    )


def rule_intent(signals: RuleSignals, has_active_context: bool) -> ConversationIntent | None:
    """Intenção decidida só por regras; ``None`` quando as regras não são conclusivas."""
    if signals.previous_reference:
        return ConversationIntent.PREVIOUS_RESULT_REFERENCE
    if signals.correction and has_active_context:
        return ConversationIntent.FOLLOW_UP
    if signals.concept and not signals.period:
        return ConversationIntent.GENERAL_RESEARCH
    if signals.defi:
        return ConversationIntent.DEFI_ANALYSIS
    if len(signals.assets) >= 2 or (signals.comparison and signals.assets):
        return ConversationIntent.ASSET_COMPARISON
    if signals.assets and (signals.historical or (signals.period_days or 0) > 7):
        return ConversationIntent.HISTORICAL_ANALYSIS
    if signals.assets:
        return ConversationIntent.MARKET_ANALYSIS
    if has_active_context and (signals.follow_up_marker or signals.period or signals.metrics):
        return ConversationIntent.FOLLOW_UP
    # Sem contexto ativo: continuação explícita ou só período → follow_up (o resolver avisa que
    # não há o que continuar); métrica de mercado sem ativo → mercado (o resolver pede o ativo).
    if signals.follow_up_marker or (signals.period and not signals.metrics):
        return ConversationIntent.FOLLOW_UP
    if set(signals.metrics) & {"price", "volume", "market_cap", "change"}:
        return ConversationIntent.MARKET_ANALYSIS
    return None


EXTRACTION_HINT = """Classifique a MENSAGEM ATUAL do usuário numa conversa sobre criptoativos.
Responda SOMENTE com JSON:
{"intent": "<market_analysis|defi_analysis|asset_comparison|historical_analysis|follow_up|previous_result_reference|general_research>",
 "assets": ["símbolos citados NA MENSAGEM ATUAL"], "protocols": [], "networks": [], "metrics": [],
 "period": "período citado na mensagem ou null", "missing_information": [], "assumptions": []}
Regras: follow_up = continua o pedido anterior sem repetir os ativos; previous_result_reference = cita um resultado
já mostrado; general_research = conceito sem dado atual. Não copie ativos do contexto para "assets"."""


def llm_extract(llm: BaseChatModel, message: str, context: str) -> ExtractedRequest | None:
    human = f"CONTEXTO (não extraia entidades daqui):\n{context or '(vazio)'}\n\nMENSAGEM ATUAL:\n{message}"
    try:
        reply = with_transient_retry(llm).invoke([SystemMessage(content=EXTRACTION_HINT), HumanMessage(content=human)])
        return ExtractedRequest.model_validate(JsonOutputParser().invoke(reply))
    except (ValidationError, ValueError) as exc:
        logger.warning("intent llm invalid, using rules only: %s", exc)
        return None


def classify_message(
    message: str,
    llm: BaseChatModel | None,
    active_context: dict | None = None,
    recent_context: str = "",
) -> ExtractedRequest:
    """Extração final: regras prevalecem; o LLM preenche o que as regras não cobrem."""
    signals = rule_signals(message)
    has_active = bool(active_context and active_context.get("assets"))
    intent = rule_intent(signals, has_active)
    llm_result = None
    if llm is not None and (intent is None or not signals.assets):
        context = json.dumps(active_context or {}, ensure_ascii=False) + ("\n" + recent_context if recent_context else "")
        llm_result = llm_extract(llm, message, context)

    norm = normalize(message)
    # Do LLM, só ativos que aparecem literalmente na mensagem (evita herdar do contexto por engano).
    llm_assets = [a for a in (llm_result.assets if llm_result else []) if normalize(a) in norm]
    assets = list(dict.fromkeys(signals.assets + llm_assets))
    if intent is None:
        intent = llm_result.intent if llm_result else (
            ConversationIntent.FOLLOW_UP if has_active else ConversationIntent.GENERAL_RESEARCH
        )
        if intent == ConversationIntent.FOLLOW_UP and not has_active:
            intent = ConversationIntent.MARKET_ANALYSIS if assets else ConversationIntent.GENERAL_RESEARCH

    return ExtractedRequest(
        intent=intent,
        assets=assets,
        protocols=list(dict.fromkeys(signals.protocols + (llm_result.protocols if llm_result else []))),
        networks=signals.networks,
        metrics=list(dict.fromkeys(signals.metrics + (llm_result.metrics if llm_result else []))),
        period=signals.period,
        period_days=signals.period_days,
        is_correction=signals.correction,
        refers_to_active=bool(ANAPHORA.search(norm)),
        requires_external_data=intent not in {ConversationIntent.GENERAL_RESEARCH},
        missing_information=llm_result.missing_information if llm_result else [],
        assumptions=llm_result.assumptions if llm_result else [],
    )
