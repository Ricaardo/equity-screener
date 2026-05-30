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
from time import sleep
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

VALUATION_COLUMNS = ("market_cap", "pe_ttm", "pb")

SEC_SHARES_URL = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}"
    "/dei/EntityCommonStockSharesOutstanding.json"
)
_SEC_HEADERS = {"User-Agent": "ah-screener us-screener research championdoggg@gmail.com"}


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


def _sec_shares_outstanding(cik: int) -> float | None:
    """Latest common shares outstanding from the SEC companyconcept endpoint.

    SEC is reliable and (unlike Yahoo) not aggressively rate-limited, so this is
    the primary valuation source. Returns None on any failure.
    """
    import requests

    try:
        response = requests.get(SEC_SHARES_URL.format(cik=int(cik)), headers=_SEC_HEADERS, timeout=20)
        response.raise_for_status()
        units = response.json().get("units", {})
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.debug("SEC shares fetch failed for CIK %s: %s", cik, exc)
        return None
    rows = units.get("shares") or next(iter(units.values()), [])
    best_val = None
    best_end = ""
    for item in rows:
        end = str(item.get("end") or "")
        val = _num(item.get("val"))
        if val is not None and end >= best_end:
            best_end, best_val = end, val
    return best_val


def _latest_metrics_by_symbol(store) -> dict[str, dict[str, float | None]]:
    """Map US symbol -> latest {total_equity, parent_net_profit} from SEC fundamentals."""
    metrics = store.query_df(
        """
        SELECT symbol, report_date, total_equity, parent_net_profit
        FROM financial_metrics WHERE market = 'US'
        """
    )
    if metrics.empty:
        return {}
    metrics["report_date"] = pd.to_datetime(metrics["report_date"], errors="coerce")
    latest = metrics.sort_values("report_date").drop_duplicates("symbol", keep="last")
    out: dict[str, dict[str, float | None]] = {}
    for _, row in latest.iterrows():
        out[str(row["symbol"]).strip().upper()] = {
            "total_equity": _num(row.get("total_equity")),
            "parent_net_profit": _num(row.get("parent_net_profit")),
        }
    return out


def derive_us_valuation_sec(store, *, limit: int = 3000, pause: float = 0.08) -> dict[str, Any]:
    """Primary valuation: market_cap = shares (SEC) x last_price; PE/PB from the
    already-localized equity/net-income. No Yahoo dependency, no hard rate limits.
    """
    from ah_screener.sources.us_client import fetch_sec_company_tickers

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
    if "market_cap" in latest.columns:
        latest = latest[latest["market_cap"].isna()]
    latest = latest.sort_values("amount", ascending=False, na_position="last").head(max(limit, 0))
    if latest.empty:
        return {"status": "ok", "updated": 0, "requested": 0}

    try:
        ticker_map = fetch_sec_company_tickers()
    except Exception as exc:  # noqa: BLE001 — no CIK map => skip cleanly
        return {"status": "skipped", "reason": f"sec ticker map failed: {exc}", "updated": 0}
    metrics = _latest_metrics_by_symbol(store)

    updated_rows: list[pd.Series] = []
    requested = 0
    for _, row in latest.iterrows():
        symbol = str(row["symbol"]).strip().upper()
        meta = ticker_map.get(symbol)
        price = _num(row.get("last_price"))
        if not symbol or meta is None or price is None:
            continue
        requested += 1
        shares = _sec_shares_outstanding(int(meta["cik_str"]))
        if pause:
            sleep(pause)
        if shares is None:
            continue
        market_cap = shares * price
        fundamentals = metrics.get(symbol, {})
        equity = fundamentals.get("total_equity")
        net_income = fundamentals.get("parent_net_profit")
        new_row = row.copy()
        new_row["market_cap"] = market_cap
        if equity and equity > 0:
            new_row["pb"] = market_cap / equity
        if net_income and net_income > 0:
            new_row["pe_ttm"] = market_cap / net_income
        updated_rows.append(new_row)

    if not updated_rows:
        return {"status": "ok", "updated": 0, "requested": requested}
    written = store.upsert_dataframe("market_snapshots", pd.DataFrame(updated_rows))
    return {"status": "ok", "updated": int(written), "requested": requested}


def enrich_us_valuation_all(store, *, sec_limit: int = 3000, yf_limit: int = 400) -> dict[str, Any]:
    """Orchestrate valuation: SEC-derive (primary, reliable) then yfinance top-up
    (secondary, best-effort) for whatever SEC could not fill. Never raises.
    """
    sec = derive_us_valuation_sec(store, limit=sec_limit)
    yfin = enrich_us_valuation(store, limit=yf_limit, only_missing=True)
    return {
        "status": "ok",
        "updated": int(sec.get("updated", 0)) + int(yfin.get("updated", 0)),
        "sec": sec,
        "yfinance": yfin,
    }
