"""Ferramentas de mercado (CoinPaprika) no formato aceito pelo agente ReAct clássico.

``ZERO_SHOT_REACT_DESCRIPTION`` só aceita ferramentas de **uma entrada em texto**; por isso
cada ferramenta recebe uma string, normaliza o que o modelo escreveu ("BTC", '"bitcoin"',
"asset=eth") e valida com Pydantic antes de chamar a API.

Toda ferramenta devolve JSON com ``ok``. Em caso de falha, ``ok=false`` e ``error.kind``
dizem o que aconteceu — nunca há valor de mercado substituto.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from langchain_core.tools import Tool
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.tools.coinpaprika import COIN_ID_PATTERN, HISTORY_MAX_DAYS, CoinPaprikaClient, CoinPaprikaError

HISTORY_DEFAULT_DAYS = 7
# "SOL, 7", "sol-solana 30", "ETH": ativo e, opcionalmente, número de dias no fim.
HISTORY_INPUT = re.compile(r"^(?P<asset>.+?)(?:[\s,;|]+(?P<days>\d{1,3}))?(?:\s*dias?)?$", re.IGNORECASE)
INPUT_PREFIX = re.compile(r"^(?:query|asset|ativo|id|input|symbol|q)\s*[:=]\s*", re.IGNORECASE)


class AssetInput(BaseModel):
    """Argumento das ferramentas: nome, símbolo ou id CoinPaprika de um ativo."""

    value: str = Field(min_length=1, max_length=60)

    @field_validator("value", mode="before")
    @classmethod
    def _clean(cls, raw: object) -> object:
        if not isinstance(raw, str):
            return raw
        text = raw.strip().splitlines()[0] if raw.strip() else ""
        text = INPUT_PREFIX.sub("", text).strip().strip("'\"`").strip()
        return text


def _round(value: Any) -> Any:
    """Reduz casas decimais para caber no contexto do modelo sem perder a ordem de grandeza."""
    if isinstance(value, float):
        return round(value, 2) if abs(value) >= 1 else float(f"{value:.6g}")
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


def ok_observation(tool: str, data: Any, source: dict | None) -> dict:
    return {"ok": True, "tool": tool, "data": _round(data), "source": source}


def error_observation(tool: str, kind: str, message: str) -> dict:
    return {
        "ok": False,
        "tool": tool,
        "error": {"kind": kind, "message": message},
        "instruction": "Não invente valores: informe ao usuário que este dado não foi obtido.",
    }


def to_json(observation: dict) -> str:
    return json.dumps(observation, ensure_ascii=False, default=str)


def _ok(tool: str, data: Any, source: dict | None) -> str:
    return to_json(ok_observation(tool, data, source))


def _error(tool: str, kind: str, message: str) -> str:
    return to_json(error_observation(tool, kind, message))


def observe_ticker(client: CoinPaprikaClient, coin_id: str) -> dict:
    """Observação no mesmo formato da ferramenta get_market_data (usada também pelas chains)."""
    try:
        ticker = client.get_ticker(coin_id)
    except CoinPaprikaError as exc:
        return error_observation("get_market_data", exc.kind, str(exc))
    return ok_observation(
        "get_market_data",
        ticker.model_dump(mode="json", exclude={"source"}),
        ticker.source.model_dump(mode="json"),
    )


def observe_global(client: CoinPaprikaClient) -> dict:
    try:
        market = client.get_global()
    except CoinPaprikaError as exc:
        return error_observation("get_global_market", exc.kind, str(exc))
    return ok_observation(
        "get_global_market",
        market.model_dump(mode="json", exclude={"source"}),
        market.source.model_dump(mode="json"),
    )


def _guarded(tool: str, fn: Callable[[str], str]) -> Callable[[str], str]:
    """Converte qualquer falha em JSON de erro: a ferramenta nunca quebra o agente."""

    def run(raw: str) -> str:
        try:
            value = AssetInput(value=raw).value
        except ValidationError as exc:
            return _error(tool, "invalid_argument", f"entrada inválida {raw!r}: {exc.errors()[0]['msg']}")
        try:
            return fn(value)
        except CoinPaprikaError as exc:
            return _error(tool, exc.kind, str(exc))

    return run


def parse_history_input(raw: str) -> tuple[str, int]:
    """Texto da ferramenta de histórico → (ativo, dias). Sem número, usa a última semana."""
    text = AssetInput(value=raw).value
    match = HISTORY_INPUT.match(text)
    asset = match.group("asset").strip().strip(",;|") if match else text
    days = int(match.group("days")) if match and match.group("days") else HISTORY_DEFAULT_DAYS
    return asset, days


def build_market_tools(client: CoinPaprikaClient | None = None, profile: bool = False) -> list[Tool]:
    """``profile=True`` acrescenta ``get_asset_profile`` e ``get_price_history`` (só o agente integrado da Fase 5 usa)."""
    client = client or CoinPaprikaClient()

    def coin_id_for(value: str) -> str:
        lowered = value.lower()
        return lowered if COIN_ID_PATTERN.match(lowered) else client.resolve_asset(value).id

    def search(value: str) -> str:
        matches = client.search_assets(value, limit=5)
        if not matches:
            return _error("search_crypto_asset", "not_found", f"nenhum ativo para {value!r}")
        return _ok("search_crypto_asset", [m.model_dump() for m in matches], {"provider": "CoinPaprika", "endpoint": "/search/"})

    def market_data(value: str) -> str:
        return to_json(observe_ticker(client, coin_id_for(value)))

    def market_details(value: str) -> str:
        coin_id = coin_id_for(value)
        profile = client.get_coin_profile(coin_id)
        ohlcv = client.get_ohlcv_today(coin_id)
        data = {
            "profile": profile.model_dump(mode="json", exclude={"source"}),
            "ohlcv_today_usd": ohlcv.model_dump(mode="json", exclude={"source"}),
        }
        return _ok("get_market_details", data, ohlcv.source.model_dump(mode="json"))

    def asset_profile(value: str) -> str:
        info = client.get_coin_profile(coin_id_for(value))
        return _ok("get_asset_profile", info.model_dump(mode="json", exclude={"source"}),
                   info.source.model_dump(mode="json"))

    def price_history(raw: str) -> str:
        tool = "get_price_history"
        try:
            asset, days = parse_history_input(raw)
        except ValidationError as exc:
            return _error(tool, "invalid_argument", f"entrada inválida {raw!r}: {exc.errors()[0]['msg']}")
        if not 1 <= days <= HISTORY_MAX_DAYS:
            return _error(tool, "invalid_argument", f"período deve ter de 1 a {HISTORY_MAX_DAYS} dias (pedido: {days})")
        try:
            coin_id = coin_id_for(asset)
            history = client.get_price_history(coin_id, days)
        except CoinPaprikaError as exc:
            return _error(tool, exc.kind, str(exc))
        data = {
            "coin_id": coin_id,
            "symbol": coin_id.split("-")[0].upper(),
            "days": days,
            "interval": history.interval,
            # Só data e preço: com volume e market cap junto, o modelo pequeno embaralhava os dígitos (2026-11-13).
            "points": [{"date": p.timestamp.date().isoformat(), "price_usd": p.price_usd} for p in history.points],
        }
        return _ok(tool, data, history.source.model_dump(mode="json"))

    def global_guarded(_: str) -> str:
        # Entrada ignorada: aceita qualquer texto (o modelo às vezes escreve "none" ou "").
        return to_json(observe_global(client))

    tools = [
        Tool(
            name="search_crypto_asset",
            func=_guarded("search_crypto_asset", search),
            description=(
                "Identifica criptoativos na CoinPaprika. Entrada: nome ou símbolo (ex.: 'solana', 'BTC'). "
                "Retorna id, nome, símbolo e rank."
            ),
        ),
        Tool(
            name="get_market_data",
            func=_guarded("get_market_data", market_data),
            description=(
                "Dados de mercado ATUAIS de UM ativo: preço em USD, volume 24h, market cap, "
                "variações 1h/24h/7d/30d e ATH, com horário de atualização. "
                "Entrada: símbolo, nome ou id (ex.: 'BTC', 'ethereum', 'sol-solana')."
            ),
        ),
        Tool(
            name="get_market_details",
            func=_guarded("get_market_details", market_details),
            description=(
                "Métricas adicionais de UM ativo: OHLCV do dia (abertura, máxima, mínima, fechamento) "
                "e perfil (tipo, consenso, data de início, tags). Entrada: símbolo, nome ou id."
            ),
        ),
        Tool(
            name="get_global_market",
            func=global_guarded,
            description=(
                "Visão geral ATUAL do mercado cripto: market cap total, volume 24h e dominância do BTC. "
                "Entrada: escreva 'global'."
            ),
        ),
    ]
    if profile:
        tools.append(Tool(
            name="get_asset_profile",
            func=_guarded("get_asset_profile", asset_profile),
            description=(
                "Perfil técnico de UM ativo na CoinPaprika: consenso, código aberto, "
                "descrição, whitepaper, site e repositório. Sem preços. Entrada: símbolo, nome ou id."
            ),
        ))
        tools.append(Tool(
            name="get_price_history",
            func=price_history,
            description=(
                "Histórico de preço de UM ativo na CoinPaprika: preço em USD por dia (00:00 UTC) "
                f"nos últimos N dias (até {HISTORY_MAX_DAYS}; acima de 31, por semana). "
                "Entrada: 'ativo, dias' (ex.: 'SOL, 7')."
            ),
        ))
    return tools
