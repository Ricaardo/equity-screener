from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

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


ETF_RULES: tuple[EtfRule, ...]
ETF_TRACK_RULES: tuple[EtfTrackRule, ...]
ETF_CLUSTER_RULES: tuple[EtfClusterRule, ...]


def _load_etf_rules() -> tuple[
    tuple[EtfRule, ...], tuple[EtfTrackRule, ...], tuple[EtfClusterRule, ...]
]:
    """Load ETF classification rules from the packaged data file (stage 8: rules externalized).

    Edit ``src/ah_screener/data/etf_rules.json`` to change classification without code
    edits. Track list order is significant (first-match wins) and is preserved.
    """
    raw = json.loads(
        resources.files("ah_screener").joinpath("data", "etf_rules.json").read_text("utf-8")
    )
    categories = tuple(EtfRule(item["category"], tuple(item["keywords"])) for item in raw["categories"])
    tracks = tuple(EtfTrackRule(item["track"], tuple(item["keywords"])) for item in raw["tracks"])
    clusters = tuple(EtfClusterRule(item["cluster"], tuple(item["tracks"])) for item in raw["clusters"])
    return categories, tracks, clusters


ETF_RULES, ETF_TRACK_RULES, ETF_CLUSTER_RULES = _load_etf_rules()

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
