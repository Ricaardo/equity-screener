from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class EtfRule:
    category: str
    keywords: tuple[str, ...]


ETF_RULES: tuple[EtfRule, ...] = (
    EtfRule("货币ETF", ("货币", "现金", "添利", "保证金", "快线", "收益", "日利")),
    EtfRule("债券ETF", ("债", "国债", "政金债", "信用债", "城投", "可转债", "地方债")),
    EtfRule("商品ETF", ("黄金", "有色", "豆粕", "能源化工", "商品", "原油", "油气", "铜", "铝")),
    EtfRule(
        "跨境ETF",
        (
            "恒生",
            "港股",
            "香港",
            "中概",
            "纳指",
            "标普",
            "美国",
            "日经",
            "德国",
            "法国",
            "韩国",
            "印度",
            "东南亚",
            "qdii",
        ),
    ),
    EtfRule("红利/策略ETF", ("红利", "高股息", "价值", "成长", "质量", "低波", "增强", "优选")),
    EtfRule(
        "主题ETF",
        (
            "ai",
            "人工智能",
            "算力",
            "机器人",
            "低空",
            "数字经济",
            "云计算",
            "数据",
            "央企",
            "国企",
            "esg",
            "碳中和",
            "创新药",
            "生物科技",
            "智能驾驶",
            "出海",
            "稀土",
            "卫星",
            "光模块",
        ),
    ),
    EtfRule(
        "行业ETF",
        (
            "银行",
            "证券",
            "券商",
            "保险",
            "地产",
            "房地产",
            "医药",
            "医疗",
            "消费",
            "食品饮料",
            "白酒",
            "军工",
            "传媒",
            "游戏",
            "通信",
            "计算机",
            "软件",
            "新能源",
            "电池",
            "光伏",
            "半导体",
            "芯片",
            "电子",
            "汽车",
            "有色",
            "煤炭",
            "钢铁",
            "农业",
            "养殖",
            "物流",
            "基建",
            "建材",
            "家电",
        ),
    ),
    EtfRule(
        "宽基指数ETF",
        (
            "沪深300",
            "中证500",
            "中证1000",
            "中证2000",
            "上证50",
            "科创50",
            "创业板50",
            "创业板",
            "深证100",
            "a500",
            "a50",
            "msci中国a50",
            "上证指数",
        ),
    ),
)


def classify_etf(name: object) -> tuple[str, str]:
    text = str(name or "").strip()
    lowered = text.lower()
    for rule in ETF_RULES:
        for keyword in rule.keywords:
            if keyword.lower() in lowered:
                return rule.category, keyword
    return "其他ETF", "未识别"


def _rank_score(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() == 0:
        return pd.Series(50.0, index=series.index)
    return (numeric.rank(pct=True, ascending=True) * 100).fillna(35).clip(0, 100)


def _recommendation(score: object, amount: object, category: object) -> str:
    score_num = pd.to_numeric(pd.Series([score]), errors="coerce").iloc[0]
    amount_num = pd.to_numeric(pd.Series([amount]), errors="coerce").iloc[0]
    if pd.isna(score_num):
        return "数据不足"
    if score_num >= 76 and pd.notna(amount_num) and amount_num >= 100_000_000:
        return "优先观察"
    if score_num >= 64 and str(category) not in {"货币ETF"}:
        return "观察"
    if pd.notna(amount_num) and amount_num < 20_000_000:
        return "流动性谨慎"
    return "工具型跟踪"


def enrich_etf_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    enriched = df.copy()
    classified = enriched.get("name", pd.Series("", index=enriched.index)).map(classify_etf)
    enriched["etf_category"] = classified.map(lambda item: item[0])
    enriched["etf_keyword"] = classified.map(lambda item: item[1])

    amount = pd.to_numeric(enriched.get("amount", pd.Series(0, index=enriched.index)), errors="coerce")
    market_cap = pd.to_numeric(enriched.get("market_cap", pd.Series(0, index=enriched.index)), errors="coerce")
    pct_change = pd.to_numeric(
        enriched.get("pct_change", pd.Series(0, index=enriched.index)), errors="coerce"
    )
    turnover = pd.to_numeric(
        enriched.get("turnover_rate", pd.Series(0, index=enriched.index)), errors="coerce"
    )

    amount_score = _rank_score(amount)
    scale_score = _rank_score(market_cap)
    momentum_score = (50 + pct_change.fillna(0).clip(-6, 6) * 6).clip(0, 100)
    turnover_score = _rank_score(turnover)
    category_bonus = enriched["etf_category"].map(
        {
            "宽基指数ETF": 4.0,
            "红利/策略ETF": 4.0,
            "行业ETF": 2.0,
            "主题ETF": 1.5,
            "跨境ETF": 1.0,
            "债券ETF": -1.0,
            "商品ETF": -1.0,
            "货币ETF": -6.0,
        }
    ).fillna(0)
    risk_penalty = pd.Series(0.0, index=enriched.index)
    risk_penalty = risk_penalty.mask(amount < 20_000_000, risk_penalty + 8)
    risk_penalty = risk_penalty.mask(pct_change.abs() > 8, risk_penalty + 6)

    enriched["etf_liquidity_score"] = amount_score.round(1)
    enriched["etf_score"] = (
        amount_score * 0.55
        + scale_score * 0.20
        + momentum_score * 0.15
        + turnover_score * 0.10
        + category_bonus
        - risk_penalty
    ).clip(0, 100).round(1)
    enriched["etf_recommendation"] = enriched.apply(
        lambda row: _recommendation(row.get("etf_score"), row.get("amount"), row.get("etf_category")),
        axis=1,
    )
    return enriched
