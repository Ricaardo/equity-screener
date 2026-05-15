from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from ah_screener.config import Settings


THEME_KEYWORDS = {
    "AI",
    "人工智能",
    "算力",
    "半导体",
    "芯片",
    "机器人",
    "新能源",
    "储能",
    "光伏",
    "风电",
    "创新药",
    "CXO",
    "出海",
    "高股息",
    "央企",
    "国企",
    "国产替代",
}


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


def _theme_score(tags: pd.DataFrame, index: pd.MultiIndex) -> pd.Series:
    if tags.empty:
        return pd.Series(30.0, index=index)

    tag_text = tags.assign(
        is_theme=tags["tag_name"].astype(str).apply(
            lambda value: any(keyword.lower() in value.lower() for keyword in THEME_KEYWORDS)
        )
    )
    counts = (
        tag_text[tag_text["is_theme"]]
        .groupby(["market", "symbol"])
        .size()
        .rename("theme_hits")
        .reindex(index, fill_value=0)
    )
    return (30 + counts.clip(0, 5) * 14).astype(float).clip(0, 100)


def _risk_penalty(row: pd.Series, settings: Settings) -> tuple[float, list[str]]:
    reasons: list[str] = []
    penalty = 0.0
    name = str(row.get("name") or "")
    amount = float(row.get("amount") or 0)
    market = row["market"]

    if market == "A" and ("ST" in name.upper() or "退" in name):
        penalty += 100
        reasons.append("A股 ST/退市风险名称")

    if market == "A":
        min_amount = settings.min_a_amount
    elif market == "US":
        min_amount = settings.min_us_amount
    else:
        min_amount = settings.min_hk_amount
    if amount <= 0:
        penalty += 60
        reasons.append("成交额缺失或为0")
    elif amount < min_amount:
        penalty += 35
        reasons.append(f"成交额低于阈值 {min_amount:,.0f}")

    last_price = row.get("last_price")
    if pd.isna(last_price) or float(last_price) <= 0:
        penalty += 50
        reasons.append("最新价缺失或异常")

    return penalty, reasons


def score_snapshot(snapshots: pd.DataFrame, tags: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    if snapshots.empty:
        return pd.DataFrame()

    latest_date = snapshots["trade_date"].max()
    df = snapshots.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
    df = df.set_index(["market", "symbol"], drop=False)

    df["quality_score"] = 50.0
    df["growth_score"] = 50.0
    df["valuation_score"] = _valuation_score(df)
    df["liquidity_score"] = _liquidity_score(df)
    df["theme_score"] = _theme_score(tags, df.index)

    penalties: list[float] = []
    reasons_list: list[str] = []
    decisions: list[str] = []
    for _, row in df.iterrows():
        penalty, reasons = _risk_penalty(row, settings)
        penalties.append(penalty)
        total_before_penalty = (
            row["valuation_score"] * 0.30
            + row["liquidity_score"] * 0.25
            + row["theme_score"] * 0.20
            + row["quality_score"] * 0.15
            + row["growth_score"] * 0.10
        )
        total = max(0.0, total_before_penalty - penalty)
        if penalty >= 80 or total < 35:
            decision = "reject"
        elif total < 50:
            decision = "watch"
        else:
            decision = "keep"
        decisions.append(decision)
        reasons_list.append(json.dumps(reasons, ensure_ascii=False))

    df["risk_score"] = pd.Series(penalties, index=df.index).clip(0, 100)
    df["total_score"] = (
        df["valuation_score"] * 0.30
        + df["liquidity_score"] * 0.25
        + df["theme_score"] * 0.20
        + df["quality_score"] * 0.15
        + df["growth_score"] * 0.10
        - df["risk_score"]
    ).clip(0, 100)
    df["decision"] = decisions
    df["reasons"] = reasons_list
    df["snapshot_date"] = latest_date
    df["updated_at"] = pd.Timestamp(datetime.now())

    return df[
        [
            "snapshot_date",
            "market",
            "symbol",
            "name",
            "quality_score",
            "growth_score",
            "valuation_score",
            "liquidity_score",
            "theme_score",
            "risk_score",
            "total_score",
            "decision",
            "reasons",
            "updated_at",
        ]
    ].reset_index(drop=True)
