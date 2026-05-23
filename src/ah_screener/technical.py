from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ah_screener.etf_model import classify_etf

# Money-market and bond ETFs do not trend like equities; applying the stock
# trend/momentum bands to them produces misleading scores, so they get a neutral
# technical_score instead. Equity / sector / theme / cross-border / commodity ETFs
# trend like stocks and reuse the stock scorer. See docs/master-plan.md R11.
_NEUTRAL_ETF_CATEGORIES = {"货币ETF", "债券ETF"}


def _etf_score_profile(name: object) -> str:
    return "neutral" if classify_etf(name)[0] in _NEUTRAL_ETF_CATEGORIES else "equity"


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _score_row(row: pd.Series) -> tuple[float, float, float, str]:
    close = row["close"]
    ma20 = row["ma20"]
    ma60 = row["ma60"]
    ma120 = row["ma120"]
    ret20 = row["return_20d"]
    ret60 = row["return_60d"]
    pct_high = row["pct_from_120d_high"]
    rsi14 = row["rsi14"]

    trend = 0.0
    if pd.notna(ma20) and close > ma20:
        trend += 20
    if pd.notna(ma60) and ma20 > ma60:
        trend += 20
    if pd.notna(ma120) and ma60 > ma120:
        trend += 20
    if pd.notna(pct_high) and pct_high >= -0.10:
        trend += 15
    if pd.notna(ret60) and ret60 > 0:
        trend += 15
    if pd.notna(rsi14) and 45 <= rsi14 <= 72:
        trend += 10

    momentum = 0.0
    if pd.notna(ret20):
        if 0.03 <= ret20 <= 0.25:
            momentum += 35
        elif ret20 > 0:
            momentum += 22
    if pd.notna(ret60):
        if 0.05 <= ret60 <= 0.45:
            momentum += 35
        elif ret60 > 0:
            momentum += 20
    if pd.notna(pct_high) and -0.12 <= pct_high <= 0.03:
        momentum += 20
    if pd.notna(rsi14):
        if 50 <= rsi14 <= 68:
            momentum += 10
        elif rsi14 > 78:
            momentum -= 15

    technical = float(np.clip(trend * 0.55 + momentum * 0.45, 0, 100))
    if technical >= 72:
        signal = "strong_trend"
    elif technical >= 58:
        signal = "constructive"
    elif technical >= 45:
        signal = "neutral"
    else:
        signal = "weak"
    return float(np.clip(trend, 0, 100)), float(np.clip(momentum, 0, 100)), technical, signal


def compute_technical_indicators(daily_prices: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    if daily_prices.empty:
        return pd.DataFrame()
    if "adj_type" in daily_prices.columns:
        daily_prices = daily_prices[
            ~daily_prices["adj_type"].fillna("").astype(str).str.lower().eq("benchmark")
        ]
        if daily_prices.empty:
            return pd.DataFrame()

    latest_snapshot_date = snapshots["trade_date"].max() if not snapshots.empty else pd.Timestamp.today()
    meta = (
        snapshots.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
        if not snapshots.empty
        else snapshots
    )
    names = (
        meta.set_index(["market", "symbol"])["name"] if not snapshots.empty else pd.Series(dtype=object)
    )
    asset_types = (
        meta.set_index(["market", "symbol"])["asset_type"]
        if (not snapshots.empty and "asset_type" in meta.columns)
        else pd.Series(dtype=object)
    )

    rows: list[dict[str, object]] = []
    for (market, symbol), group in daily_prices.groupby(["market", "symbol"]):
        prices = (
            group.sort_values(["trade_date", "source"])
            .drop_duplicates("trade_date", keep="last")
            .dropna(subset=["close"])
            .copy()
        )
        name = names.get((market, symbol))
        is_etf = str(asset_types.get((market, symbol), "stock") or "stock").lower() == "etf"
        if len(prices) < 60:
            # Stocks keep prior behaviour (skipped). Newly-listed / thin ETFs still
            # surface with a neutral, flagged score rather than vanishing (R12).
            if is_etf:
                rows.append(_insufficient_history_row(latest_snapshot_date, market, symbol, name, prices))
            continue
        close = pd.to_numeric(prices["close"], errors="coerce")
        high = pd.to_numeric(prices["high"], errors="coerce")
        returns = close.pct_change()
        latest = prices.iloc[-1]
        row = {
            "snapshot_date": latest_snapshot_date,
            "market": market,
            "symbol": symbol,
            "name": name if name is not None else latest.get("name"),
            "close": float(close.iloc[-1]),
            "ma20": float(close.rolling(20).mean().iloc[-1]),
            "ma60": float(close.rolling(60).mean().iloc[-1]),
            "ma120": float(close.rolling(120).mean().iloc[-1]) if len(close) >= 120 else np.nan,
            "return_20d": float(close.iloc[-1] / close.shift(20).iloc[-1] - 1)
            if len(close) > 20 and close.shift(20).iloc[-1] > 0
            else np.nan,
            "return_60d": float(close.iloc[-1] / close.shift(60).iloc[-1] - 1)
            if len(close) > 60 and close.shift(60).iloc[-1] > 0
            else np.nan,
            "pct_from_120d_high": float(close.iloc[-1] / high.rolling(120).max().iloc[-1] - 1)
            if len(high) >= 120 and high.rolling(120).max().iloc[-1] > 0
            else np.nan,
            "rsi14": float(_rsi(close).iloc[-1]),
            "volatility_20d": float(returns.rolling(20).std().iloc[-1] * np.sqrt(252)),
        }
        if is_etf and _etf_score_profile(name) == "neutral":
            # Money/bond ETFs: keep the indicators, but a neutral score (R11).
            row["trend_score"] = np.nan
            row["momentum_score"] = np.nan
            row["technical_score"] = 50.0
            row["technical_signal"] = "工具型"
        else:
            trend, momentum, technical, signal = _score_row(pd.Series(row))
            row["trend_score"] = trend
            row["momentum_score"] = momentum
            row["technical_score"] = technical
            row["technical_signal"] = signal
        row["updated_at"] = pd.Timestamp(datetime.now())
        rows.append(row)

    return pd.DataFrame(rows)


def _insufficient_history_row(
    snapshot_date: object, market: object, symbol: object, name: object, prices: pd.DataFrame
) -> dict[str, object]:
    """Neutral, flagged technical row for an ETF without enough history (R12)."""
    close = pd.to_numeric(prices["close"], errors="coerce") if not prices.empty else pd.Series(dtype=float)
    return {
        "snapshot_date": snapshot_date,
        "market": market,
        "symbol": symbol,
        "name": name,
        "close": float(close.iloc[-1]) if len(close) else np.nan,
        "ma20": np.nan,
        "ma60": np.nan,
        "ma120": np.nan,
        "return_20d": np.nan,
        "return_60d": np.nan,
        "pct_from_120d_high": np.nan,
        "rsi14": np.nan,
        "volatility_20d": np.nan,
        "trend_score": np.nan,
        "momentum_score": np.nan,
        "technical_score": 50.0,
        "technical_signal": "数据不足",
        "updated_at": pd.Timestamp(datetime.now()),
    }
