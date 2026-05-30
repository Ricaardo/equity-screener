"""Best-effort valuation enrichment for the free (futu-independent) US path.

The free data path (akshare history + Nasdaq directory) yields price/volume but
not ``market_cap`` / ``pe_ttm`` / ``pb`` — those were Futu-only. This module fills
them into the latest US snapshots via yfinance, ordered by turnover so the most
tradeable names are valued first.

It is deliberately resilient: yfinance is imported lazily, and when Yahoo
rate-limits (very common from shared IPs) we stop early and report ``rate_limited``
rather than raising. The screen then still runs — valuation simply stays neutral
for the un-enriched tail instead of the whole pipeline failing.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

VALUATION_COLUMNS = ("market_cap", "pe_ttm", "pb")


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number) or number == 0:
        return None
    return number


def _is_rate_limit(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "ratelimit" in name or "too many requests" in str(exc).lower()


def _fetch_one(symbol: str) -> dict[str, float | None]:
    """Fetch market_cap / trailing PE / PB for one symbol. Raises on rate limit."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    market_cap = None
    fast = getattr(ticker, "fast_info", None)
    if fast is not None:
        try:
            market_cap = _num(fast.get("market_cap") if hasattr(fast, "get") else fast["market_cap"])
        except (KeyError, AttributeError, TypeError):
            market_cap = None
    pe = pb = None
    try:
        info = ticker.info or {}
    except Exception as exc:  # noqa: BLE001 — .info rate-limits separately; keep fast_info mcap
        if _is_rate_limit(exc):
            raise
        info = {}
    pe = _num(info.get("trailingPE"))
    pb = _num(info.get("priceToBook"))
    if market_cap is None:
        market_cap = _num(info.get("marketCap"))
    return {"market_cap": market_cap, "pe_ttm": pe, "pb": pb}


def enrich_us_valuation(store, *, limit: int = 600, only_missing: bool = True) -> dict[str, Any]:
    """Fill valuation fields on the latest US stock snapshots via yfinance.

    Returns a status dict; never raises for missing deps or rate limits.
    """
    try:
        import yfinance  # noqa: F401
    except ImportError:
        return {"status": "skipped", "reason": "yfinance not installed", "updated": 0}

    snapshots = store.query_df(
        """
        SELECT * FROM market_snapshots
        WHERE market = 'US' AND COALESCE(asset_type, 'stock') <> 'etf'
        """
    )
    if snapshots.empty:
        return {"status": "empty", "updated": 0, "requested": 0}

    snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"], errors="coerce")
    latest = snapshots.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
    if only_missing and "market_cap" in latest.columns:
        latest = latest[latest["market_cap"].isna()]
    latest = latest.sort_values("amount", ascending=False, na_position="last").head(max(limit, 0))
    if latest.empty:
        return {"status": "ok", "updated": 0, "requested": 0, "rate_limited": False}

    updated_rows: list[pd.Series] = []
    rate_limited = False
    requested = 0
    for _, row in latest.iterrows():
        symbol = str(row["symbol"]).strip().upper()
        if not symbol:
            continue
        requested += 1
        try:
            values = _fetch_one(symbol)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            if _is_rate_limit(exc):
                rate_limited = True
                logger.warning("yfinance rate-limited at %s; stopping enrichment early", symbol)
                break
            logger.debug("valuation fetch failed for %s: %s", symbol, exc)
            continue
        if all(values[col] is None for col in VALUATION_COLUMNS):
            continue
        new_row = row.copy()
        for col in VALUATION_COLUMNS:
            if values[col] is not None:
                new_row[col] = values[col]
        updated_rows.append(new_row)

    if not updated_rows:
        return {"status": "ok", "updated": 0, "requested": requested, "rate_limited": rate_limited}

    payload = pd.DataFrame(updated_rows)
    written = store.upsert_dataframe("market_snapshots", payload)
    return {
        "status": "ok",
        "updated": int(written),
        "requested": requested,
        "rate_limited": rate_limited,
    }
