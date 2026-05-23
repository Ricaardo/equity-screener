from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class EtfRule:
    category: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class EtfTrackRule:
    track: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class EtfClusterRule:
    """A correlation cluster groups near-substitute tracks (e.g. 沪深300≈上证50≈中证A500).

    Kept deliberately conservative: only fold tracks that are genuine substitutes
    (same broad exposure). Distinct commodities/industries/markets stay separate and
    fall back to their own track. See docs/master-plan.md R16.
    """

    cluster: str
    tracks: tuple[str, ...]


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
            "s&p 500",
            "s&p500",
            "spdr s&p",
            "nasdaq 100",
            "nasdaq-100",
            "qqq",
            "russell 2000",
            "dow jones",
            "dia",
            "iwm",
        ),
    ),
)

ETF_TRACK_RULES: tuple[EtfTrackRule, ...] = (
    EtfTrackRule("沪深300", ("沪深300", "csi300", "csi 300")),
    EtfTrackRule("中证A500", ("中证a500", "a500")),
    EtfTrackRule("中证500", ("中证500", "csi500", "csi 500")),
    EtfTrackRule("中证1000", ("中证1000", "csi1000", "csi 1000")),
    EtfTrackRule("中证2000", ("中证2000", "csi2000", "csi 2000")),
    # Specific "...50" tracks must be matched before 上证50, whose greedy "50etf"
    # keyword would otherwise swallow 科创50ETF / 创业板50ETF.
    EtfTrackRule("科创50", ("科创50", "科创板50")),
    EtfTrackRule("创业板50", ("创业板50",)),
    EtfTrackRule("创业板指", ("创业板", "创业板指")),
    EtfTrackRule("深证100", ("深证100", "深100")),
    EtfTrackRule("上证50", ("上证50", "50etf")),
    EtfTrackRule("MSCI中国A50", ("msci中国a50", "中国a50", "a50中国")),
    EtfTrackRule("恒生指数", ("恒生指数", "恒指", "hsi", "tracker fund", "盈富")),
    EtfTrackRule("恒生科技", ("恒生科技", "hstech", "hang seng tech")),
    EtfTrackRule("恒生中国企业", ("恒生中国企业", "国企指数", "hscei", "h股指数")),
    EtfTrackRule("恒生高股息", ("恒生高股息", "恒生股息", "高股息")),
    EtfTrackRule("纳斯达克100", ("纳斯达克100", "纳指100", "纳指", "nasdaq 100", "nasdaq100", "nasdaq-100", "qqq")),
    EtfTrackRule("标普500", ("标普500", "s&p500", "s&p 500", "sp500", "spdr s&p", "spy")),
    EtfTrackRule("罗素2000", ("russell 2000", "russell2000", "iwm")),
    EtfTrackRule("道琼斯工业", ("dow jones", "dia")),
    EtfTrackRule("日经225", ("日经225", "日经", "nikkei 225")),
    EtfTrackRule("德国DAX", ("德国dax", "dax")),
    EtfTrackRule("黄金", ("黄金", "gold")),
    EtfTrackRule("原油", ("原油", "油气", "oil")),
    EtfTrackRule("中证红利", ("中证红利", "红利低波", "红利")),
    EtfTrackRule("证券", ("证券", "券商")),
    EtfTrackRule("银行", ("银行",)),
    EtfTrackRule("医药医疗", ("医药", "医疗", "创新药")),
    EtfTrackRule("消费", ("消费", "食品饮料", "白酒")),
    EtfTrackRule("新能源", ("新能源", "电池", "光伏")),
    EtfTrackRule("半导体芯片", ("半导体", "芯片")),
    EtfTrackRule("人工智能", ("人工智能", "ai", "算力", "机器人")),
    EtfTrackRule("军工", ("军工",)),
    EtfTrackRule("传媒游戏", ("传媒", "游戏")),
    EtfTrackRule("有色金属", ("有色", "稀土", "铜", "铝")),
    EtfTrackRule("煤炭", ("煤炭",)),
)

# Conservative correlation clusters: only near-substitute tracks are folded.
# Anything not listed keeps its own track as the cluster (no over-folding).
ETF_CLUSTER_RULES: tuple[EtfClusterRule, ...] = (
    EtfClusterRule("大盘宽基", ("沪深300", "上证50", "中证A500", "MSCI中国A50", "深证100")),
    EtfClusterRule("中小盘宽基", ("中证500", "中证1000", "中证2000")),
    EtfClusterRule("成长科创宽基", ("创业板指", "创业板50", "科创50")),
    EtfClusterRule("中国互联网科技", ("恒生科技", "恒生", "中概")),
    EtfClusterRule("港股核心宽基", ("恒生指数", "恒生中国企业")),
    EtfClusterRule("美股大盘", ("纳斯达克100", "标普500", "道琼斯工业")),
    EtfClusterRule("美股小盘", ("罗素2000",)),
)

_TRACK_TO_CLUSTER: dict[str, str] = {
    track: rule.cluster for rule in ETF_CLUSTER_RULES for track in rule.tracks
}

HK_ETF_CODE_PREFIXES = ("028", "030", "031", "072", "073", "075")
HK_ETF_NAME_KEYWORDS = (
    "ETF",
    "ＥＴＦ",
    "基金",
    "盈富",
    "安硕",
    "南方",
    "华夏",
    "易方达",
    "嘉实",
    "博时",
    "三星",
    "GX",
    "FI",
    "XI",
    "SPDR",
    "TR",
    "ISHARES",
    "CSOP",
    "PREMIA",
)


def classify_etf(name: object) -> tuple[str, str]:
    text = str(name or "").strip()
    lowered = text.lower()
    for rule in ETF_RULES:
        for keyword in rule.keywords:
            if keyword.lower() in lowered:
                return rule.category, keyword
    return "其他ETF", "未识别"


def infer_etf_track(name: object, category: object = None, keyword: object = None) -> tuple[str, str]:
    text = str(name or "").strip()
    lowered = text.lower()
    compact = lowered.replace(" ", "")
    for rule in ETF_TRACK_RULES:
        for item in rule.keywords:
            lowered_item = item.lower()
            if lowered_item in lowered or lowered_item.replace(" ", "") in compact:
                return rule.track, item
    fallback = str(keyword or "").strip()
    if fallback and fallback != "未识别":
        return fallback, fallback
    category_text = str(category or "").strip()
    return category_text or "其他ETF", "未识别"


def infer_etf_cluster(track: object) -> str:
    """Map an inferred track to its correlation cluster, defaulting to the track itself."""
    key = str(track or "").strip()
    if not key:
        return "其他ETF"
    return _TRACK_TO_CLUSTER.get(key, key)


def is_hk_listed_etf(symbol: object, name: object) -> bool:
    clean_symbol = str(symbol or "").lower().replace("hk", "").zfill(5)
    upper_name = str(name or "").strip().upper()
    if "ETF" in upper_name or "ＥＴＦ" in upper_name:
        return True
    if not clean_symbol.startswith(HK_ETF_CODE_PREFIXES):
        return False
    return any(keyword.upper() in upper_name for keyword in HK_ETF_NAME_KEYWORDS)


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


def _join_technical_score(enriched: pd.DataFrame, technicals: pd.DataFrame | None) -> pd.Series:
    """Real technical_score per ETF (Stage 2), neutral 50 where history is missing.

    Replaces the previous ``50 + pct_change*6`` pseudo-momentum so ETFs and stocks
    share one technical engine. See docs/master-plan.md R11/R12.
    """
    neutral = pd.Series(50.0, index=enriched.index)
    if technicals is None or technicals.empty or "technical_score" not in technicals.columns:
        return neutral
    tech = (
        technicals[["market", "symbol", "technical_score"]]
        .dropna(subset=["market", "symbol"])
        .drop_duplicates(["market", "symbol"], keep="last")
    )
    keyed = enriched[["market", "symbol"]].merge(tech, on=["market", "symbol"], how="left")
    score = pd.to_numeric(keyed["technical_score"].to_numpy(), errors="coerce")
    return pd.Series(score, index=enriched.index).fillna(50.0).clip(0, 100)


def enrich_etf_snapshot(df: pd.DataFrame, technicals: pd.DataFrame | None = None) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    enriched = df.copy()
    classified = enriched.get("name", pd.Series("", index=enriched.index)).map(classify_etf)
    enriched["etf_category"] = classified.map(lambda item: item[0])
    enriched["etf_keyword"] = classified.map(lambda item: item[1])
    tracked = enriched.apply(
        lambda row: infer_etf_track(row.get("name"), row.get("etf_category"), row.get("etf_keyword")),
        axis=1,
    )
    enriched["etf_track"] = tracked.map(lambda item: item[0])
    enriched["etf_track_keyword"] = tracked.map(lambda item: item[1])
    enriched["etf_cluster"] = enriched["etf_track"].map(infer_etf_cluster)
    enriched["etf_peer_group"] = enriched["etf_category"].astype(str) + ":" + enriched["etf_track"].astype(str)
    enriched["etf_technical_score"] = _join_technical_score(enriched, technicals)

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
    technical_score = enriched["etf_technical_score"]
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
        + technical_score * 0.15
        + turnover_score * 0.10
        + category_bonus
        - risk_penalty
    ).clip(0, 100).round(1)
    enriched["etf_recommendation"] = enriched.apply(
        lambda row: _recommendation(row.get("etf_score"), row.get("amount"), row.get("etf_category")),
        axis=1,
    )
    return enriched


def consolidate_etf_candidates(
    df: pd.DataFrame,
    top: int = 100,
    category: str | None = None,
    group_col: str = "etf_peer_group",
    technicals: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """De-duplicate an ETF pool, keeping the best candidate per group.

    ``group_col`` selects the de-dup granularity: ``etf_peer_group`` (default,
    track level — folds same-index funds) or ``etf_cluster`` (cluster level —
    folds near-substitute indices). Backward compatible: default behaviour is
    unchanged. A frame that is already enriched (carries ``etf_score``) is used
    as-is, so an upstream technical join is preserved.
    """
    if df.empty:
        return df.copy()

    enriched = df if "etf_score" in df.columns else enrich_etf_snapshot(df, technicals=technicals)
    if category:
        enriched = enriched[enriched["etf_category"].eq(category)]
    if enriched.empty:
        return enriched
    if group_col not in enriched.columns:
        raise KeyError(f"group_col {group_col!r} not present in enriched ETF frame")

    enriched = enriched.copy()
    enriched["amount_num"] = pd.to_numeric(
        enriched.get("amount", pd.Series(0, index=enriched.index)), errors="coerce"
    ).fillna(0)
    enriched["score_num"] = pd.to_numeric(
        enriched.get("etf_score", pd.Series(0, index=enriched.index)), errors="coerce"
    ).fillna(0)
    ranked = enriched.sort_values([group_col, "score_num", "amount_num"], ascending=[True, False, False])
    leaders = ranked.drop_duplicates(group_col, keep="first").copy()

    group_stats = (
        enriched.groupby(group_col, dropna=False)
        .agg(
            peer_count=("symbol", "nunique"),
            peer_total_amount=("amount_num", "sum"),
            peer_max_score=("score_num", "max"),
        )
        .reset_index()
    )

    alternatives = (
        ranked.assign(
            peer_item=lambda frame: frame["market"].astype(str)
            + ":"
            + frame["symbol"].astype(str)
            + " "
            + frame["name"].astype(str)
        )
        .groupby(group_col, dropna=False)["peer_item"]
        .apply(lambda values: " | ".join(values.iloc[1:4]))
        .reset_index(name="peer_alternatives")
    )

    leaders = leaders.merge(group_stats, on=group_col, how="left").merge(
        alternatives, on=group_col, how="left"
    )
    leaders["selection_note"] = leaders.apply(
        lambda row: (
            f"{row[group_col]} 同组{int(row['peer_count'])}只，"
            "优先选流动性/规模/动量综合最高"
        ),
        axis=1,
    )
    return leaders.sort_values(["score_num", "amount_num"], ascending=[False, False]).head(top)
