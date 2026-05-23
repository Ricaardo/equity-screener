"""Point-in-time fundamentals: reconstruct what was *publicly known* at a past date.

Historical validation/backtest must not use restated/latest financials (look-ahead,
master-plan R1). Given ``financial_statement_items`` (keyed by report_date), this
returns the latest report whose data was filed by ``as_of - lag_days`` and an as-of
fundamental score from its YoY growth.

A-share income statements store YoY directly (e.g. TOTAL_OPERATE_INCOME_YOY); HK is
best-effort by line-item name; US falls back to neutral.
"""

from __future__ import annotations

import pandas as pd

# Item codes that already carry YoY growth (%), most specific first.
_REVENUE_YOY_CODES = ("OPERATE_INCOME_YOY", "TOTAL_OPERATE_INCOME_YOY")
_PROFIT_YOY_CODES = ("PARENT_NETPROFIT_YOY", "NETPROFIT_YOY", "DEDUCT_PARENT_NETPROFIT_YOY")
_REVENUE_HK_NAMES = ("营业额", "营业收入", "收入", "revenue")
_PROFIT_HK_NAMES = ("股东应占溢利", "净利润", "本公司拥有人应占", "profit attributable")
NEUTRAL_SCORE = 50.0
PUBLICATION_LAG_DAYS = 60  # ~filing delay between period end and public availability


def _yoy_to_score(revenue_yoy: float, profit_yoy: float) -> float:
    """Map blended YoY growth (%) to 0-100; 50 = flat, profit weighted higher."""
    parts = [(revenue_yoy, 0.4), (profit_yoy, 0.6)]
    have = [(v, w) for v, w in parts if pd.notna(v)]
    if not have:
        return NEUTRAL_SCORE
    blended = sum(v * w for v, w in have) / sum(w for _, w in have)
    return float(min(100.0, max(0.0, 50.0 + blended * 0.5)))


def build_income_index(items: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    """Per-(market, symbol) income YoY by report_date, sorted ascending — for as-of lookups."""
    if items is None or items.empty:
        return {}
    inc = items[items["statement_type"].astype(str).eq("income")].copy()
    if inc.empty:
        return {}
    inc["report_date"] = pd.to_datetime(inc["report_date"], errors="coerce")
    inc["amount"] = pd.to_numeric(inc["amount"], errors="coerce")
    code = inc["item_code"].astype(str)
    name = inc["item_name"].astype(str)
    inc["is_rev_yoy"] = code.isin(_REVENUE_YOY_CODES) | name.str.contains("|".join(_REVENUE_HK_NAMES), case=False, na=False) & code.str.contains("YOY", na=False)
    inc["is_profit_yoy"] = code.isin(_PROFIT_YOY_CODES)
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for (market, symbol), grp in inc.groupby(["market", "symbol"]):
        rev = grp[grp["is_rev_yoy"]].groupby("report_date")["amount"].first()
        profit = grp[grp["is_profit_yoy"]].groupby("report_date")["amount"].first()
        frame = pd.DataFrame({"revenue_yoy": rev, "profit_yoy": profit}).sort_index()
        if not frame.empty:
            out[(str(market), str(symbol))] = frame
    return out


def as_of_score_from_index(
    index: dict[tuple[str, str], pd.DataFrame],
    market: str,
    symbol: str,
    as_of: pd.Timestamp,
    lag_days: int = PUBLICATION_LAG_DAYS,
) -> float:
    frame = index.get((str(market), str(symbol)))
    if frame is None or frame.empty:
        return NEUTRAL_SCORE
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=lag_days)
    known = frame[frame.index <= cutoff]
    if known.empty:
        return NEUTRAL_SCORE
    row = known.iloc[-1]
    return _yoy_to_score(row.get("revenue_yoy"), row.get("profit_yoy"))


def as_of_fundamental_score(
    items: pd.DataFrame, market: str, symbol: str, as_of: pd.Timestamp, lag_days: int = PUBLICATION_LAG_DAYS
) -> float:
    """Convenience single lookup (builds the index); prefer build_income_index for batches."""
    return as_of_score_from_index(build_income_index(items), market, symbol, as_of, lag_days)
