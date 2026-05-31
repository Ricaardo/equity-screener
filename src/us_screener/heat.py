"""US symbol heat scoring (0-100) from price and liquidity behaviour.

Heat is intentionally simple and fully offline: it reads existing US
``daily_prices`` plus latest ``market_snapshots`` and scores symbols on
relative volume, short-term return, trend confirmation, 52-week-high proximity
and liquidity. The output is ephemeral and does not require schema changes.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from ah_screener.scoring import _rank_score


HEAT_COLUMNS = ["market", "symbol", "heat_score", "heat_components"]


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


def _num(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _score_bool(value: object) -> float:
    if pd.isna(value):
        return 50.0
    return 100.0 if bool(value) else 0.0


def _series(frame: pd.DataFrame, column: str, dtype: Any = "float") -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series(index=frame.index, dtype=dtype)


def _build_components(row: pd.Series) -> dict[str, Any]:
    return {
        "rvol": _num(row.get("rvol")),
        "return_20d": _num(row.get("return_20d")),
        "above_ma20": None if pd.isna(row.get("above_ma20")) else bool(row.get("above_ma20")),
        "above_ma50": None if pd.isna(row.get("above_ma50")) else bool(row.get("above_ma50")),
        "proximity_52w_high": _num(row.get("proximity_52w_high")),
        "amount": _num(row.get("amount")),
        "scores": {
            "rvol": round(float(row.get("rvol_score", 50.0) or 50.0), 2),
            "return_20d": round(float(row.get("return_20d_score", 50.0) or 50.0), 2),
            "above_ma20": round(float(row.get("above_ma20_score", 50.0) or 50.0), 2),
            "above_ma50": round(float(row.get("above_ma50_score", 50.0) or 50.0), 2),
            "proximity_52w_high": round(
                float(row.get("proximity_52w_high_score", 50.0) or 50.0), 2
            ),
            "liquidity": round(float(row.get("liquidity_score", 50.0) or 50.0), 2),
        },
    }


def compute_heat_scores(
    store,
    *,
    symbols: Iterable[str] | None = None,
    lookback_days: int = 420,
) -> pd.DataFrame:
    """Compute per-US-symbol heat scores from stored snapshots and daily prices.

    ``symbols`` is an optional pre-filter used by the daily screener: low-liquidity
    names are still retained in the final audit frame, but they do not need an
    expensive per-symbol price/volume pass.
    """
    symbol_set = _normalize_symbols(symbols)
    symbol_sql, symbol_params = _symbol_filter(symbol_set)
    snapshots = store.query_df(
        f"""
        SELECT market, symbol, trade_date, last_price, amount, asset_type
        FROM market_snapshots
        WHERE market = 'US' AND COALESCE(asset_type, 'stock') <> 'etf'
        {symbol_sql}
        """,
        symbol_params,
    )
    if snapshots.empty:
        return pd.DataFrame(columns=HEAT_COLUMNS)

    snapshots = snapshots.copy()
    snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"], errors="coerce")
    latest = snapshots.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
    latest = latest[["market", "symbol", "last_price", "amount"]].copy()

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
    metric_df = store.query_df(
        f"""
        WITH filtered AS (
            SELECT market, symbol, trade_date, close, high, volume, source, updated_at
            FROM daily_prices
            WHERE market = 'US'
              AND COALESCE(LOWER(adj_type), '') <> 'benchmark'
              {symbol_sql}
              {date_sql}
        ),
        dedup AS (
            SELECT market, symbol, trade_date, close, high, volume
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
                close,
                volume,
                AVG(volume) OVER (
                    PARTITION BY market, symbol
                    ORDER BY trade_date
                    ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                ) AS prev_avg_volume_20,
                AVG(close) OVER (
                    PARTITION BY market, symbol
                    ORDER BY trade_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS ma20,
                AVG(close) OVER (
                    PARTITION BY market, symbol
                    ORDER BY trade_date
                    ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                ) AS ma50,
                LAG(close, 20) OVER (
                    PARTITION BY market, symbol
                    ORDER BY trade_date
                ) AS close_20d_ago,
                MAX(high) OVER (
                    PARTITION BY market, symbol
                    ORDER BY trade_date
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                ) AS high_52w,
                COUNT(high) OVER (
                    PARTITION BY market, symbol
                    ORDER BY trade_date
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                ) AS high_52w_count
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
        SELECT
            market,
            symbol,
            close AS latest_close,
            volume / NULLIF(prev_avg_volume_20, 0) AS rvol,
            close / NULLIF(close_20d_ago, 0) - 1 AS return_20d,
            CASE WHEN ma20 IS NULL OR close IS NULL THEN NULL ELSE close > ma20 END AS above_ma20,
            CASE WHEN ma50 IS NULL OR close IS NULL THEN NULL ELSE close > ma50 END AS above_ma50,
            CASE
                WHEN high_52w_count >= 60 AND high_52w > 0 AND close IS NOT NULL
                THEN close / high_52w
                ELSE NULL
            END AS proximity_52w_high
        FROM latest
        WHERE latest_rank = 1
        """,
        params,
    )

    merged = latest.merge(metric_df, on=["market", "symbol"], how="left")
    merged["rvol_score"] = _rank_score(_series(merged, "rvol"), ascending=True)
    merged["return_20d_score"] = _rank_score(_series(merged, "return_20d"), ascending=True)
    merged["above_ma20_score"] = _series(merged, "above_ma20", dtype=object).map(_score_bool)
    merged["above_ma50_score"] = _series(merged, "above_ma50", dtype=object).map(_score_bool)
    merged["proximity_52w_high_score"] = _rank_score(
        _series(merged, "proximity_52w_high"), ascending=True
    )
    amount = pd.to_numeric(_series(merged, "amount"), errors="coerce")
    merged["liquidity_score"] = _rank_score(np.log10(amount.where(amount > 0)), ascending=True)

    merged["heat_score"] = (
        merged["rvol_score"].fillna(50.0) * 0.30
        + merged["return_20d_score"].fillna(50.0) * 0.20
        + merged["above_ma20_score"].fillna(50.0) * 0.10
        + merged["above_ma50_score"].fillna(50.0) * 0.10
        + merged["proximity_52w_high_score"].fillna(50.0) * 0.15
        + merged["liquidity_score"].fillna(50.0) * 0.15
    ).clip(0, 100)
    merged["heat_components"] = merged.apply(_build_components, axis=1)

    out = merged[["market", "symbol", "heat_score", "heat_components"]].copy()
    out["heat_score"] = pd.to_numeric(out["heat_score"], errors="coerce").fillna(50.0).round(2)
    return out.sort_values(["heat_score", "symbol"], ascending=[False, True]).reset_index(drop=True)
