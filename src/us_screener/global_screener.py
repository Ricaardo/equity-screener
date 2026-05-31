"""Price-only technical screener for the banked global history (HK / JP / UK ...).

``global_history.duckdb`` holds stooq daily bars for non-US markets but no
fundamentals / snapshots / macro, so those markets can only be screened on what the
price series supports: trend, momentum, 52-week proximity, RSI and relative volume.
This reuses ``ah_screener.technical.compute_technical_indicators`` (which works with
no snapshot metadata) and adds a relative-volume heat measure, then ranks.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_OUT_FIELDS = [
    "market", "symbol", "close", "composite_score", "technical_score",
    "trend_score", "momentum_score", "return_20d", "return_60d",
    "pct_from_120d_high", "rsi14", "rvol", "amount", "trade_date",
]


def _rvol(daily_prices: pd.DataFrame) -> pd.Series:
    """Latest volume / 20-day average volume, per (market, symbol)."""
    df = daily_prices.sort_values("trade_date")
    out: dict[tuple[str, str], float] = {}
    for (market, symbol), group in df.groupby(["market", "symbol"]):
        vol = pd.to_numeric(group["volume"], errors="coerce").dropna()
        if len(vol) < 20:
            continue
        avg = vol.iloc[-20:].mean()
        if avg and avg > 0:
            out[(market, symbol)] = float(vol.iloc[-1] / avg)
    return pd.Series(out)


def screen_market(
    store, market: str, *, top: int = 25, min_history: int = 120,
    min_amount: float = 0.0, max_stale_days: int = 14, persist: bool = False,
) -> dict[str, Any]:
    """Rank a market's symbols by a trend/momentum/RVOL composite (price-only)."""
    from ah_screener.technical import compute_technical_indicators

    market = market.strip().upper()
    daily = store.query_df("SELECT * FROM daily_prices WHERE market = ?", [market])
    if daily.empty:
        return {"market": market, "universe": 0, "candidates": []}
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], errors="coerce")

    tech = compute_technical_indicators(daily, pd.DataFrame())
    if tech.empty:
        return {"market": market, "universe": 0, "candidates": []}

    latest = daily.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
    latest_amt = latest.set_index(["market", "symbol"])["amount"]
    latest_date = latest.set_index(["market", "symbol"])["trade_date"]
    rvol = _rvol(daily)

    tech = tech.copy()
    idx = list(zip(tech["market"], tech["symbol"]))
    tech["rvol"] = [rvol.get(k, np.nan) for k in idx]
    tech["amount"] = [latest_amt.get(k, np.nan) for k in idx]
    tech["trade_date"] = [latest_date.get(k, pd.NaT) for k in idx]

    # composite: trend/momentum-led, RVOL as a confirmation kicker.
    tech_score = pd.to_numeric(tech["technical_score"], errors="coerce").fillna(0.0)
    rvol_bonus = (pd.to_numeric(tech["rvol"], errors="coerce").fillna(1.0).clip(0, 3) - 1.0) * 10.0
    tech["composite_score"] = (tech_score + rvol_bonus).clip(0, 100).round(2)

    ranked = tech
    if min_amount > 0:
        ranked = ranked[pd.to_numeric(ranked["amount"], errors="coerce").fillna(0) >= min_amount]
    if max_stale_days and not latest.empty:
        cutoff = pd.to_datetime(daily["trade_date"]).max() - pd.Timedelta(days=max_stale_days)
        ranked = ranked[pd.to_datetime(ranked["trade_date"], errors="coerce") >= cutoff]
    ranked = ranked.sort_values(["composite_score", "return_60d"], ascending=False)

    present = [f for f in _OUT_FIELDS if f in ranked.columns]
    candidates: list[dict[str, Any]] = []
    for _, row in ranked.head(top).iterrows():
        rec: dict[str, Any] = {}
        for field in present:
            val = row[field]
            if isinstance(val, pd.Timestamp):
                rec[field] = None if pd.isna(val) else val.strftime("%Y-%m-%d")
            elif isinstance(val, (np.floating, float)):
                rec[field] = None if pd.isna(val) else round(float(val), 4)
            elif isinstance(val, (np.integer,)):
                rec[field] = int(val)
            else:
                rec[field] = None if (isinstance(val, float) and pd.isna(val)) else val
        candidates.append(rec)

    result = {"market": market, "universe": int(len(tech)), "candidates": candidates}
    if persist:
        store.upsert_dataframe(
            "expert_screening_results",
            _persist_frame(ranked.head(top), market),
        )
        result["persisted"] = len(candidates)
    return result


def _persist_frame(ranked: pd.DataFrame, market: str) -> pd.DataFrame:
    snapshot_date = pd.to_datetime(ranked["trade_date"], errors="coerce").max()
    return pd.DataFrame(
        {
            "snapshot_date": (snapshot_date.date() if pd.notna(snapshot_date) else pd.Timestamp.now().date()),
            "strategy": "global_technical",
            "market": market,
            "symbol": ranked["symbol"].astype(str),
            "expert_score": pd.to_numeric(ranked["composite_score"], errors="coerce"),
            "technical_score": pd.to_numeric(ranked["technical_score"], errors="coerce"),
            "decision": "technical_candidate",
            "updated_at": pd.Timestamp.now(),
        }
    )
