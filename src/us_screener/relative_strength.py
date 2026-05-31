"""Relative strength vs the market — price momentum measured against a benchmark.

Absolute momentum (in ``heat``) says "this went up". Relative strength says "this
went up *more than the market*", which is the cleaner read on what the market is
collectively expecting — leadership shows up in RS before it shows up in headlines.
Computed as multi-window excess return over a benchmark ETF (SPY), ranked 0-100.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ah_screener.scoring import _rank_score

RS_COLUMNS = ["market", "symbol", "rs_score", "rs_components"]
# trading-day windows (~1m / 3m / 6m) and their weights
_WINDOWS = {"r1m": 21, "r3m": 63, "r6m": 126}
_WEIGHTS = {"r1m": 0.45, "r3m": 0.35, "r6m": 0.20}


def _window_return(group: pd.Series, window: int) -> float | None:
    close = pd.to_numeric(group, errors="coerce").dropna()
    if len(close) <= window:
        return None
    base = close.iloc[-1 - window]
    last = close.iloc[-1]
    if base and base > 0:
        return float(last / base - 1.0)
    return None


def _benchmark_returns(daily: pd.DataFrame, benchmark: str) -> dict[str, float]:
    bench = daily[daily["symbol"].astype(str).str.upper() == benchmark.upper()]
    if bench.empty:
        return {}
    close = bench.sort_values("trade_date")["close"]
    out: dict[str, float] = {}
    for key, win in _WINDOWS.items():
        ret = _window_return(close, win)
        if ret is not None:
            out[key] = ret
    return out


def compute_rs_scores(store, *, benchmark: str = "SPY") -> pd.DataFrame:
    """Per-symbol relative-strength score (0-100) vs ``benchmark`` excess return."""
    daily = store.query_df(
        "SELECT market, symbol, trade_date, close FROM daily_prices WHERE market = 'US'"
    )
    if daily.empty:
        return pd.DataFrame(columns=RS_COLUMNS)
    daily = daily.copy()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], errors="coerce")
    daily["close"] = pd.to_numeric(daily["close"], errors="coerce")

    bench = _benchmark_returns(daily, benchmark)
    daily = daily.sort_values(["symbol", "trade_date"])

    rows: list[dict[str, Any]] = []
    for symbol, group in daily.groupby("symbol"):
        excess: dict[str, float] = {}
        for key, win in _WINDOWS.items():
            sym_ret = _window_return(group["close"], win)
            if sym_ret is None:
                continue
            excess[key] = sym_ret - bench.get(key, 0.0)
        if not excess:
            continue
        weight = sum(_WEIGHTS[k] for k in excess)
        rs_raw = sum(excess[k] * _WEIGHTS[k] for k in excess) / weight
        rows.append(
            {
                "market": "US",
                "symbol": str(symbol).strip().upper(),
                "rs_raw": rs_raw,
                "rs_components": {k: round(v * 100, 2) for k, v in excess.items()},
            }
        )
    if not rows:
        return pd.DataFrame(columns=RS_COLUMNS)

    frame = pd.DataFrame(rows)
    # Rank excess return across the universe -> 0-100 (higher = stronger leadership).
    frame["rs_score"] = _rank_score(frame["rs_raw"], ascending=True).fillna(50.0).clip(0, 100).round(2)
    return frame[RS_COLUMNS]
