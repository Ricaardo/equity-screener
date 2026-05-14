from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from ah_screener.config import Settings
from ah_screener.scoring import _liquidity_score, _risk_penalty, _valuation_score


STRATEGY_NAME = "china_masters_fundamental_theme_technical_v2"


@dataclass(frozen=True)
class HotTheme:
    name: str
    markets: tuple[str, ...]
    weight: float
    keywords: tuple[str, ...]
    rationale: str
    source: str


HOT_THEMES: tuple[HotTheme, ...] = (
    HotTheme(
        name="AI算力硬件",
        markets=("A", "HK"),
        weight=1.00,
        keywords=("AI", "人工智能", "算力", "CPO", "光模块", "光通信", "PCB", "服务器", "液冷", "数据中心", "存储", "GPU"),
        rationale="AI 应用扩散先拉动算力、网络、存储、散热和服务器资本开支。",
        source="current_market_theme:ai_compute_infrastructure",
    ),
    HotTheme(
        name="半导体国产替代",
        markets=("A", "HK"),
        weight=0.90,
        keywords=("半导体", "芯片", "集成电路", "存储", "海思", "先进封装", "光刻胶", "设备", "材料", "晶圆"),
        rationale="地缘约束和产业升级使半导体设备、材料、设计和制造长期具备政策与需求双支撑。",
        source="current_market_theme:semiconductor_localization",
    ),
    HotTheme(
        name="人形机器人与高端制造",
        markets=("A", "HK"),
        weight=0.85,
        keywords=("机器人", "人形机器人", "伺服", "减速器", "传感器", "执行器", "自动化", "工业母机"),
        rationale="AI 与硬件制造结合，机器人链条处在从主题验证到产业订单验证的阶段。",
        source="current_market_theme:robotics",
    ),
    HotTheme(
        name="创新药与医疗科技",
        markets=("A", "HK"),
        weight=0.78,
        keywords=("创新药", "CXO", "生物医药", "医疗器械", "AI医疗", "疫苗", "药明", "恒瑞", "百济", "信达"),
        rationale="创新药出海、BD 交易和医药估值修复使优质药企重新进入成长筛选池。",
        source="current_market_theme:innovative_healthcare",
    ),
    HotTheme(
        name="高股息央国企防御",
        markets=("A", "HK"),
        weight=0.72,
        keywords=("高股息", "央企", "国企", "电力", "煤炭", "银行", "公用事业", "运营商", "石油", "能源", "电信"),
        rationale="低利率和波动市场中，现金流稳定、分红率较高的央国企适合作为防御底仓候选。",
        source="current_market_theme:high_dividend_soe",
    ),
    HotTheme(
        name="电力储能与能源转型",
        markets=("A", "HK"),
        weight=0.82,
        keywords=("电力", "储能", "光伏", "风电", "电池", "电网", "新能源", "能源互联网", "钙钛矿", "充电"),
        rationale="AI 算力用电增长、能源转型和电网投资共同提升电力与储能链条关注度。",
        source="current_market_theme:power_storage",
    ),
    HotTheme(
        name="资源涨价与安全资产",
        markets=("A", "HK"),
        weight=0.76,
        keywords=("黄金", "有色", "稀土", "铜", "铝", "小金属", "石油", "煤炭", "资源"),
        rationale="通胀预期、地缘风险和供给约束使贵金属、能源和部分工业金属保持配置价值。",
        source="current_market_theme:resources",
    ),
    HotTheme(
        name="港股AI互联网平台",
        markets=("HK",),
        weight=0.88,
        keywords=("腾讯", "阿里", "美团", "快手", "京东", "网易", "百度", "小米", "联想", "金蝶", "金山云", "哔哩"),
        rationale="港股互联网平台具备 AI 产品化、云业务、现金流和估值修复的交集。",
        source="current_market_theme:hk_ai_internet",
    ),
    HotTheme(
        name="汽车智能化与出海",
        markets=("A", "HK"),
        weight=0.75,
        keywords=("汽车", "智能驾驶", "华为汽车", "小米汽车", "比亚迪", "电动车", "零部件", "出海"),
        rationale="智能驾驶、品牌出海和供应链升级使整车与核心零部件适合做主题筛选。",
        source="current_market_theme:smart_ev_export",
    ),
)


CURATED_THEME_OVERRIDES: dict[tuple[str, str], tuple[str, ...]] = {
    ("A", "300308"): ("AI算力硬件",),
    ("A", "300502"): ("AI算力硬件",),
    ("A", "300394"): ("AI算力硬件",),
    ("A", "601138"): ("AI算力硬件",),
    ("A", "000977"): ("AI算力硬件",),
    ("A", "603019"): ("AI算力硬件",),
    ("A", "300442"): ("AI算力硬件",),
    ("A", "300476"): ("AI算力硬件",),
    ("A", "000988"): ("AI算力硬件",),
    ("A", "002281"): ("AI算力硬件",),
    ("A", "688256"): ("AI算力硬件", "半导体国产替代"),
    ("A", "688041"): ("AI算力硬件", "半导体国产替代"),
    ("A", "688008"): ("半导体国产替代", "AI算力硬件"),
    ("A", "603986"): ("半导体国产替代",),
    ("A", "688981"): ("半导体国产替代",),
    ("A", "300475"): ("半导体国产替代",),
    ("A", "300782"): ("半导体国产替代",),
    ("A", "300750"): ("电力储能与能源转型",),
    ("A", "002015"): ("电力储能与能源转型",),
    ("A", "002594"): ("汽车智能化与出海", "电力储能与能源转型"),
    ("A", "600276"): ("创新药与医疗科技",),
    ("A", "603259"): ("创新药与医疗科技",),
    ("HK", "00700"): ("港股AI互联网平台",),
    ("HK", "09988"): ("港股AI互联网平台",),
    ("HK", "03690"): ("港股AI互联网平台",),
    ("HK", "01024"): ("港股AI互联网平台",),
    ("HK", "09618"): ("港股AI互联网平台",),
    ("HK", "09999"): ("港股AI互联网平台",),
    ("HK", "09888"): ("港股AI互联网平台",),
    ("HK", "01810"): ("港股AI互联网平台", "汽车智能化与出海"),
    ("HK", "00981"): ("半导体国产替代",),
    ("HK", "01347"): ("半导体国产替代",),
    ("HK", "06869"): ("AI算力硬件",),
    ("HK", "01211"): ("汽车智能化与出海", "电力储能与能源转型"),
    ("HK", "00175"): ("汽车智能化与出海",),
    ("HK", "02015"): ("汽车智能化与出海",),
    ("HK", "09868"): ("汽车智能化与出海",),
    ("HK", "01801"): ("创新药与医疗科技",),
    ("HK", "06160"): ("创新药与医疗科技",),
    ("HK", "02269"): ("创新药与医疗科技",),
    ("HK", "00883"): ("高股息央国企防御", "资源涨价与安全资产"),
    ("HK", "00857"): ("高股息央国企防御", "资源涨价与安全资产"),
    ("HK", "01088"): ("高股息央国企防御", "资源涨价与安全资产"),
    ("HK", "00941"): ("高股息央国企防御",),
    ("HK", "00728"): ("高股息央国企防御",),
}


DUAL_LISTING_GROUPS: dict[tuple[str, str], str] = {
    ("A", "002594"): "比亚迪AH",
    ("HK", "01211"): "比亚迪AH",
    ("A", "688981"): "中芯国际AH",
    ("HK", "00981"): "中芯国际AH",
    ("A", "600941"): "中国移动AH",
    ("HK", "00941"): "中国移动AH",
    ("A", "601728"): "中国电信AH",
    ("HK", "00728"): "中国电信AH",
    ("A", "600050"): "中国联通AH",
    ("HK", "00762"): "中国联通AH",
    ("A", "600938"): "中国海油AH",
    ("HK", "00883"): "中国海油AH",
    ("A", "601857"): "中国石油AH",
    ("HK", "00857"): "中国石油AH",
    ("A", "601088"): "中国神华AH",
    ("HK", "01088"): "中国神华AH",
    ("A", "000063"): "中兴通讯AH",
    ("HK", "00763"): "中兴通讯AH",
    ("A", "603259"): "药明康德AH",
    ("HK", "02359"): "药明康德AH",
    ("A", "601012"): "隆基绿能AH",
    ("HK", "06865"): "隆基绿能AH",
    ("A", "601318"): "中国平安AH",
    ("HK", "02318"): "中国平安AH",
    ("A", "600036"): "招商银行AH",
    ("HK", "03968"): "招商银行AH",
    ("A", "601398"): "工商银行AH",
    ("HK", "01398"): "工商银行AH",
    ("A", "601939"): "建设银行AH",
    ("HK", "00939"): "建设银行AH",
    ("A", "601288"): "农业银行AH",
    ("HK", "01288"): "农业银行AH",
    ("A", "601988"): "中国银行AH",
    ("HK", "03988"): "中国银行AH",
}


THEME_PRIORITY: tuple[str, ...] = (
    "AI算力硬件",
    "半导体国产替代",
    "港股AI互联网平台",
    "人形机器人与高端制造",
    "创新药与医疗科技",
    "汽车智能化与出海",
    "电力储能与能源转型",
    "高股息央国企防御",
    "资源涨价与安全资产",
)


def hot_theme_definitions_df(snapshot_date: pd.Timestamp) -> pd.DataFrame:
    rows = []
    updated_at = pd.Timestamp(datetime.now())
    for theme in HOT_THEMES:
        for market in theme.markets:
            rows.append(
                {
                    "snapshot_date": snapshot_date,
                    "theme_name": theme.name,
                    "market": market,
                    "weight": theme.weight,
                    "keywords": json.dumps(theme.keywords, ensure_ascii=False),
                    "rationale": theme.rationale,
                    "source": theme.source,
                    "updated_at": updated_at,
                }
            )
    return pd.DataFrame(rows)


def _theme_matches(row: pd.Series, tag_text: str) -> list[HotTheme]:
    market = str(row["market"])
    symbol = str(row["symbol"])
    text = f"{row.get('name', '')} {tag_text}"
    matches: dict[str, HotTheme] = {}
    for theme in HOT_THEMES:
        if market not in theme.markets:
            continue
        if any(keyword.lower() in text.lower() for keyword in theme.keywords):
            matches[theme.name] = theme
    for theme_name in CURATED_THEME_OVERRIDES.get((market, symbol), ()):
        theme = next((item for item in HOT_THEMES if item.name == theme_name), None)
        if theme is not None:
            matches[theme.name] = theme
    return list(matches.values())


def _theme_score(matches: list[HotTheme]) -> float:
    if not matches:
        return 28.0
    score = 38 + sum(theme.weight * 18 for theme in matches[:4])
    return float(np.clip(score, 0, 100))


def _rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    valid = pd.to_numeric(series, errors="coerce")
    if valid.notna().sum() == 0:
        return pd.Series(50.0, index=series.index)
    return (valid.rank(pct=True, ascending=ascending) * 100).fillna(50).clip(0, 100)


def _group_rank(df: pd.DataFrame, column: str, group_columns: list[str]) -> pd.Series:
    values = pd.to_numeric(df[column], errors="coerce")
    if values.notna().sum() == 0:
        return pd.Series(50.0, index=df.index)
    peer_count = values.groupby([df[column] for column in group_columns]).transform("count")
    peer_rank = values.groupby([df[column] for column in group_columns]).rank(pct=True)
    market_rank = values.groupby(df["market"]).rank(pct=True)
    rank = peer_rank.where(peer_count >= 5, market_rank)
    return (rank * 100).fillna(50).clip(0, 100)


def _peer_scores(df: pd.DataFrame) -> pd.Series:
    fundamental = _group_rank(df, "fundamental_input_score", ["market", "industry_peer_group"])
    valuation = _group_rank(df, "valuation_score", ["market", "industry_peer_group"])
    technical = _group_rank(df, "technical_input_score", ["market", "industry_peer_group"])
    liquidity = _group_rank(df, "liquidity_score", ["market", "industry_peer_group"])
    score = fundamental * 0.45 + valuation * 0.25 + technical * 0.20 + liquidity * 0.10
    return score.clip(0, 100)


def run_expert_model(
    snapshots: pd.DataFrame,
    tags: pd.DataFrame,
    technicals: pd.DataFrame,
    fundamentals: pd.DataFrame,
    settings: Settings,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if snapshots.empty:
        return pd.DataFrame(), pd.DataFrame()

    snapshot_date = snapshots["trade_date"].max()
    df = snapshots[snapshots["trade_date"] == snapshot_date].copy()
    df = df.drop_duplicates(["market", "symbol"], keep="last")
    df = df.set_index(["market", "symbol"], drop=False)

    tag_text = (
        tags.groupby(["market", "symbol"])["tag_name"].apply(lambda values: " ".join(map(str, values)))
        if not tags.empty
        else pd.Series(dtype=object)
    )
    industry_tag = (
        tags[tags["tag_type"].eq("industry")]
        .sort_values(["market", "symbol", "tag_name"])
        .groupby(["market", "symbol"])["tag_name"]
        .first()
        if not tags.empty and "tag_type" in tags.columns
        else pd.Series(dtype=object)
    )
    tech = (
        technicals[technicals["snapshot_date"] == technicals["snapshot_date"].max()]
        .drop_duplicates(["market", "symbol"], keep="last")
        .set_index(["market", "symbol"])
        if not technicals.empty
        else pd.DataFrame()
    )
    fundamental = (
        fundamentals[fundamentals["snapshot_date"] == fundamentals["snapshot_date"].max()]
        .drop_duplicates(["market", "symbol"], keep="last")
        .set_index(["market", "symbol"])
        if not fundamentals.empty
        else pd.DataFrame()
    )

    df["valuation_score"] = _valuation_score(df)
    df["liquidity_score"] = _liquidity_score(df)
    df["market_cap_score"] = _rank(df["market_cap"], ascending=True)
    df["technical_input_score"] = (
        pd.to_numeric(tech["technical_score"], errors="coerce").reindex(df.index).fillna(42.0)
        if not tech.empty and "technical_score" in tech.columns
        else 42.0
    )
    df["fundamental_input_score"] = (
        pd.to_numeric(fundamental["fundamental_score"], errors="coerce").reindex(df.index).fillna(50.0)
        if not fundamental.empty and "fundamental_score" in fundamental.columns
        else 50.0
    )
    board_fallback = df["board"] if "board" in df.columns else df["market"]
    df["industry_peer_group"] = pd.Series(industry_tag, dtype=object).reindex(df.index)
    df["industry_peer_group"] = df["industry_peer_group"].fillna(board_fallback).fillna(df["market"])
    df["peer_score"] = _peer_scores(df)

    rows: list[dict[str, object]] = []
    updated_at = pd.Timestamp(datetime.now())
    for key, row in df.iterrows():
        symbol_tags = str(tag_text.get(key, ""))
        matches = _theme_matches(row, symbol_tags)
        theme_score = _theme_score(matches)

        tech_row = tech.loc[key] if not tech.empty and key in tech.index else None
        technical_score = float(tech_row["technical_score"]) if tech_row is not None else 42.0
        technical_signal = str(tech_row["technical_signal"]) if tech_row is not None else "missing_history"
        fundamental_row = fundamental.loc[key] if not fundamental.empty and key in fundamental.index else None
        fundamental_score = (
            float(fundamental_row["fundamental_score"]) if fundamental_row is not None else 50.0
        )

        penalty, risk_reasons = _risk_penalty(row, settings)
        if tech_row is None:
            penalty += 6
            risk_reasons.append("缺少历史日线，技术面降权")
        elif pd.notna(tech_row["rsi14"]) and float(tech_row["rsi14"]) > 78:
            penalty += 8
            risk_reasons.append("RSI 偏热，追高风险")
        elif pd.notna(tech_row["return_20d"]) and float(tech_row["return_20d"]) > 0.45:
            penalty += 8
            risk_reasons.append("20日涨幅过大，短线拥挤")
        if fundamental_row is None:
            penalty += 4
            risk_reasons.append("缺少财报基本面，基本面中性降权")
        else:
            warnings = str(fundamental_row.get("warnings") or "[]")
            if warnings not in {"[]", "", "None"}:
                risk_reasons.append(f"基本面预警={warnings}")

        valuation = float(row["valuation_score"])
        liquidity = float(row["liquidity_score"])
        cap = float(row["market_cap_score"])
        peer_score = float(row["peer_score"])
        industry_peer_group = str(row["industry_peer_group"])
        risk_inverse = 100 - min(penalty, 100)

        graham_value = valuation * 0.75 + (100 if any(t.name == "高股息央国企防御" for t in matches) else 45) * 0.25
        buffett_quality_proxy = liquidity * 0.40 + cap * 0.35 + risk_inverse * 0.25
        fisher_growth = theme_score * 0.60 + technical_score * 0.25 + liquidity * 0.15
        lynch_garp = theme_score * 0.40 + technical_score * 0.25 + valuation * 0.25 + liquidity * 0.10
        oneil_momentum = technical_score * 0.78 + liquidity * 0.22
        master_score = (
            graham_value * 0.18
            + buffett_quality_proxy * 0.22
            + fisher_growth * 0.22
            + lynch_garp * 0.18
            + oneil_momentum * 0.20
        )
        china_master_score = _china_master_score(
            valuation=valuation,
            liquidity=liquidity,
            risk_inverse=risk_inverse,
            theme_score=theme_score,
            technical_score=technical_score,
            fundamental_score=fundamental_score,
            matches=matches,
        )
        expert_score = (
            master_score * 0.19
            + china_master_score * 0.27
            + fundamental_score * 0.17
            + theme_score * 0.17
            + technical_score * 0.11
            + liquidity * 0.03
            + peer_score * 0.06
            - penalty
        )
        expert_score = float(np.clip(expert_score, 0, 100))

        if penalty >= 80 or expert_score < 42:
            decision = "reject"
        elif expert_score >= 68 and theme_score >= 55 and technical_score >= 55:
            decision = "core_candidate"
        elif expert_score >= 56:
            decision = "watchlist"
        else:
            decision = "reserve"

        reason_parts = [
            f"大师框架分={master_score:.1f}",
            f"中国大师框架分={china_master_score:.1f}",
            f"基本面分={fundamental_score:.1f}",
            f"同类分位={peer_score:.1f}",
            f"同类组={industry_peer_group}",
            f"主题分={theme_score:.1f}",
            f"技术信号={technical_signal}",
        ]
        if matches:
            reason_parts.append("匹配主题=" + "、".join(theme.name for theme in matches))
        if risk_reasons:
            reason_parts.extend(risk_reasons)

        rows.append(
            {
                "snapshot_date": snapshot_date,
                "strategy": STRATEGY_NAME,
                "market": row["market"],
                "symbol": row["symbol"],
                "name": row["name"],
                "expert_score": expert_score,
                "master_score": float(np.clip(master_score, 0, 100)),
                "china_master_score": float(np.clip(china_master_score, 0, 100)),
                "fundamental_score": fundamental_score,
                "industry_peer_group": industry_peer_group,
                "peer_score": peer_score,
                "theme_score": theme_score,
                "technical_score": technical_score,
                "liquidity_score": liquidity,
                "valuation_score": valuation,
                "risk_score": float(np.clip(penalty, 0, 100)),
                "decision": decision,
                "theme_matches": json.dumps([theme.name for theme in matches], ensure_ascii=False),
                "reasons": json.dumps(reason_parts, ensure_ascii=False),
                "updated_at": updated_at,
            }
        )

    return pd.DataFrame(rows), hot_theme_definitions_df(snapshot_date)


def _china_master_score(
    valuation: float,
    liquidity: float,
    risk_inverse: float,
    theme_score: float,
    technical_score: float,
    fundamental_score: float,
    matches: list[HotTheme],
) -> float:
    theme_names = {theme.name for theme in matches}
    high_dividend = 100.0 if "高股息央国企防御" in theme_names else 45.0
    cycle_fit = 90.0 if theme_names.intersection({"资源涨价与安全资产", "电力储能与能源转型"}) else 50.0
    growth_fit = 90.0 if theme_names.intersection(
        {"AI算力硬件", "半导体国产替代", "创新药与医疗科技", "港股AI互联网平台", "汽车智能化与出海"}
    ) else 50.0

    zhang_lei_long_term = (
        fundamental_score * 0.38 + theme_score * 0.32 + liquidity * 0.12 + risk_inverse * 0.18
    )
    qiu_guolu_quality_value = (
        valuation * 0.34 + fundamental_score * 0.34 + risk_inverse * 0.22 + high_dividend * 0.10
    )
    dan_bin_lin_yuan_compounder = fundamental_score * 0.55 + growth_fit * 0.20 + risk_inverse * 0.25
    deng_xiaofeng_cycle_quality = (
        fundamental_score * 0.30 + valuation * 0.22 + cycle_fit * 0.22 + technical_score * 0.16 + risk_inverse * 0.10
    )
    chen_guangming_balanced = (
        fundamental_score * 0.34 + theme_score * 0.22 + technical_score * 0.18 + liquidity * 0.16 + risk_inverse * 0.10
    )
    score = (
        zhang_lei_long_term * 0.24
        + qiu_guolu_quality_value * 0.22
        + dan_bin_lin_yuan_compounder * 0.18
        + deng_xiaofeng_cycle_quality * 0.16
        + chen_guangming_balanced * 0.20
    )
    return float(np.clip(score, 0, 100))


def _theme_list(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item]


def _primary_bucket(themes: list[str]) -> str:
    if "高股息央国企防御" in themes and "资源涨价与安全资产" in themes:
        return "高股息资源防御"
    for theme in THEME_PRIORITY:
        if theme in themes:
            return theme
    return str(themes[0]) if themes else "未匹配主题"


def _style_bucket(row: pd.Series, themes: list[str]) -> str:
    if "高股息央国企防御" in themes:
        return "红利防御"
    if "资源涨价与安全资产" in themes:
        return "资源周期"
    if "创新药与医疗科技" in themes:
        return "医药成长"
    if "汽车智能化与出海" in themes:
        return "智能汽车"
    if "电力储能与能源转型" in themes:
        return "能源转型"
    if any(theme in themes for theme in ("AI算力硬件", "半导体国产替代", "港股AI互联网平台", "人形机器人与高端制造")):
        if float(row.get("valuation_score", 50) or 50) >= 65:
            return "科技成长偏估值"
        return "科技成长"
    if float(row.get("fundamental_score", 50) or 50) >= 70:
        return "质量成长"
    if float(row.get("valuation_score", 50) or 50) >= 70:
        return "低估值修复"
    return "综合候选"


def _normalized_name_key(name: object) -> str | None:
    text = str(name or "").strip()
    if not text:
        return None
    text = re.sub(r"(股份有限公司|有限公司|控股|集团|股份|有限|公司|[-－]?[WHSA]+$|\s+)", "", text, flags=re.IGNORECASE)
    return text if len(text) >= 2 else None


def _peer_group(row: pd.Series) -> str:
    market = str(row["market"])
    symbol = str(row["symbol"]).zfill(5 if market == "HK" else 6)
    mapped = DUAL_LISTING_GROUPS.get((market, symbol))
    if mapped:
        return mapped
    name_key = _normalized_name_key(row.get("name"))
    if name_key:
        return f"name:{name_key}"
    return f"{market}:{symbol}"


def _select_diverse_bucket(group: pd.DataFrame, max_per_bucket: int, max_per_style: int) -> list[int]:
    selected: list[int] = []
    style_counts: dict[str, int] = {}

    for idx, row in group.iterrows():
        style = str(row["style_bucket"])
        if style_counts.get(style, 0) >= max_per_style:
            continue
        selected.append(idx)
        style_counts[style] = style_counts.get(style, 0) + 1
        if len(selected) >= max_per_bucket:
            return selected

    for idx in group.index:
        if idx in selected:
            continue
        selected.append(idx)
        if len(selected) >= max_per_bucket:
            break
    return selected


def refine_candidates(results: pd.DataFrame, max_per_bucket: int = 3, max_per_style: int = 2) -> pd.DataFrame:
    if results.empty:
        return pd.DataFrame()

    candidates = results[results["decision"].isin(["core_candidate", "watchlist", "reserve"])].copy()
    candidates = candidates[candidates["expert_score"] >= 56]
    if candidates.empty:
        return pd.DataFrame()

    candidates["theme_list"] = candidates["theme_matches"].apply(_theme_list)
    candidates["bucket"] = candidates["theme_list"].apply(_primary_bucket)
    candidates["style_bucket"] = candidates.apply(lambda row: _style_bucket(row, row["theme_list"]), axis=1)
    candidates["peer_group"] = candidates.apply(_peer_group, axis=1)
    for column, default in [("peer_score", 50.0), ("industry_peer_group", "")]:
        if column not in candidates.columns:
            candidates[column] = default
    candidates = candidates.sort_values(
        ["expert_score", "peer_score", "fundamental_score", "technical_score", "liquidity_score"],
        ascending=[False, False, False, False, False],
    ).drop_duplicates(["snapshot_date", "strategy", "peer_group"], keep="first")

    candidates = candidates.sort_values(
        ["bucket", "expert_score", "peer_score", "fundamental_score", "technical_score", "liquidity_score"],
        ascending=[True, False, False, False, False, False],
    )
    selected_indices: list[int] = []
    for _, group in candidates.groupby("bucket", sort=True):
        selected_indices.extend(_select_diverse_bucket(group, max_per_bucket, max_per_style))

    refined = candidates.loc[selected_indices].copy()
    refined = refined.sort_values(
        ["bucket", "expert_score", "peer_score", "fundamental_score", "technical_score", "liquidity_score"],
        ascending=[True, False, False, False, False, False],
    )
    refined["rank_in_bucket"] = refined.groupby("bucket").cumcount() + 1
    refined["selection_note"] = refined.apply(
        lambda row: (
            f"同主题最多{max_per_bucket}只；同风格优先最多{max_per_style}只；"
            f"A/H或同名主体只留最高分；主体={row['peer_group']}；风格={row['style_bucket']}；"
            f"同类组={row['industry_peer_group']}；同类分位={float(row['peer_score']):.1f}"
        ),
        axis=1,
    )
    refined["updated_at"] = pd.Timestamp(datetime.now())
    return refined[
        [
            "snapshot_date",
            "strategy",
            "bucket",
            "rank_in_bucket",
            "peer_group",
            "style_bucket",
            "market",
            "symbol",
            "name",
            "expert_score",
            "fundamental_score",
            "technical_score",
            "industry_peer_group",
            "peer_score",
            "theme_matches",
            "reasons",
            "selection_note",
            "updated_at",
        ]
    ].reset_index(drop=True)
