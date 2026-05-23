from __future__ import annotations

import json
from html import escape

import pandas as pd
import streamlit as st

from ah_screener.classification import enrich_security_metadata
from ah_screener.config import get_settings
from ah_screener.etf_model import enrich_etf_snapshot
from ah_screener.selection import dedup_etf_pool
from ah_screener.universe import ETFS, select_assets
from ah_screener.expert_model import STRATEGY_NAME
from ah_screener.pipeline import coverage_status
from ah_screener.storage import Store


st.set_page_config(page_title="A/H/US Research Desk", layout="wide", initial_sidebar_state="expanded")


DESK_CSS = """
<style>
:root {
  --bg: #f4efe4;
  --surface: #fffaf0;
  --surface-2: #ece2cf;
  --ink: #191511;
  --muted: #756a5c;
  --line: #d5c5aa;
  --line-dark: #a88f68;
  --accent: #7b261f;
  --accent-2: #a86a2a;
  --green: #2f5a43;
  --blue: #284b63;
  --shadow: rgba(55, 42, 27, 0.12);
}

.stApp {
  color: var(--ink);
  background:
    linear-gradient(180deg, rgba(123, 38, 31, 0.08), transparent 260px),
    radial-gradient(circle at top left, rgba(168, 106, 42, 0.11), transparent 330px),
    var(--bg);
}

header,
header[data-testid="stHeader"],
div[data-testid="stToolbar"],
div[data-testid="stDecoration"],
div[data-testid="stStatusWidget"],
#MainMenu,
footer {
  display: none !important;
  height: 0 !important;
  min-height: 0 !important;
  visibility: hidden !important;
}

div[data-testid="stAppViewContainer"],
section.main,
main,
.stMain {
  padding-top: 0 !important;
  margin-top: 0 !important;
}

div[data-testid="stAppViewBlockContainer"],
section[data-testid="stSidebar"] > div:first-child {
  padding-top: 0 !important;
  margin-top: 0 !important;
}

.block-container {
  max-width: 1540px;
  padding: 0 1.4rem 2.6rem !important;
}

section[data-testid="stSidebar"] {
  background: #211a17;
  border-right: 1px solid #4e3e32;
}

section[data-testid="stSidebar"] * {
  color: #f7ead2;
}

section[data-testid="stSidebar"] div[data-baseweb="select"] span,
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] label p {
  color: #f7ead2;
}

section[data-testid="stSidebar"] div[data-baseweb="select"] > div,
section[data-testid="stSidebar"] input {
  background: #30251f;
  border-color: #66513f;
}

h1, h2, h3 {
  color: var(--ink);
  font-family: Georgia, "Noto Serif SC", "Times New Roman", serif;
  letter-spacing: 0;
}

h2 {
  font-size: 1.12rem;
  margin: 0.3rem 0 0.5rem;
}

div[data-testid="stTabs"] button {
  border-radius: 0;
  color: var(--ink);
  font-size: 0.92rem;
  background: transparent;
  border-bottom: 2px solid transparent;
}

div[data-testid="stTabs"] button[aria-selected="true"] {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.desk-hero {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 1rem;
  align-items: stretch;
  margin-bottom: 0.95rem;
}

.desk-title {
  background: rgba(255, 250, 240, 0.92);
  border: 1px solid var(--line);
  border-top: 4px solid var(--accent);
  box-shadow: 0 16px 34px var(--shadow);
  padding: 1.05rem 1.15rem 1rem;
}

.desk-title h1 {
  margin: 0.08rem 0 0.38rem;
  font-size: clamp(1.85rem, 3vw, 3rem);
  line-height: 1.02;
}

.eyebrow {
  color: var(--accent-2);
  font-size: 0.74rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.desk-subtitle {
  color: var(--muted);
  max-width: 920px;
  font-size: 0.94rem;
}

.desk-status {
  min-width: 245px;
  background: #211a17;
  color: #f7ead2;
  border: 1px solid #4e3e32;
  box-shadow: 0 16px 34px var(--shadow);
  padding: 1rem;
}

.desk-status .status-label {
  color: #d1b98e;
  font-size: 0.76rem;
  margin-bottom: 0.38rem;
}

.desk-status .status-value {
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 1.05rem;
  line-height: 1.35;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(128px, 1fr));
  gap: 0.72rem;
  margin: 0.8rem 0 1rem;
}

.kpi {
  background: rgba(255, 250, 240, 0.94);
  border: 1px solid var(--line);
  box-shadow: 0 10px 22px var(--shadow);
  padding: 0.82rem 0.88rem;
}

.kpi .label {
  color: var(--muted);
  font-size: 0.76rem;
}

.kpi .value {
  margin-top: 0.25rem;
  color: var(--ink);
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 1.55rem;
  line-height: 1;
}

.panel {
  background: rgba(255, 250, 240, 0.94);
  border: 1px solid var(--line);
  box-shadow: 0 10px 24px var(--shadow);
  padding: 0.95rem;
  margin-bottom: 0.9rem;
}

.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.8rem;
  margin-bottom: 0.65rem;
  border-bottom: 1px solid var(--line);
  padding-bottom: 0.55rem;
}

.panel-title strong {
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 1.05rem;
}

.hint {
  color: var(--muted);
  font-size: 0.78rem;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(255px, 1fr));
  gap: 0.75rem;
}

.candidate-card {
  background: #fffdf7;
  border: 1px solid var(--line);
  border-left: 4px solid var(--accent);
  padding: 0.82rem;
}

.candidate-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.65rem;
  margin-bottom: 0.52rem;
}

.candidate-name {
  font-family: Georgia, "Noto Serif SC", serif;
  font-weight: 700;
  font-size: 1.02rem;
}

.candidate-meta {
  color: var(--muted);
  font-size: 0.76rem;
  margin-top: 0.12rem;
}

.score {
  min-width: 54px;
  text-align: center;
  background: var(--accent);
  color: #fff7e6;
  padding: 0.25rem 0.35rem;
  font-family: Georgia, "Times New Roman", serif;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.34rem;
}

.chip {
  border: 1px solid var(--line-dark);
  background: #f1e6d2;
  color: #33271d;
  padding: 0.14rem 0.38rem;
  font-size: 0.74rem;
  white-space: nowrap;
}

.chip.green {
  border-color: #87a18b;
  background: #dfe9dd;
  color: #1f3f2e;
}

.chip.red {
  border-color: #b98580;
  background: #f0d8d4;
  color: #69251f;
}

.stDataFrame {
  border: 1px solid var(--line);
  box-shadow: 0 8px 18px var(--shadow);
}

div[data-testid="stAlert"] {
  background: #fff4d8;
  border: 1px solid var(--line-dark);
  color: var(--ink);
}

@media (max-width: 980px) {
  .desk-hero {
    grid-template-columns: 1fr;
  }
  .desk-status {
    min-width: 0;
  }
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(128px, 1fr));
  }
}
</style>
"""


META_COLUMNS = ["asset_type", "board", "exchange", "status", "is_st", "is_hk_connect"]


def _json_list(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return str(value)
    if isinstance(parsed, list):
        return "、".join(str(item) for item in parsed if item)
    return str(parsed)


def _safe(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return escape(str(value), quote=True)


def _score(value: object) -> str:
    if value is None or pd.isna(value):
        return "--"
    return f"{float(value):.1f}"


def _amount(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    number = float(value)
    if abs(number) >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿"
    if abs(number) >= 10_000:
        return f"{number / 10_000:.1f}万"
    return f"{number:.0f}"


def _pct(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.2f}%"


def _asset_label(value: object) -> str:
    return "ETF" if str(value).lower() == "etf" else "股票"


def latest_frame(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df.empty or column not in df.columns:
        return df.copy()
    return df[df[column] == df[column].max()].copy()


@st.cache_data(ttl=300)
def load_table(table: str) -> pd.DataFrame:
    store = Store(get_settings().db_path)
    return store.query_df(f"SELECT * FROM {table}")


def load_securities() -> pd.DataFrame:
    try:
        securities = load_table("securities")
    except Exception:
        return pd.DataFrame()
    if securities.empty:
        return securities
    return enrich_security_metadata(securities)


def with_metadata(df: pd.DataFrame, securities: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    enriched = df.copy()
    if securities.empty:
        enriched["asset_type"] = enriched.get("asset_type", "stock")
        enriched["board"] = enriched.get("board", "未分类")
        enriched["is_st"] = enriched.get("is_st", False)
        enriched["is_hk_connect"] = enriched.get("is_hk_connect", False)
        return enriched

    for column in META_COLUMNS:
        if column in enriched.columns:
            enriched = enriched.drop(columns=[column])
    metadata = securities[["market", "symbol", *META_COLUMNS]].drop_duplicates(["market", "symbol"])
    enriched = enriched.merge(metadata, on=["market", "symbol"], how="left")
    enriched["asset_type"] = enriched["asset_type"].fillna("stock")
    enriched["board"] = enriched["board"].fillna("未分类")
    enriched["is_st"] = enriched["is_st"].fillna(False).astype(bool)
    enriched["is_hk_connect"] = enriched["is_hk_connect"].fillna(False).astype(bool)
    return enriched


def load_market_view(securities: pd.DataFrame) -> pd.DataFrame:
    try:
        snapshots = load_table("market_snapshots")
    except Exception:
        return pd.DataFrame()
    snapshots = latest_frame(snapshots, "trade_date")
    return with_metadata(snapshots, securities)


def load_expert_view(securities: pd.DataFrame) -> pd.DataFrame:
    try:
        expert = load_table("expert_screening_results")
    except Exception:
        return pd.DataFrame()
    expert = expert[expert.get("strategy", "") == STRATEGY_NAME] if not expert.empty else expert
    expert = latest_frame(expert, "snapshot_date")
    return with_metadata(expert, securities)


def load_refined_view(securities: pd.DataFrame) -> pd.DataFrame:
    try:
        refined = load_table("refined_candidates")
    except Exception:
        return pd.DataFrame()
    refined = refined[refined.get("strategy", "") == STRATEGY_NAME] if not refined.empty else refined
    refined = latest_frame(refined, "snapshot_date")
    return with_metadata(refined, securities)


def load_fundamental_view(securities: pd.DataFrame) -> pd.DataFrame:
    try:
        fundamentals = load_table("financial_metrics")
    except Exception:
        return pd.DataFrame()
    fundamentals = latest_frame(fundamentals, "snapshot_date")
    return with_metadata(fundamentals, securities)


@st.cache_data(ttl=300)
def load_coverage_view() -> pd.DataFrame:
    try:
        return coverage_status()
    except Exception:
        return pd.DataFrame()


def apply_common_filters(df: pd.DataFrame) -> pd.DataFrame:
    filtered = df.copy()
    if filtered.empty:
        return filtered
    if market_filter and "market" in filtered.columns:
        filtered = filtered[filtered["market"].isin(market_filter)]
    if asset_filter and "asset_type" in filtered.columns:
        filtered = filtered[filtered["asset_type"].isin(asset_filter)]
    if board_filter and "board" in filtered.columns:
        filtered = filtered[filtered["board"].isin(board_filter)]
    if risk_filter == "排除 ST/退":
        filtered = filtered[~filtered.get("is_st", pd.Series(False, index=filtered.index)).astype(bool)]
    elif risk_filter == "仅 ST/退":
        filtered = filtered[filtered.get("is_st", pd.Series(False, index=filtered.index)).astype(bool)]
    if search_text:
        text = search_text.strip().lower()
        haystack = (
            filtered.get("symbol", pd.Series("", index=filtered.index)).astype(str).str.lower()
            + " "
            + filtered.get("name", pd.Series("", index=filtered.index)).astype(str).str.lower()
        )
        filtered = filtered[haystack.str.contains(text, regex=False)]
    return filtered


def render_hero(snapshot_text: str, universe_count: int, refined_count: int) -> None:
    st.markdown(
        f"""
        <div class="desk-hero">
          <div class="desk-title">
            <div class="eyebrow">A/H/US Research Desk</div>
            <h1>A/H/US 股票与 ETF 研究台</h1>
            <div class="desk-subtitle">
              A 股按主板、创业板、科创板、北交所、ST/退市风险拆分；港股标记港股通；ETF 单独成池。
            </div>
          </div>
          <div class="desk-status">
            <div class="status-label">当前快照</div>
            <div class="status-value">{_safe(snapshot_text)}</div>
            <div class="status-label" style="margin-top:0.7rem;">策略</div>
            <div class="status-value">{_safe(STRATEGY_NAME)}</div>
            <div class="status-label" style="margin-top:0.7rem;">覆盖</div>
            <div class="status-value">{universe_count:,} / 精选 {refined_count:,}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpis(market_df: pd.DataFrame, expert_df: pd.DataFrame, refined_df: pd.DataFrame) -> None:
    stock_df = market_df[market_df["asset_type"].eq("stock")] if not market_df.empty else market_df
    a_stock = len(stock_df[stock_df["market"].eq("A")]) if not stock_df.empty else 0
    hk_stock = len(stock_df[stock_df["market"].eq("HK")]) if not stock_df.empty else 0
    etfs = len(market_df[market_df["asset_type"].eq("etf")]) if not market_df.empty else 0
    hk_connect = int(market_df.get("is_hk_connect", pd.Series(dtype=bool)).fillna(False).sum())
    st_count = int(market_df.get("is_st", pd.Series(dtype=bool)).fillna(False).sum())
    core = int(expert_df["decision"].eq("core_candidate").sum()) if not expert_df.empty else 0
    cells = [
        ("A 股股票", a_stock),
        ("港股股票", hk_stock),
        ("ETF", etfs),
        ("港股通", hk_connect),
        ("ST/退风险", st_count),
        ("核心候选", core or len(refined_df)),
    ]
    html = "".join(f'<div class="kpi"><div class="label">{label}</div><div class="value">{value:,}</div></div>' for label, value in cells)
    st.markdown(f'<div class="kpi-grid">{html}</div>', unsafe_allow_html=True)


def render_conclusion(refined_df: pd.DataFrame, expert_df: pd.DataFrame) -> None:
    """Data-driven one-glance stance: candidate counts + top theme directions."""
    core = int(expert_df["decision"].eq("core_candidate").sum()) if not expert_df.empty else 0
    watch = int(expert_df["decision"].eq("watchlist").sum()) if not expert_df.empty else 0
    directions: list[str] = []
    if not refined_df.empty and {"bucket", "expert_score"}.issubset(refined_df.columns):
        grouped = (
            refined_df.assign(score=pd.to_numeric(refined_df["expert_score"], errors="coerce"))
            .groupby("bucket")
            .agg(n=("symbol", "nunique"), score=("score", "mean"))
            .sort_values(["n", "score"], ascending=False)
            .head(3)
        )
        directions = [
            f"{_safe(bucket)}（{int(row['n'])}只·均分{row['score']:.0f}）"
            for bucket, row in grouped.iterrows()
        ]
    dir_html = " &nbsp;·&nbsp; ".join(directions) if directions else "暂无提炼方向，先运行 expert-score"
    st.markdown(
        f"""
        <div class="panel">
          <div class="panel-title"><strong>当前结论</strong><span class="hint">数据驱动</span></div>
          <div style="padding:0.4rem 0.2rem;line-height:1.7;">
            <span class="chip green">核心候选 {core}</span>
            <span class="chip">观察 {watch}</span><br>
            <span style="opacity:0.7;">重点方向：</span>{dir_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_candidate_cards(df: pd.DataFrame, limit: int = 8) -> None:
    if df.empty:
        st.info("暂无精选候选。")
        return
    cards = []
    for _, row in df.sort_values("expert_score", ascending=False).head(limit).iterrows():
        themes = [item for item in _json_list(row.get("theme_matches")).split("、") if item]
        theme_html = "".join(f'<span class="chip green">{_safe(item)}</span>' for item in themes[:3])
        st_chip = '<span class="chip red">ST/退</span>' if bool(row.get("is_st")) else ""
        cards.append(
            '<div class="candidate-card">'
            '<div class="candidate-head">'
            "<div>"
            f'<div class="candidate-name">{_safe(row.get("name"))}</div>'
            f'<div class="candidate-meta">{_safe(row.get("market"))} · {_safe(row.get("symbol"))} · {_safe(row.get("board"))}</div>'
            "</div>"
            f'<div class="score">{_score(row.get("expert_score"))}</div>'
            "</div>"
            '<div class="chip-row">'
            f'<span class="chip">{_safe(row.get("style_bucket"))}</span>'
            f'<span class="chip">基本面 {_score(row.get("fundamental_score"))}</span>'
            f'<span class="chip">技术 {_score(row.get("technical_score"))}</span>'
            f'<span class="chip">同类 {_score(row.get("peer_score"))}</span>'
            f'<span class="chip">行业 {_score(row.get("industry_fit_score"))}</span>'
            f"{st_chip}{theme_html}"
            "</div>"
            "</div>"
        )
    st.markdown(f'<div class="card-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def display_refined(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    for column, default in [
        ("peer_score", pd.NA),
        ("industry_fit_score", pd.NA),
        ("industry_peer_group", ""),
    ]:
        if column not in df.columns:
            df[column] = default
    out = df[
        [
            "bucket",
            "rank_in_bucket",
            "style_bucket",
            "market",
            "board",
            "symbol",
            "name",
            "expert_score",
            "fundamental_score",
            "technical_score",
            "peer_score",
            "industry_fit_score",
            "industry_peer_group",
            "theme_matches",
            "selection_note",
        ]
    ].copy()
    out["theme_matches"] = out["theme_matches"].map(_json_list)
    return out.rename(
        columns={
            "bucket": "主题",
            "rank_in_bucket": "排名",
            "style_bucket": "风格",
            "market": "市场",
            "board": "板块",
            "symbol": "代码",
            "name": "名称",
            "expert_score": "专家分",
            "fundamental_score": "基本面",
            "technical_score": "技术面",
            "peer_score": "同类分位",
            "industry_fit_score": "行业适配",
            "industry_peer_group": "同类组",
            "theme_matches": "主题匹配",
            "selection_note": "提炼逻辑",
        }
    )


def display_expert(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    for column, default in [
        ("peer_score", pd.NA),
        ("industry_fit_score", pd.NA),
        ("industry_peer_group", ""),
    ]:
        if column not in df.columns:
            df[column] = default
    out = df[
        [
            "market",
            "board",
            "symbol",
            "name",
            "expert_score",
            "decision",
            "fundamental_score",
            "china_master_score",
            "technical_score",
            "peer_score",
            "industry_fit_score",
            "industry_peer_group",
            "theme_matches",
            "reasons",
        ]
    ].copy()
    out["theme_matches"] = out["theme_matches"].map(_json_list)
    out["reasons"] = out["reasons"].map(_json_list)
    return out.rename(
        columns={
            "market": "市场",
            "board": "板块",
            "symbol": "代码",
            "name": "名称",
            "expert_score": "专家分",
            "decision": "决策",
            "fundamental_score": "基本面",
            "china_master_score": "中国大师框架",
            "technical_score": "技术面",
            "peer_score": "同类分位",
            "industry_fit_score": "行业适配",
            "industry_peer_group": "同类组",
            "theme_matches": "主题匹配",
            "reasons": "理由",
        }
    )


def display_market(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df[
        [
            "market",
            "asset_type",
            "board",
            "symbol",
            "name",
            "last_price",
            "pct_change",
            "amount",
            "turnover_rate",
            "pe_ttm",
            "pb",
            "market_cap",
            "is_hk_connect",
            "is_st",
        ]
    ].copy()
    out["asset_type"] = out["asset_type"].map(_asset_label)
    out["amount"] = out["amount"].map(_amount)
    out["market_cap"] = out["market_cap"].map(_amount)
    out["pct_change"] = out["pct_change"].map(_pct)
    out["turnover_rate"] = out["turnover_rate"].map(_pct)
    out["is_hk_connect"] = out["is_hk_connect"].map(lambda value: "是" if value else "")
    out["is_st"] = out["is_st"].map(lambda value: "是" if value else "")
    return out.rename(
        columns={
            "market": "市场",
            "asset_type": "类型",
            "board": "板块",
            "symbol": "代码",
            "name": "名称",
            "last_price": "最新价",
            "pct_change": "涨跌幅",
            "amount": "成交额",
            "turnover_rate": "换手率",
            "pe_ttm": "PE",
            "pb": "PB",
            "market_cap": "市值/规模",
            "is_hk_connect": "港股通",
            "is_st": "ST/退",
        }
    )


def display_etf(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df[
        [
            "market",
            "symbol",
            "name",
            "etf_category",
            "etf_keyword",
            "etf_score",
            "etf_liquidity_score",
            "etf_recommendation",
            "last_price",
            "pct_change",
            "amount",
            "market_cap",
        ]
    ].copy()
    out["amount"] = out["amount"].map(_amount)
    out["market_cap"] = out["market_cap"].map(_amount)
    out["pct_change"] = out["pct_change"].map(_pct)
    return out.rename(
        columns={
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "etf_category": "分类",
            "etf_keyword": "识别词",
            "etf_score": "ETF分",
            "etf_liquidity_score": "流动性",
            "etf_recommendation": "建议",
            "last_price": "最新价",
            "pct_change": "涨跌幅",
            "amount": "成交额",
            "market_cap": "规模",
        }
    )


def display_etf_dedup(df: pd.DataFrame) -> pd.DataFrame:
    """Double-layer de-duplicated ETF leaders, with fold provenance."""
    if df is None or df.empty:
        return pd.DataFrame()
    cols = [
        "market", "symbol", "name", "etf_cluster", "etf_track", "etf_score",
        "peer_count", "etf_recommendation", "pct_change", "amount", "peer_alternatives",
    ]
    out = df[[c for c in cols if c in df.columns]].copy()
    out["amount"] = out["amount"].map(_amount)
    out["pct_change"] = out["pct_change"].map(_pct)
    if "peer_count" in out.columns:
        out["peer_count"] = pd.to_numeric(out["peer_count"], errors="coerce").fillna(1).astype(int)
    return out.rename(
        columns={
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "etf_cluster": "簇",
            "etf_track": "跟踪",
            "etf_score": "ETF分",
            "peer_count": "同组数",
            "etf_recommendation": "建议",
            "pct_change": "涨跌幅",
            "amount": "成交额",
            "peer_alternatives": "同类备选",
        }
    )


def display_potential(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    cols = [
        "market", "symbol", "name", "potential_score", "technical_setup_score",
        "relative_strength_score", "pivot_price", "target_price", "stop_price",
        "rr_ratio", "hist_win_rate", "bias_note",
    ]
    out = df[[c for c in cols if c in df.columns]].copy()
    return out.rename(
        columns={
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "potential_score": "潜力分",
            "technical_setup_score": "筑底",
            "relative_strength_score": "RS",
            "pivot_price": "触发价",
            "target_price": "目标价",
            "stop_price": "止损价",
            "rr_ratio": "RR",
            "hist_win_rate": "历史胜率",
            "bias_note": "偏差说明",
        }
    )


def display_coverage(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.rename(
        columns={
            "market": "市场",
            "asset_type": "类型",
            "board": "板块",
            "universe": "证券数",
            "technical_covered": "技术覆盖",
            "technical_pct": "技术覆盖率",
            "fundamental_covered": "基本面覆盖",
            "fundamental_pct": "基本面覆盖率",
            "expert_covered": "专家覆盖",
            "expert_pct": "专家覆盖率",
        }
    )


def display_fundamentals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    for column in [
        "revenue_cagr_3y",
        "net_profit_cagr_3y",
        "roe_avg_3y",
        "roe_stability_score",
        "fundamental_trend_score",
        "rd_expense_ratio",
        "capex_to_revenue",
        "capex_to_operating_cashflow",
        "innovation_efficiency_score",
    ]:
        if column not in df.columns:
            df[column] = pd.NA
    out = df[
        [
            "market",
            "board",
            "symbol",
            "name",
            "report_date",
            "fundamental_score",
            "roe",
            "revenue_yoy",
            "net_profit_yoy",
            "revenue_cagr_3y",
            "net_profit_cagr_3y",
            "roe_avg_3y",
            "roe_stability_score",
            "fundamental_trend_score",
            "rd_expense_ratio",
            "capex_to_revenue",
            "capex_to_operating_cashflow",
            "innovation_efficiency_score",
            "debt_asset_ratio",
            "cashflow_to_profit",
            "warnings",
        ]
    ].copy()
    out["warnings"] = out["warnings"].map(_json_list)
    return out.rename(
        columns={
            "market": "市场",
            "board": "板块",
            "symbol": "代码",
            "name": "名称",
            "report_date": "报告期",
            "fundamental_score": "基本面分",
            "roe": "ROE",
            "revenue_yoy": "收入同比",
            "net_profit_yoy": "利润同比",
            "revenue_cagr_3y": "收入CAGR",
            "net_profit_cagr_3y": "利润CAGR",
            "roe_avg_3y": "ROE均值",
            "roe_stability_score": "ROE稳定",
            "fundamental_trend_score": "多期趋势",
            "rd_expense_ratio": "研发费用率",
            "capex_to_revenue": "资本开支/收入",
            "capex_to_operating_cashflow": "资本开支/经营现金流",
            "innovation_efficiency_score": "研发资本效率",
            "debt_asset_ratio": "资产负债率",
            "cashflow_to_profit": "现金流/利润",
            "warnings": "预警",
        }
    )


st.markdown(DESK_CSS, unsafe_allow_html=True)

securities = load_securities()
market_view = load_market_view(securities)
expert_view = load_expert_view(securities)
refined_view = load_refined_view(securities)
fundamental_view = load_fundamental_view(securities)
coverage_view = load_coverage_view()

if market_view.empty and expert_view.empty and refined_view.empty:
    st.info("暂无数据。请先运行 ah-screener sync-spot --market all。")
    st.stop()

snapshot_text = "暂无"
if not market_view.empty and "trade_date" in market_view.columns:
    snapshot_text = str(market_view["trade_date"].max()).split(" ")[0]
elif not expert_view.empty and "snapshot_date" in expert_view.columns:
    snapshot_text = str(expert_view["snapshot_date"].max()).split(" ")[0]

st.sidebar.markdown("## 筛选")
market_options = sorted(market_view.get("market", pd.Series(dtype=object)).dropna().unique())
market_filter = st.sidebar.multiselect("市场", market_options, default=market_options)

asset_options = sorted(market_view.get("asset_type", pd.Series(dtype=object)).dropna().unique())
asset_filter = st.sidebar.multiselect("类型", asset_options, default=asset_options)

board_options = sorted(market_view.get("board", pd.Series(dtype=object)).dropna().unique())
board_filter = st.sidebar.multiselect("板块", board_options, default=board_options)

risk_filter = st.sidebar.selectbox("风险状态", ["全部", "排除 ST/退", "仅 ST/退"], index=1)
search_text = st.sidebar.text_input("代码或名称")
st.sidebar.caption("↑ 市场/类型/板块/风险/搜索：作用于全部页签")
min_expert_score = st.sidebar.slider("专家最低分", 0, 100, 55)
min_fundamental_score = st.sidebar.slider("基本面最低分", 0, 100, 0)
decision_options = (
    sorted(expert_view.get("decision", pd.Series(dtype=object)).dropna().unique())
    if not expert_view.empty
    else []
)
default_decisions = [item for item in ["core_candidate", "watchlist"] if item in decision_options]
decision_filter = st.sidebar.multiselect("专家决策", decision_options, default=default_decisions)
st.sidebar.caption("↑ 专家分/基本面分/决策：仅作用于 精选 · 股票池 · 基本面（潜力页有独立阈值）")

filtered_market = apply_common_filters(market_view)
filtered_expert = apply_common_filters(expert_view)
filtered_refined = apply_common_filters(refined_view)
filtered_fundamentals = apply_common_filters(fundamental_view)

if not filtered_expert.empty:
    filtered_expert = filtered_expert[
        (pd.to_numeric(filtered_expert["expert_score"], errors="coerce") >= min_expert_score)
        & (
            pd.to_numeric(filtered_expert["fundamental_score"], errors="coerce").fillna(0)
            >= min_fundamental_score
        )
    ]
    if decision_filter:
        filtered_expert = filtered_expert[filtered_expert["decision"].isin(decision_filter)]

if not filtered_refined.empty:
    filtered_refined = filtered_refined[
        (pd.to_numeric(filtered_refined["expert_score"], errors="coerce") >= min_expert_score)
        & (
            pd.to_numeric(filtered_refined["fundamental_score"], errors="coerce").fillna(0)
            >= min_fundamental_score
        )
    ]

if not filtered_fundamentals.empty:
    filtered_fundamentals = filtered_fundamentals[
        pd.to_numeric(filtered_fundamentals["fundamental_score"], errors="coerce").fillna(0)
        >= min_fundamental_score
    ]

render_hero(snapshot_text, len(market_view), len(refined_view))
render_kpis(market_view, expert_view, refined_view)

overview_tab, refined_tab, potential_tab, stocks_tab, etf_tab, fundamentals_tab, coverage_tab, tags_tab = st.tabs(
    ["总览", "精选", "潜力", "股票池", "ETF", "基本面", "覆盖", "标签"]
)

with overview_tab:
    render_conclusion(refined_view, expert_view)
    left, right = st.columns([1.25, 1])
    with left:
        st.markdown('<div class="panel"><div class="panel-title"><strong>高优先级候选</strong><span class="hint">按专家分排序</span></div>', unsafe_allow_html=True)
        render_candidate_cards(filtered_refined, limit=8)
        st.markdown("</div>", unsafe_allow_html=True)
    with right:
        st.markdown('<div class="panel"><div class="panel-title"><strong>板块结构</strong><span class="hint">当前筛选范围</span></div>', unsafe_allow_html=True)
        if filtered_market.empty:
            st.info("暂无板块数据。")
        else:
            st.bar_chart(filtered_market.groupby("board").size().sort_values(ascending=False).head(12))
        st.markdown("</div>", unsafe_allow_html=True)

    a, b = st.columns(2)
    with a:
        st.markdown("## 成交额居前")
        top_amount = filtered_market.sort_values("amount", ascending=False).head(30)
        st.dataframe(display_market(top_amount), width="stretch", hide_index=True, height=430)
    with b:
        st.markdown("## 专家决策分布")
        if filtered_expert.empty:
            st.info("暂无专家评分。")
        else:
            st.bar_chart(filtered_expert.groupby("decision").size().sort_values(ascending=False))

with refined_tab:
    st.markdown("## 精选候选")
    render_candidate_cards(filtered_refined, limit=12)
    st.dataframe(display_refined(filtered_refined), width="stretch", hide_index=True, height=560)

with potential_tab:
    st.markdown("## 潜力扫描")
    st.caption("price-only v1：历史胜率含幸存者偏差，仅作相对参考；基本面/题材暂为中性占位。")
    try:
        potential_view = load_table("potential_candidates")
    except Exception:
        potential_view = pd.DataFrame()
    if potential_view.empty:
        st.info("暂无潜力扫描结果。运行 ah-screener potential-scan 后刷新。")
    else:
        filtered_potential = apply_common_filters(potential_view)
        min_potential = st.slider("潜力最低分", 0, 100, 55)
        filtered_potential = filtered_potential[
            pd.to_numeric(filtered_potential["potential_score"], errors="coerce").fillna(0) >= min_potential
        ]
        st.dataframe(
            display_potential(filtered_potential.sort_values("potential_score", ascending=False).head(200)),
            width="stretch",
            hide_index=True,
            height=620,
        )

with stocks_tab:
    st.markdown("## 股票池")
    stock_expert = filtered_expert[filtered_expert["asset_type"].eq("stock")] if not filtered_expert.empty else filtered_expert
    st.dataframe(display_expert(stock_expert.head(400)), width="stretch", hide_index=True, height=650)

with etf_tab:
    st.markdown("## ETF 工具池")
    etfs_raw = select_assets(filtered_market, ETFS) if not filtered_market.empty else filtered_market
    if etfs_raw is None or etfs_raw.empty:
        st.info("暂无 ETF 数据。运行 ah-screener sync-spot --market ETF 后刷新。")
    else:
        technicals = load_table("technical_indicators")
        full = enrich_etf_snapshot(etfs_raw, technicals=technicals)
        deduped = dedup_etf_pool(etfs_raw, technicals=technicals, top=80)
        a, b, c, d = st.columns(4)
        a.metric("ETF 数量", f"{len(full):,}")
        b.metric("双层去重后", f"{len(deduped):,}")
        c.metric("成交额过亿", f"{(pd.to_numeric(full['amount'], errors='coerce') >= 100_000_000).sum():,}")
        d.metric("分类数", f"{full['etf_category'].nunique():,}")
        view = st.radio("视图", ["双层去重精选", "完整池"], horizontal=True)
        if view == "双层去重精选":
            st.caption("同指数多家基金折叠为一只，再按相关性簇保留代表；同组数 = 被折叠的同质 ETF 数量，同类备选可追溯。")
            st.dataframe(display_etf_dedup(deduped), width="stretch", hide_index=True, height=620)
        else:
            category_options = sorted(full["etf_category"].dropna().unique())
            selected_categories = st.multiselect("ETF 分类", category_options, default=category_options)
            view_df = full[full["etf_category"].isin(selected_categories)] if selected_categories else full
            st.dataframe(
                display_etf(view_df.sort_values(["etf_score", "amount"], ascending=False).head(300)),
                width="stretch",
                hide_index=True,
                height=620,
            )

with fundamentals_tab:
    st.markdown("## 基本面")
    if filtered_fundamentals.empty:
        st.info("暂无基本面数据。")
    else:
        st.dataframe(
            display_fundamentals(filtered_fundamentals.sort_values("fundamental_score", ascending=False)),
            width="stretch",
            hide_index=True,
            height=650,
        )

with coverage_tab:
    st.markdown("## 覆盖状态")
    if coverage_view.empty:
        st.info("暂无覆盖率数据。")
    else:
        st.dataframe(display_coverage(coverage_view), width="stretch", hide_index=True, height=650)

with tags_tab:
    st.markdown("## 标签")
    try:
        tags = load_table("company_tags")
    except Exception:
        tags = pd.DataFrame()
    if tags.empty:
        st.info("暂无标签数据。")
    else:
        tag_counts = (
            tags.groupby(["tag_type", "tag_name"])
            .size()
            .rename("数量")
            .reset_index()
            .sort_values("数量", ascending=False)
            .head(120)
            .rename(columns={"tag_type": "类型", "tag_name": "标签"})
        )
        st.dataframe(tag_counts, width="stretch", hide_index=True, height=650)
