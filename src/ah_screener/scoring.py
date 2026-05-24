"""Shared scoring primitives.

The standalone simple-score model (``score_snapshot`` → ``screening_scores``) was
retired — it duplicated the richer ``expert_model`` and had no report/UI/backtest
consumers (docs/master-plan.md R7, stage 8). These rank/valuation/liquidity/risk
helpers remain because ``expert_model`` reuses them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ah_screener import weights
from ah_screener.config import Settings


def _rank_score(series: pd.Series, ascending: bool = True) -> pd.Series:
    valid = pd.to_numeric(series, errors="coerce")
    if valid.notna().sum() == 0:
        return pd.Series(50.0, index=series.index)
    pct = valid.rank(pct=True, ascending=ascending)
    return (pct * 100).fillna(50).clip(0, 100)


def _valuation_score(df: pd.DataFrame) -> pd.Series:
    pe = pd.to_numeric(df["pe_ttm"], errors="coerce")
    pb = pd.to_numeric(df["pb"], errors="coerce")

    pe_score = _rank_score(pe.where((pe > 0) & (pe < 120)), ascending=False)
    pb_score = _rank_score(pb.where((pb > 0) & (pb < 30)), ascending=False)
    return (pe_score * 0.65 + pb_score * 0.35).fillna(50).clip(0, 100)


def _liquidity_score(df: pd.DataFrame) -> pd.Series:
    amount = pd.to_numeric(df["amount"], errors="coerce")
    log_amount = np.log10(amount.where(amount > 0))
    return _rank_score(log_amount, ascending=True)


def _risk_penalty(
    row: pd.Series,
    settings: Settings,
    delisted_keys: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[float, list[str]]:
    """Pre-rank risk gate ("先排雷").

    ``delisted_keys`` are ``(market, symbol)`` pairs found in
    ``security_lifecycle_events`` (P2-1): a live snapshot row overlapping a delisted
    record is a hard red flag. HK/US also get penny-price and distress-name rules so
    the gate is not A-share-only.
    """
    reasons: list[str] = []
    penalty = 0.0
    p = weights.RISK_PENALTY
    name = str(row.get("name") or "")
    amount = float(row.get("amount") or 0)
    market = str(row["market"])
    symbol = str(row.get("symbol") or "")

    if market == "A" and ("ST" in name.upper() or "退" in name):
        penalty += p["a_st_name"]
        reasons.append("A股 ST/退市风险名称")

    if (market, symbol) in delisted_keys:
        penalty += p["delisted_lifecycle"]
        reasons.append("命中退市/摘牌生命周期记录")

    if weights.NAME_DISTRESS_MARKERS and any(
        marker.lower() in name.lower() for marker in weights.NAME_DISTRESS_MARKERS
    ):
        penalty += p["name_distress"]
        reasons.append("名称含清盘/除牌/退市/破产等风险词")

    if market == "A":
        min_amount = settings.min_a_amount
    elif market == "US":
        min_amount = settings.min_us_amount
    else:
        min_amount = settings.min_hk_amount
    if amount <= 0:
        penalty += p["amount_missing"]
        reasons.append("成交额缺失或为0")
    elif amount < min_amount:
        penalty += p["amount_below_floor"]
        reasons.append(f"成交额低于阈值 {min_amount:,.0f}")

    last_price = row.get("last_price")
    if pd.isna(last_price) or float(last_price) <= 0:
        penalty += p["price_missing"]
        reasons.append("最新价缺失或异常")
    else:
        price = float(last_price)
        if market == "HK" and price < weights.HK_PENNY_PRICE:
            penalty += p["hk_penny"]
            reasons.append(f"港股仙股价格(<{weights.HK_PENNY_PRICE})")
        elif market == "US" and price < weights.US_PENNY_PRICE:
            penalty += p["us_penny"]
            reasons.append(f"美股低价股(<${weights.US_PENNY_PRICE:.0f})退市风险")

    return penalty, reasons
