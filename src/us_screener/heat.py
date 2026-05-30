"""US symbol heat scoring (0-100) from price and liquidity behaviour.

Heat is intentionally simple and fully offline: it reads existing US
``daily_prices`` plus latest ``market_snapshots`` and scores symbols on
relative volume, short-term return, trend confirmation, 52-week-high proximity
and liquidity. The output is ephemeral and does not require schema changes.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ah_screener.scoring import _rank_score


HEAT_COLUMNS = ["market", "symbol", "heat_score", "heat_components"]


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


def _series(frame: pd.DataFrame, column: str, dtype: str = "float") -> pd.Series:
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


def compute_heat_scores(store) -> pd.DataFrame:
    """Compute per-US-symbol heat scores from stored snapshots and daily prices."""
    snapshots = store.query_df(
        """
        SELECT market, symbol, trade_date, last_price, amount, asset_type
        FROM market_snapshots
        WHERE market = 'US' AND COALESCE(asset_type, 'stock') <> 'etf'
        """
    )
    if snapshots.empty:
        return pd.DataFrame(columns=HEAT_COLUMNS)

    snapshots = snapshots.copy()
    snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"], errors="coerce")
    latest = snapshots.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
    latest = latest[["market", "symbol", "last_price", "amount"]].copy()

    daily = store.query_df(
        """
        SELECT market, symbol, trade_date, close, high, volume, adj_type
        FROM daily_prices
        WHERE market = 'US'
        """
    )
    metrics: list[dict[str, object]] = []
    if not daily.empty:
        daily = daily.copy()
        if "adj_type" in daily.columns:
            daily = daily[
                ~daily["adj_type"].fillna("").astype(str).str.lower().eq("benchmark")
            ]
        daily["trade_date"] = pd.to_datetime(daily["trade_date"], errors="coerce")
        for (market, symbol), group in daily.groupby(["market", "symbol"]):
            prices = group.sort_values(["trade_date"]).drop_duplicates("trade_date", keep="last")
            if prices.empty:
                continue
            close = pd.to_numeric(prices["close"], errors="coerce")
            high = pd.to_numeric(prices["high"], errors="coerce")
            volume = pd.to_numeric(prices["volume"], errors="coerce")
            latest_close = close.iloc[-1] if len(close) else np.nan
            prev_avg_volume = volume.shift(1).rolling(20).mean().iloc[-1] if len(volume) >= 21 else np.nan
            rvol = np.nan
            if pd.notna(prev_avg_volume) and prev_avg_volume > 0 and pd.notna(volume.iloc[-1]):
                rvol = float(volume.iloc[-1] / prev_avg_volume)
            ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else np.nan
            ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else np.nan
            ret20 = (
                float(latest_close / close.shift(20).iloc[-1] - 1)
                if len(close) > 20 and pd.notna(close.shift(20).iloc[-1]) and close.shift(20).iloc[-1] > 0
                else np.nan
            )
            high_52w = high.tail(252).max() if len(high.dropna()) >= 60 else np.nan
            proximity = (
                float(latest_close / high_52w)
                if pd.notna(latest_close) and pd.notna(high_52w) and high_52w > 0
                else np.nan
            )
            metrics.append(
                {
                    "market": market,
                    "symbol": symbol,
                    "latest_close": latest_close,
                    "rvol": rvol,
                    "return_20d": ret20,
                    "above_ma20": latest_close > ma20
                    if pd.notna(ma20) and pd.notna(latest_close)
                    else pd.NA,
                    "above_ma50": latest_close > ma50
                    if pd.notna(ma50) and pd.notna(latest_close)
                    else pd.NA,
                    "proximity_52w_high": proximity,
                }
            )

    metric_df = pd.DataFrame(metrics)
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
