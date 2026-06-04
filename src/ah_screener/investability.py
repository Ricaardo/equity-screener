from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

import pandas as pd

from ah_screener.classification import is_st_name
from ah_screener.config import Settings
from ah_screener import weights


SHELL_STRUCTURE_RE = re.compile(
    r"\b(blank check|special purpose acquisition|acquisition corp|spac|warrant|rights?|units?)\b",
    re.IGNORECASE,
)

REASON_LABELS: dict[str, str] = {
    "non_stock_asset": "非股票资产",
    "price_missing": "价格缺失",
    "amount_missing": "成交额缺失",
    "low_amount": "成交额低于推荐阈值",
    "market_cap_missing": "市值缺失",
    "low_market_cap": "市值低于推荐阈值",
    "distress_name": "名称含退市/清盘等风险词",
    "a_st_or_delisting_name": "A股 ST/退市风险",
    "a_excluded_board": "A股非主板推荐范围",
    "hk_penny": "港股仙股价格",
    "hk_non_connect_illiquid": "非港股通且流动性不足",
    "us_low_price": "美股低价股",
    "us_shell_structure": "疑似 SPAC/权证/unit/壳结构",
}


def _to_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _recommend_amount_floor(market: str, settings: Settings) -> float:
    if market == "A":
        return settings.recommend_min_a_amount
    if market == "HK":
        return settings.recommend_min_hk_amount
    return settings.recommend_min_us_amount


def _recommend_market_cap_floor(market: str, settings: Settings) -> float:
    if market == "A":
        return settings.recommend_min_a_market_cap
    if market == "HK":
        return settings.recommend_min_hk_market_cap
    return settings.recommend_min_us_market_cap


def investability_reasons(row: pd.Series, settings: Settings) -> list[str]:
    """Recommendation-grade gate before an equity can enter the refined candidate pool."""
    reasons: list[str] = []
    market = str(row.get("market") or "").upper()
    name = str(row.get("name") or "")
    board = str(row.get("board") or "")
    asset_type = str(row.get("asset_type") or "stock").lower()

    if asset_type != "stock":
        reasons.append("non_stock_asset")

    price = _to_float(row.get("last_price"))
    if price is None or price <= 0:
        reasons.append("price_missing")
    elif market == "HK" and price < weights.HK_PENNY_PRICE:
        reasons.append("hk_penny")
    elif market == "US" and price < settings.recommend_min_us_price:
        reasons.append("us_low_price")

    amount = _to_float(row.get("amount"))
    amount_floor = _recommend_amount_floor(market, settings)
    if amount is None or amount <= 0:
        reasons.append("amount_missing")
    elif amount < amount_floor:
        reasons.append("low_amount")

    market_cap = _to_float(row.get("market_cap"))
    market_cap_floor = _recommend_market_cap_floor(market, settings)
    if market_cap is None or market_cap <= 0:
        reasons.append("market_cap_missing")
    elif market_cap < market_cap_floor:
        reasons.append("low_market_cap")

    lowered = name.lower()
    if weights.NAME_DISTRESS_MARKERS and any(
        marker.lower() in lowered for marker in weights.NAME_DISTRESS_MARKERS
    ):
        reasons.append("distress_name")

    if market == "A":
        if is_st_name(name):
            reasons.append("a_st_or_delisting_name")
        if board in {"B股", "北交所"}:
            reasons.append("a_excluded_board")
    elif market == "HK":
        is_hk_connect = _bool_value(row.get("is_hk_connect"))
        if not is_hk_connect and amount is not None and amount < settings.recommend_min_hk_non_connect_amount:
            reasons.append("hk_non_connect_illiquid")
    elif market == "US":
        symbol = str(row.get("symbol") or "")
        text = f"{symbol} {name}"
        if SHELL_STRUCTURE_RE.search(text):
            reasons.append("us_shell_structure")

    return list(dict.fromkeys(reasons))


def summarize_reason_lists(reason_lists: Iterable[object]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in reason_lists:
        if isinstance(value, list):
            counts.update(str(item) for item in value if item)
        elif isinstance(value, tuple):
            counts.update(str(item) for item in value if item)
        elif isinstance(value, str) and value:
            text = value.strip()
            if text.startswith("["):
                try:
                    import json

                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = []
                if isinstance(parsed, list):
                    counts.update(str(item) for item in parsed if item)
                    continue
            counts.update(part.strip() for part in text.split(",") if part.strip())
    return dict(counts.most_common())
