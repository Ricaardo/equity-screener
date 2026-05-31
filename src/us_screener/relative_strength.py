"""Relative strength vs the market — price momentum measured against a benchmark.

Absolute momentum (in ``heat``) says "this went up". Relative strength says "this
went up *more than the market*", which is the cleaner read on what the market is
collectively expecting — leadership shows up in RS before it shows up in headlines.
Computed as multi-window excess return over a benchmark ETF (SPY), ranked 0-100.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from ah_screener.scoring import _rank_score

RS_COLUMNS = ["market", "symbol", "rs_score", "rs_components"]
# trading-day windows (~1m / 3m / 6m) and their weights
_WINDOWS = {"r1m": 21, "r3m": 63, "r6m": 126}
_WEIGHTS = {"r1m": 0.45, "r3m": 0.35, "r6m": 0.20}


def _normalize_symbols(symbols: Iterable[str] | None) -> set[str] | None:
    if symbols is None:
        return None
    return {str(symbol).strip().upper() for symbol in symbols if str(symbol or "").strip()}


def _symbol_filter(symbols: set[str] | None) -> tuple[str, list[str]]:
    if symbols is None:
        return "", []
    if not symbols:
        return " AND 1 = 0", []
    ordered = sorted(symbols)
    placeholders = ", ".join(["?"] * len(ordered))
    return f" AND UPPER(symbol) IN ({placeholders})", ordered


def compute_rs_scores(
    store,
    *,
    benchmark: str = "SPY",
    symbols: Iterable[str] | None = None,
    lookback_days: int = 420,
) -> pd.DataFrame:
    """Per-symbol relative-strength score (0-100) vs ``benchmark`` excess return."""
    requested_symbols = _normalize_symbols(symbols)
    query_symbols = None
    if requested_symbols is not None:
        query_symbols = set(requested_symbols)
        query_symbols.add(benchmark.strip().upper())
    symbol_sql, symbol_params = _symbol_filter(query_symbols)
    date_sql = ""
    params: list[object] = list(symbol_params)
    if lookback_days and lookback_days > 0:
        date_sql = """
        AND trade_date >= (
            SELECT MAX(trade_date) - (? * INTERVAL '1 day')
            FROM daily_prices
            WHERE market = 'US'
        )
        """
        params.append(int(lookback_days))
    frame = store.query_df(
        f"""
        WITH filtered AS (
            SELECT market, symbol, trade_date, close, source, updated_at
            FROM daily_prices
            WHERE market = 'US'
              {symbol_sql}
              {date_sql}
        ),
        dedup AS (
            SELECT market, symbol, trade_date, close
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY market, symbol, trade_date
                        ORDER BY updated_at DESC NULLS LAST, source DESC NULLS LAST
                    ) AS date_rank
                FROM filtered
            )
            WHERE date_rank = 1
        ),
        features AS (
            SELECT
                market,
                symbol,
                trade_date,
                close / NULLIF(
                    LAG(close, 21) OVER (PARTITION BY market, symbol ORDER BY trade_date),
                    0
                ) - 1 AS r1m,
                close / NULLIF(
                    LAG(close, 63) OVER (PARTITION BY market, symbol ORDER BY trade_date),
                    0
                ) - 1 AS r3m,
                close / NULLIF(
                    LAG(close, 126) OVER (PARTITION BY market, symbol ORDER BY trade_date),
                    0
                ) - 1 AS r6m
            FROM dedup
        ),
        latest AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY market, symbol
                    ORDER BY trade_date DESC
                ) AS latest_rank
            FROM features
        )
        SELECT market, symbol, r1m, r3m, r6m
        FROM latest
        WHERE latest_rank = 1
        """,
        params,
    )
    if frame.empty:
        return pd.DataFrame(columns=RS_COLUMNS)
    frame = frame.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()

    bench_row = frame[frame["symbol"].eq(benchmark.strip().upper())]
    bench = {
        key: float(bench_row.iloc[0][key])
        for key in _WINDOWS
        if not bench_row.empty and pd.notna(bench_row.iloc[0][key])
    }
    if requested_symbols is not None:
        frame = frame[frame["symbol"].isin(requested_symbols)]
    if frame.empty:
        return pd.DataFrame(columns=RS_COLUMNS)

    excess = pd.DataFrame(index=frame.index)
    for key in _WINDOWS:
        excess[key] = pd.to_numeric(frame[key], errors="coerce") - bench.get(key, 0.0)
    weights = pd.Series(_WEIGHTS)
    weight_sum = excess.notna().mul(weights, axis=1).sum(axis=1)
    valid = weight_sum > 0
    if not valid.any():
        return pd.DataFrame(columns=RS_COLUMNS)

    frame = frame.loc[valid, ["market", "symbol"]].copy()
    excess = excess.loc[valid]
    frame["rs_raw"] = excess.fillna(0.0).mul(weights, axis=1).sum(axis=1) / weight_sum.loc[valid]
    frame["rs_components"] = [
        {key: round(float(row[key]) * 100, 2) for key in _WINDOWS if pd.notna(row[key])}
        for _, row in excess.iterrows()
    ]
    # Rank excess return across the universe -> 0-100 (higher = stronger leadership).
    frame["rs_score"] = _rank_score(frame["rs_raw"], ascending=True).fillna(50.0).clip(0, 100).round(2)
    return frame[RS_COLUMNS]
