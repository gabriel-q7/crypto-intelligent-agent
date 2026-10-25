"""Verificação real da integração com a CoinPaprika (usada por GET /health/coinpaprika)."""

from collections.abc import Callable
from typing import Any

from app.tools.coinpaprika import CoinPaprikaClient, CoinPaprikaError


def _check(name: str, call: Callable[[], Any], expect_error: str | None = None) -> dict:
    try:
        detail = call()
    except CoinPaprikaError as exc:
        ok = expect_error is not None and exc.kind == expect_error
        return {"check": name, "ok": ok, "error_kind": exc.kind, "detail": str(exc)}
    if expect_error:
        return {"check": name, "ok": False, "detail": f"esperava erro {expect_error}"}
    return {"check": name, "ok": True, "detail": detail}


def run_checks(client: CoinPaprikaClient, asset: str = "btc-bitcoin") -> dict:
    def ticker() -> dict:
        t = client.get_ticker(asset)
        return {"price_usd": t.price_usd, "source_updated_at": t.source.source_updated_at, "retrieved_at": t.source.retrieved_at}

    def ohlcv() -> dict:
        o = client.get_ohlcv_today(asset)
        return {"close": o.close, "time_open": o.time_open, "time_close": o.time_close}

    def profile() -> dict:
        p = client.get_coin_profile(asset)
        return {"name": p.name, "proof_type": p.proof_type}

    def global_market() -> dict:
        g = client.get_global()
        return {"bitcoin_dominance_percentage": g.bitcoin_dominance_percentage, "source_updated_at": g.source.source_updated_at}

    checks = [
        _check("search", lambda: client.resolve_asset("BTC").id),
        _check("ticker", ticker),
        _check("ohlcv_today", ohlcv),
        _check("profile", profile),
        _check("global", global_market),
        _check("missing_asset_is_not_found", lambda: client.get_ticker("xyz-ativo-inexistente"), "not_found"),
        _check("invalid_id_rejected", lambda: client.get_ticker("BTC!"), "invalid_argument"),
    ]
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
