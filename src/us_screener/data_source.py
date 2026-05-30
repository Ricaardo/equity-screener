"""Free, futu-independent US data source (bulk snapshots).

The slow part of the free path used to be deriving each snapshot from a per-symbol
akshare history call (~1 symbol/sec → hours for the whole market). Sina's ``gb_``
quote endpoint returns last price, OHLC, volume, turnover, market cap and PE for
*hundreds of symbols in a single request*, so the whole US universe localizes in
a minute or two instead of hours — which is what makes a daily pre-market job
practical.

Universe comes from the free Nasdaq Trader directory (via the core
``fetch_us_security_master`` when Futu is disabled); per-symbol daily history
(for technicals) still uses akshare and is scoped to the liquid top-N by the
pipeline. SEC-derived valuation (see ``valuation_enrich``) fills PB and
cross-checks market cap.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from time import sleep
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

SINA_QUOTE_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537.36",
}

# Field indices in the Sina ``gb_`` (US) quote payload (verified against live data):
# 0 name, 1 last, 2 pct, 3 datetime, 4 change, 5 open, 6 high, 7 low, 8 52w-high,
# 9 52w-low, 10 volume, 12 market_cap, 13 eps, 14 pe, 19 shares, 30 amount(turnover).
_F_LAST, _F_PCT, _F_DATETIME = 1, 2, 3
_F_OPEN, _F_HIGH, _F_LOW = 5, 6, 7
_F_VOLUME, _F_MARKET_CAP, _F_PE, _F_AMOUNT = 10, 12, 14, 30

_SNAPSHOT_COLUMNS = [
    "market",
    "symbol",
    "asset_type",
    "board",
    "trade_date",
    "name",
    "last_price",
    "pct_change",
    "volume",
    "amount",
    "turnover_rate",
    "pe_ttm",
    "pb",
    "market_cap",
    "source",
    "updated_at",
]
_SECURITIES_COLUMNS = [
    "market",
    "symbol",
    "name",
    "asset_type",
    "board",
    "exchange",
    "currency",
    "status",
    "is_st",
    "is_hk_connect",
    "metadata_source",
    "metadata_confidence",
    "updated_at",
]


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number) or number == 0:
        return None
    return number


def _sina_code(symbol: str) -> str:
    return "gb_" + symbol.strip().lower().replace(".", "_").replace("-", "_")


def fetch_sina_quotes(
    symbols: list[str], *, batch: int = 160, pause: float = 0.12, timeout: int = 20
) -> pd.DataFrame:
    """Bulk US quotes from Sina. One request per ``batch`` symbols.

    Returns a DataFrame with last_price / pct_change / open / high / low / volume /
    amount / market_cap / pe_ttm / trade_date, keyed by ``symbol``. Failed batches
    are logged and skipped (free source, may be flaky) — never raises.
    """
    import requests

    code_to_symbol: dict[str, str] = {}
    for raw in symbols:
        symbol = str(raw).strip().upper()
        if symbol:
            code_to_symbol[_sina_code(symbol)] = symbol
    codes = list(code_to_symbol)

    rows: list[dict[str, Any]] = []
    for start in range(0, len(codes), batch):
        chunk = codes[start : start + batch]
        try:
            response = requests.get(SINA_QUOTE_URL + ",".join(chunk), headers=SINA_HEADERS, timeout=timeout)
            response.encoding = "gbk"
            text = response.text
        except Exception as exc:  # noqa: BLE001 — flaky free source
            logger.warning("Sina quote batch %d failed: %s", start // batch, exc)
            if pause:
                sleep(pause)
            continue
        for line in text.strip().split("\n"):
            if "hq_str_" not in line or '="' not in line:
                continue
            code = line.split("hq_str_", 1)[1].split("=", 1)[0].strip()
            symbol = code_to_symbol.get(code)
            if not symbol:
                continue
            payload = line.split('="', 1)[1].rstrip().rstrip(";").strip('"')
            fields = payload.split(",")
            if len(fields) <= _F_AMOUNT:
                continue
            last = _num(fields[_F_LAST])
            if last is None:
                continue
            volume = _num(fields[_F_VOLUME])
            amount = _num(fields[_F_AMOUNT])
            if amount is None and volume is not None:
                amount = last * volume
            trade_date = pd.to_datetime(fields[_F_DATETIME], errors="coerce")
            rows.append(
                {
                    "symbol": symbol,
                    "last_price": last,
                    "pct_change": _num(fields[_F_PCT]),
                    "open": _num(fields[_F_OPEN]),
                    "high": _num(fields[_F_HIGH]),
                    "low": _num(fields[_F_LOW]),
                    "volume": volume,
                    "amount": amount,
                    "market_cap": _num(fields[_F_MARKET_CAP]),
                    "pe_ttm": _num(fields[_F_PE]),
                    "trade_date": trade_date,
                }
            )
        if pause:
            sleep(pause)
    return pd.DataFrame(rows)


def localize_us_universe_free(
    store, *, include_etf: bool = True, batch: int = 160, pause: float = 0.12
) -> dict[str, Any]:
    """Localize the full US universe + bulk snapshots into the store (no Futu).

    securities <- free Nasdaq directory; market_snapshots <- Sina bulk quotes.
    """
    from ah_screener.sources.us_client import fetch_us_security_master

    master = fetch_us_security_master()
    if master.empty:
        return {"securities": 0, "snapshots": 0, "quotes": 0}
    master = master.drop_duplicates(["market", "symbol"]).copy()
    if not include_etf and "asset_type" in master.columns:
        master = master[master["asset_type"].fillna("stock").astype(str).str.lower() != "etf"]

    now = pd.Timestamp(datetime.now())
    securities = master.reindex(columns=_SECURITIES_COLUMNS)
    securities["updated_at"] = now
    securities_written = store.upsert_dataframe("securities", securities)

    quotes = fetch_sina_quotes(master["symbol"].tolist(), batch=batch, pause=pause)
    if quotes.empty:
        return {"securities": securities_written, "snapshots": 0, "quotes": 0}

    meta = master.drop_duplicates("symbol").set_index("symbol")
    snap = quotes.copy()
    snap["market"] = "US"
    snap["name"] = snap["symbol"].map(lambda s: str(meta.at[s, "name"]) if s in meta.index else s)
    snap["asset_type"] = snap["symbol"].map(
        lambda s: str(meta.at[s, "asset_type"]) if s in meta.index else "stock"
    )
    snap["board"] = snap["symbol"].map(
        lambda s: str(meta.at[s, "board"]) if s in meta.index else ""
    )
    snap["trade_date"] = snap["trade_date"].fillna(now).dt.date
    snap["turnover_rate"] = pd.NA
    snap["pb"] = pd.NA
    snap["source"] = "sina.gb"
    snap["updated_at"] = now
    snapshots_written = store.upsert_dataframe("market_snapshots", snap.reindex(columns=_SNAPSHOT_COLUMNS))
    return {
        "securities": securities_written,
        "snapshots": snapshots_written,
        "quotes": int(len(quotes)),
    }


def _alpaca_credentials() -> tuple[str, str] | None:
    import os

    key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
    secret = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
    return (key, secret) if key and secret else None


def localize_us_history_alpaca(
    store, symbols: list[str], *, lookback_days: int = 420, batch: int = 200
) -> dict[str, Any]:
    """Bulk daily history via Alpaca: one request returns bars for *many* symbols
    (IEX free feed), so the whole universe localizes in ~tens of requests instead
    of thousands of per-symbol calls. Returns ``status='skipped'`` if alpaca-py or
    credentials are absent (caller falls back to the parallel akshare path).
    """
    creds = _alpaca_credentials()
    if creds is None:
        return {"status": "skipped", "reason": "no alpaca credentials", "rows": 0}
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        return {"status": "skipped", "reason": "alpaca-py not installed", "rows": 0}

    wanted = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    if not wanted:
        return {"status": "ok", "symbols_ok": 0, "rows": 0}

    client = StockHistoricalDataClient(*creds)
    start = datetime.now() - timedelta(days=lookback_days)
    now = pd.Timestamp(datetime.now())
    frames: list[pd.DataFrame] = []
    symbols_ok: set[str] = set()
    failed_batches = 0
    for offset in range(0, len(wanted), batch):
        chunk = wanted[offset : offset + batch]
        try:
            request = StockBarsRequest(
                symbol_or_symbols=chunk,
                timeframe=TimeFrame.Day,
                start=start,
                feed=DataFeed.IEX,
            )
            frame = client.get_stock_bars(request).df
        except Exception as exc:  # noqa: BLE001 — degrade to akshare fallback
            logger.warning("alpaca bars batch %d failed: %s", offset // batch, exc)
            failed_batches += 1
            continue
        if frame is None or frame.empty:
            continue
        frame = frame.reset_index()  # columns: symbol, timestamp, open, high, low, close, volume, vwap...
        out = pd.DataFrame(
            {
                "market": "US",
                "symbol": frame["symbol"].astype(str).str.upper(),
                "trade_date": pd.to_datetime(frame["timestamp"], utc=True).dt.tz_localize(None).dt.normalize(),
                "open": pd.to_numeric(frame["open"], errors="coerce"),
                "high": pd.to_numeric(frame["high"], errors="coerce"),
                "low": pd.to_numeric(frame["low"], errors="coerce"),
                "close": pd.to_numeric(frame["close"], errors="coerce"),
                "volume": pd.to_numeric(frame["volume"], errors="coerce"),
            }
        )
        out["amount"] = out["close"] * out["volume"]
        out["adj_type"] = "raw"
        out["source"] = "alpaca.iex"
        out["updated_at"] = now
        frames.append(out)
        symbols_ok.update(out["symbol"].unique().tolist())

    if not frames:
        return {"status": "ok", "symbols_ok": 0, "rows": 0, "failed_batches": failed_batches}
    written = store.upsert_dataframe("daily_prices", pd.concat(frames, ignore_index=True))
    return {
        "status": "ok",
        "symbols_ok": len(symbols_ok),
        "rows": int(written),
        "failed_batches": failed_batches,
    }


def localize_us_history_free(
    store, symbols: list[str], *, lookback_days: int = 420, max_workers: int = 8
) -> dict[str, Any]:
    """Parallel per-symbol daily history (akshare, futu disabled) into daily_prices.

    The per-symbol fetches are independent HTTP calls, so a small thread pool gives
    a ~6x speedup (measured) over the sequential core path with no rate-limit issues.
    """
    from ah_screener.sources.us_client import fetch_us_history

    end = datetime.now()
    start_date = (end - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end_date = end.strftime("%Y%m%d")
    wanted = [str(s).strip().upper() for s in symbols if str(s).strip()]

    def _one(symbol: str):
        try:
            history = fetch_us_history(symbol, start_date=start_date, end_date=end_date)
            return symbol, history
        except Exception as exc:  # noqa: BLE001 — per-symbol free source may fail
            logger.debug("history fetch failed for %s: %s", symbol, exc)
            return symbol, None

    frames: list[pd.DataFrame] = []
    ok = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        for _symbol, history in pool.map(_one, wanted):
            if history is None or history.empty:
                failed += 1
                continue
            frames.append(history)
            ok += 1

    if not frames:
        return {"symbols_ok": 0, "symbols_failed": failed, "rows": 0}
    written = store.upsert_dataframe("daily_prices", pd.concat(frames, ignore_index=True))
    return {"symbols_ok": ok, "symbols_failed": failed, "rows": int(written)}
