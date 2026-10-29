"""Cliente HTTP da CoinPaprika (API v1, https://api.coinpaprika.com/).

Endpoints usados (plano gratuito):
- ``GET /search/``                 identificar ativos por nome, símbolo ou id;
- ``GET /tickers/{coin_id}``       preço, volume, market cap, variações, ATH;
- ``GET /coins/{coin_id}/ohlcv/today``  OHLCV do dia corrente (UTC);
- ``GET /coins/{coin_id}``         perfil do ativo (tipo, tags, início);
- ``GET /global``                  visão geral do mercado.

Histórico de OHLCV (``/ohlcv/historical``) é bloqueado no plano gratuito (HTTP 402) e não é usado.

Regra central: nenhuma função devolve dado inventado. Falhas viram ``CoinPaprikaError``
com ``kind`` explícito; cada resultado carrega ``source`` com o endpoint, o horário de
atualização informado pela fonte (quando existe) e o horário da consulta.
"""

import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import CoinPaprikaSettings, get_coinpaprika_settings

logger = logging.getLogger(__name__)

PROVIDER = "CoinPaprika"
COIN_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")

ErrorKind = Literal[
    "invalid_argument",
    "not_found",
    "rate_limited",
    "plan_restricted",
    "timeout",
    "network",
    "http_error",
    "malformed",
]


class CoinPaprikaError(Exception):
    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        status_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.retry_after_s = retry_after_s


# --------------------------------------------------------------------------- modelos


class SourceInfo(BaseModel):
    provider: str = PROVIDER
    endpoint: str
    source_updated_at: datetime | None = Field(
        default=None, description="Horário de atualização informado pela fonte."
    )
    retrieved_at: datetime = Field(description="Horário (UTC) em que a consulta foi feita.")


class AssetMatch(BaseModel):
    id: str
    name: str
    symbol: str
    rank: int | None = None
    is_active: bool | None = None


class MarketTicker(BaseModel):
    id: str
    name: str
    symbol: str
    rank: int | None
    price_usd: float
    volume_24h_usd: float | None
    market_cap_usd: float | None
    percent_change_1h: float | None
    percent_change_24h: float | None
    percent_change_7d: float | None
    percent_change_30d: float | None
    ath_price_usd: float | None
    ath_date: datetime | None
    percent_from_ath: float | None
    circulating_supply: float | None = None
    max_supply: float | None = None
    source: SourceInfo


class OhlcvToday(BaseModel):
    coin_id: str
    time_open: datetime
    time_close: datetime
    open: float
    high: float
    low: float
    close: float
    volume_usd: float | None
    market_cap_usd: float | None
    source: SourceInfo


class CoinProfile(BaseModel):
    id: str
    name: str
    symbol: str
    type: str | None = None
    is_active: bool | None = None
    started_at: datetime | None = None
    development_status: str | None = None
    proof_type: str | None = None
    tags: list[str] = []
    source: SourceInfo


class GlobalMarket(BaseModel):
    market_cap_usd: float
    volume_24h_usd: float
    bitcoin_dominance_percentage: float
    cryptocurrencies_number: int | None
    market_cap_ath_usd: float | None
    source: SourceInfo


# --------------------------------------------------------------------------- cliente


def validate_coin_id(coin_id: str) -> str:
    coin_id = coin_id.strip().lower()
    if not COIN_ID_PATTERN.match(coin_id):
        raise CoinPaprikaError(
            "invalid_argument",
            f"id de ativo inválido: {coin_id!r} (formato esperado: 'btc-bitcoin')",
        )
    return coin_id


def _now() -> datetime:
    return datetime.now(UTC)


class CoinPaprikaClient:
    def __init__(
        self,
        settings: CoinPaprikaSettings | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or get_coinpaprika_settings()
        headers = {"Accept": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = self.settings.api_key
        self._http = httpx.Client(
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_s,
            headers=headers,
            transport=transport,
            follow_redirects=True,
        )
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ HTTP base

    def _get(self, path: str, params: dict | None = None) -> tuple[Any, datetime]:
        """GET com retries para falhas transitórias (timeout, rede, 429 curto, 5xx)."""
        last_error: CoinPaprikaError | None = None
        for attempt in range(self.settings.max_retries + 1):
            started = time.perf_counter()
            retrieved_at = _now()
            try:
                response = self._http.get(path, params=params)
            except httpx.TimeoutException as exc:
                last_error = CoinPaprikaError("timeout", f"timeout em {path}: {exc}")
            except httpx.TransportError as exc:
                last_error = CoinPaprikaError("network", f"falha de rede em {path}: {exc}")
            else:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.info(
                    "coinpaprika request path=%s status=%s attempt=%d duration_ms=%.0f",
                    path, response.status_code, attempt + 1, elapsed_ms,
                )
                outcome = self._handle_response(path, response, retrieved_at)
                if not isinstance(outcome, CoinPaprikaError):
                    return outcome
                last_error = outcome
                if outcome.kind not in {"rate_limited", "http_error"} or (
                    outcome.kind == "http_error" and (outcome.status_code or 0) < 500
                ):
                    raise outcome
                if outcome.kind == "rate_limited" and (
                    outcome.retry_after_s is None
                    or outcome.retry_after_s > self.settings.max_retry_wait_s
                ):
                    raise outcome

            if attempt < self.settings.max_retries:
                wait = (
                    last_error.retry_after_s
                    if last_error.retry_after_s is not None
                    else 0.5 * 2**attempt
                )
                logger.warning(
                    "coinpaprika retry path=%s kind=%s wait_s=%.1f", path, last_error.kind, wait
                )
                self._sleep(wait)
        assert last_error is not None
        logger.error("coinpaprika failed path=%s kind=%s", path, last_error.kind)
        raise last_error

    @staticmethod
    def _handle_response(
        path: str, response: httpx.Response, retrieved_at: datetime
    ) -> tuple[Any, datetime] | CoinPaprikaError:
        status = response.status_code
        if status == 200:
            try:
                return response.json(), retrieved_at
            except ValueError:
                return CoinPaprikaError("malformed", f"resposta não é JSON em {path}", status)
        detail = response.text[:200]
        if status == 404:
            return CoinPaprikaError("not_found", f"não encontrado: {path}", status)
        if status == 402:
            return CoinPaprikaError(
                "plan_restricted", f"recurso indisponível no plano atual: {detail}", status
            )
        if status == 429:
            retry_after = response.headers.get("retry-after")
            return CoinPaprikaError(
                "rate_limited",
                "limite de requisições da CoinPaprika atingido",
                status,
                retry_after_s=float(retry_after) if retry_after else None,
            )
        return CoinPaprikaError("http_error", f"HTTP {status} em {path}: {detail}", status)

    @staticmethod
    def _parse(model: type[BaseModel], payload: dict, path: str) -> Any:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise CoinPaprikaError("malformed", f"resposta inesperada em {path}: {exc}") from exc

    # ------------------------------------------------------------------ endpoints

    def search_assets(self, query: str, limit: int = 5) -> list[AssetMatch]:
        query = query.strip()
        if not 1 <= len(query) <= 60:
            raise CoinPaprikaError("invalid_argument", "a busca deve ter de 1 a 60 caracteres")
        data, _ = self._get("/search/", {"q": query, "c": "currencies", "limit": limit})
        if not isinstance(data, dict) or not isinstance(data.get("currencies"), list):
            raise CoinPaprikaError("malformed", "resposta de /search/ sem 'currencies'")
        return [self._parse(AssetMatch, item, "/search/") for item in data["currencies"]]

    def resolve_asset(self, query: str) -> AssetMatch:
        """Resolve nome, símbolo ou id para um único ativo.

        Preferência: id exato > símbolo exato > nome exato, desempatando pelo rank.
        """
        candidates = self.search_assets(query, limit=10)
        needle = query.strip().lower()

        def best(matches: list[AssetMatch]) -> AssetMatch | None:
            ranked = sorted(matches, key=lambda m: (m.rank is None or m.rank == 0, m.rank or 0))
            return ranked[0] if ranked else None

        for predicate in (
            lambda m: m.id == needle,
            lambda m: m.symbol.lower() == needle,
            lambda m: m.name.lower() == needle,
        ):
            if match := best([m for m in candidates if predicate(m)]):
                return match
        if candidates:
            return best(candidates)  # type: ignore[return-value]
        raise CoinPaprikaError("not_found", f"nenhum ativo encontrado para {query!r}")

    def get_ticker(self, coin_id: str) -> MarketTicker:
        coin_id = validate_coin_id(coin_id)
        path = f"/tickers/{coin_id}"
        data, retrieved_at = self._get(path, {"quotes": "USD"})
        try:
            usd = data["quotes"]["USD"]
            payload = {
                "id": data["id"],
                "name": data["name"],
                "symbol": data["symbol"],
                "rank": data.get("rank"),
                "price_usd": usd["price"],
                "volume_24h_usd": usd.get("volume_24h"),
                "market_cap_usd": usd.get("market_cap"),
                "percent_change_1h": usd.get("percent_change_1h"),
                "percent_change_24h": usd.get("percent_change_24h"),
                "percent_change_7d": usd.get("percent_change_7d"),
                # Plano gratuito devolve 0 fixo em 30d/1y (verificado em 2026-10-28 para BTC, ETH
                # e SOL): é preenchimento, não dado. 0.0 exato vira "indisponível".
                "percent_change_30d": usd.get("percent_change_30d") or None,
                "ath_price_usd": usd.get("ath_price"),
                "ath_date": usd.get("ath_date"),
                "percent_from_ath": usd.get("percent_from_price_ath"),
                "circulating_supply": data.get("circulating_supply"),
                "max_supply": data.get("max_supply") or None,
                "source": SourceInfo(
                    endpoint=path,
                    source_updated_at=data.get("last_updated"),
                    retrieved_at=retrieved_at,
                ),
            }
        except (KeyError, TypeError) as exc:
            raise CoinPaprikaError("malformed", f"resposta inesperada em {path}: {exc!r}") from exc
        return self._parse(MarketTicker, payload, path)

    def get_ohlcv_today(self, coin_id: str) -> OhlcvToday:
        coin_id = validate_coin_id(coin_id)
        path = f"/coins/{coin_id}/ohlcv/today"
        data, retrieved_at = self._get(path)
        if not isinstance(data, list) or not data:
            raise CoinPaprikaError("malformed", f"OHLCV do dia vazio em {path}")
        row = data[0]
        payload = {
            "coin_id": coin_id,
            **{k: row.get(k) for k in ("time_open", "time_close", "open", "high", "low", "close")},
            "volume_usd": row.get("volume"),
            "market_cap_usd": row.get("market_cap"),
            "source": SourceInfo(
                endpoint=path, source_updated_at=row.get("time_close"), retrieved_at=retrieved_at
            ),
        }
        return self._parse(OhlcvToday, payload, path)

    def get_coin_profile(self, coin_id: str) -> CoinProfile:
        coin_id = validate_coin_id(coin_id)
        path = f"/coins/{coin_id}"
        data, retrieved_at = self._get(path)
        if not isinstance(data, dict):
            raise CoinPaprikaError("malformed", f"resposta inesperada em {path}")
        payload = {
            **{k: data.get(k) for k in ("id", "name", "symbol", "type", "is_active")},
            "started_at": data.get("started_at"),
            "development_status": data.get("development_status"),
            "proof_type": data.get("proof_type"),
            "tags": [t.get("name") for t in data.get("tags") or [] if t.get("name")][:10],
            "source": SourceInfo(endpoint=path, retrieved_at=retrieved_at),
        }
        return self._parse(CoinProfile, payload, path)

    def get_global(self) -> GlobalMarket:
        path = "/global"
        data, retrieved_at = self._get(path)
        if not isinstance(data, dict):
            raise CoinPaprikaError("malformed", "resposta inesperada em /global")
        updated = data.get("last_updated")
        payload = {
            "market_cap_usd": data.get("market_cap_usd"),
            "volume_24h_usd": data.get("volume_24h_usd"),
            "bitcoin_dominance_percentage": data.get("bitcoin_dominance_percentage"),
            "cryptocurrencies_number": data.get("cryptocurrencies_number"),
            "market_cap_ath_usd": data.get("market_cap_ath_value"),
            "source": SourceInfo(
                endpoint=path,
                source_updated_at=datetime.fromtimestamp(updated, UTC) if updated else None,
                retrieved_at=retrieved_at,
            ),
        }
        return self._parse(GlobalMarket, payload, path)
