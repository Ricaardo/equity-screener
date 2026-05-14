from __future__ import annotations

import json
from html import escape

import pandas as pd
import streamlit as st

from ah_screener.config import get_settings
from ah_screener.expert_model import STRATEGY_NAME
from ah_screener.storage import Store


st.set_page_config(page_title="A/H 证券札记", layout="wide", initial_sidebar_state="expanded")


PARCHMENT_CSS = """
<style>
:root {
  --paper: #ead7a4;
  --paper-deep: #dbc184;
  --paper-light: #f7ebc4;
  --ink: #2b2118;
  --muted: #6f5738;
  --wine: #7b2d26;
  --wine-deep: #4f1d18;
  --moss: #405f3d;
  --moss-light: #d9dfbd;
  --line: #9c7a45;
  --shadow: rgba(55, 38, 20, 0.24);
}

.stApp {
  color: var(--ink);
  background-color: var(--paper);
  background-image:
    linear-gradient(90deg, rgba(72, 47, 18, 0.035) 1px, transparent 1px),
    linear-gradient(rgba(72, 47, 18, 0.028) 1px, transparent 1px);
  background-size: 34px 34px;
}

.block-container {
  padding-top: 1.6rem;
  padding-bottom: 3rem;
  max-width: 1500px;
}

section[data-testid="stSidebar"] {
  background: #d9c184;
  border-right: 2px solid var(--line);
  box-shadow: 4px 0 18px var(--shadow);
}

section[data-testid="stSidebar"] * {
  color: var(--ink);
}

h1, h2, h3 {
  color: var(--wine-deep);
  letter-spacing: 0;
  font-family: Georgia, "Times New Roman", "Noto Serif SC", serif;
}

h1 {
  font-size: 2.35rem;
  line-height: 1.08;
  margin-bottom: 0.4rem;
}

h2 {
  font-size: 1.28rem;
  border-bottom: 1px solid rgba(123, 45, 38, 0.35);
  padding-bottom: 0.35rem;
}

div[data-testid="stTabs"] button {
  background: #ddc78e;
  border: 1px solid rgba(106, 77, 38, 0.65);
  color: var(--ink);
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 0.95rem;
  margin-right: 0.35rem;
  border-radius: 0;
}

div[data-testid="stTabs"] button[aria-selected="true"] {
  background: var(--wine);
  color: #f8edc8;
  border-color: var(--wine-deep);
}

div[data-testid="stMetric"] {
  background: rgba(247, 235, 196, 0.82);
  border: 1px solid rgba(122, 91, 44, 0.55);
  border-left: 5px solid var(--wine);
  padding: 0.9rem 1rem;
  box-shadow: 0 8px 18px rgba(68, 45, 18, 0.14);
}

div[data-testid="stMetricLabel"] p {
  color: var(--muted);
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 0.88rem;
}

div[data-testid="stMetricValue"] {
  color: var(--wine-deep);
  font-family: Georgia, "Times New Roman", serif;
}

.archive-shell {
  background: rgba(246, 232, 187, 0.88);
  border: 2px solid var(--line);
  box-shadow: 0 18px 40px rgba(61, 41, 19, 0.22);
  padding: 1.15rem 1.25rem;
  margin-bottom: 1rem;
}

.archive-title {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 2px double rgba(80, 48, 20, 0.5);
  padding-bottom: 0.85rem;
}

.archive-kicker {
  color: var(--muted);
  font-size: 0.78rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-family: Georgia, "Times New Roman", serif;
}

.archive-subtitle {
  color: var(--muted);
  font-size: 0.98rem;
  margin-top: 0.25rem;
}

.seal {
  min-width: 92px;
  height: 92px;
  border: 2px solid var(--wine);
  color: var(--wine-deep);
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  font-family: Georgia, "Noto Serif SC", serif;
  font-weight: 700;
  font-size: 0.9rem;
  background: #e4c98a;
}

.section-note {
  background: #f1dfae;
  border-left: 5px solid var(--moss);
  padding: 0.75rem 0.9rem;
  color: #3a2b1b;
  margin: 0.6rem 0 1rem;
}

.candidate-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 0.85rem;
  margin: 0.8rem 0 1.1rem;
}

.candidate-card {
  background: #f6e9c1;
  border: 1px solid rgba(108, 76, 36, 0.62);
  box-shadow: 0 10px 22px rgba(72, 49, 20, 0.14);
  padding: 0.9rem;
}

.candidate-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.8rem;
  border-bottom: 1px solid rgba(108, 76, 36, 0.28);
  padding-bottom: 0.6rem;
  margin-bottom: 0.65rem;
}

.candidate-name {
  color: var(--wine-deep);
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 1.1rem;
  font-weight: 700;
}

.candidate-code {
  color: var(--muted);
  font-size: 0.78rem;
  margin-top: 0.18rem;
}

.score-badge {
  min-width: 58px;
  text-align: center;
  color: #f7ebc4;
  background: var(--wine);
  border: 1px solid var(--wine-deep);
  padding: 0.25rem 0.45rem;
  font-family: Georgia, "Times New Roman", serif;
}

.candidate-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.tag {
  border: 1px solid rgba(80, 95, 61, 0.55);
  background: var(--moss-light);
  color: #24381f;
  padding: 0.16rem 0.42rem;
  font-size: 0.78rem;
}

.warn-tag {
  border-color: rgba(123, 45, 38, 0.55);
  background: #ead2a8;
  color: var(--wine-deep);
}

.stDataFrame {
  border: 1px solid rgba(122, 91, 44, 0.55);
  box-shadow: 0 8px 18px rgba(68, 45, 18, 0.12);
}

div[data-testid="stAlert"] {
  background: #f2e0b3;
  border: 1px solid var(--line);
  color: var(--ink);
}

.stButton button, .stDownloadButton button {
  background: var(--wine);
  color: #f8edc8;
  border: 1px solid var(--wine-deep);
  border-radius: 0;
}

.stButton button:hover, .stDownloadButton button:hover {
  background: var(--wine-deep);
  color: #fff3c9;
  border-color: var(--wine-deep);
}

div[data-baseweb="select"] > div,
div[data-baseweb="slider"],
input {
  border-radius: 0;
}

hr {
  border: 0;
  border-top: 1px solid rgba(80, 48, 20, 0.45);
  margin: 1.2rem 0;
}
</style>
"""


@st.cache_data(ttl=300)
def load_scores() -> pd.DataFrame:
    store = Store(get_settings().db_path)
    return store.query_df(
        """
        SELECT *
        FROM screening_scores
        ORDER BY snapshot_date DESC, total_score DESC
        """
    )


@st.cache_data(ttl=300)
def load_tags() -> pd.DataFrame:
    store = Store(get_settings().db_path)
    return store.query_df("SELECT * FROM company_tags")


@st.cache_data(ttl=300)
def load_expert_scores() -> pd.DataFrame:
    store = Store(get_settings().db_path)
    return store.query_df(
        """
        SELECT *
        FROM expert_screening_results
        WHERE strategy = ?
        ORDER BY snapshot_date DESC, expert_score DESC
        """,
        [STRATEGY_NAME],
    )


@st.cache_data(ttl=300)
def load_refined_candidates() -> pd.DataFrame:
    store = Store(get_settings().db_path)
    return store.query_df(
        """
        SELECT *
        FROM refined_candidates
        WHERE strategy = ?
        ORDER BY snapshot_date DESC, bucket, rank_in_bucket
        """,
        [STRATEGY_NAME],
    )


@st.cache_data(ttl=300)
def load_fundamentals() -> pd.DataFrame:
    store = Store(get_settings().db_path)
    return store.query_df(
        """
        SELECT *
        FROM financial_metrics
        ORDER BY snapshot_date DESC, fundamental_score DESC
        """
    )


def render_json_list(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(parsed, list):
        return "、".join(map(str, parsed))
    return str(parsed)


def html_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return escape(str(value), quote=True)


def latest_frame(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df.empty or column not in df.columns:
        return df.copy()
    return df[df[column] == df[column].max()].copy()


def score_rank(value: object) -> str:
    if pd.isna(value):
        return "缺"
    score = float(value)
    if score >= 75:
        return "甲"
    if score >= 65:
        return "乙"
    if score >= 55:
        return "丙"
    return "丁"


def score_class(value: object) -> str:
    if pd.isna(value):
        return "warn-tag"
    return "" if float(value) >= 60 else "warn-tag"


def fmt_score(value: object) -> str:
    if pd.isna(value):
        return "--"
    return f"{float(value):.1f}"


def show_header(snapshot_text: str, coverage: int, refined_count: int) -> None:
    st.markdown(
        f"""
        <div class="archive-shell">
          <div class="archive-title">
            <div>
              <div class="archive-kicker">A/H Stock Ledger</div>
              <h1>A/H 证券札记</h1>
              <div class="archive-subtitle">
                策略：{STRATEGY_NAME} ｜ 快照：{snapshot_text} ｜ 覆盖：{coverage:,} ｜ 精选：{refined_count:,}
              </div>
            </div>
            <div class="seal">港 A<br/>候选<br/>卷宗</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def candidate_cards(df: pd.DataFrame, limit: int = 9) -> None:
    if df.empty:
        st.info("暂无提炼候选。")
        return
    cards = []
    for _, row in df.head(limit).iterrows():
        themes = render_json_list(row.get("theme_matches")).split("、")
        theme_tags = "".join(f'<span class="tag">{html_text(item)}</span>' for item in themes if item)
        fund_class = score_class(row.get("fundamental_score"))
        tech_class = score_class(row.get("technical_score"))
        cards.append(
            f"""
            <div class="candidate-card">
              <div class="candidate-top">
                <div>
                  <div class="candidate-name">{html_text(row.get("name", ""))}</div>
                  <div class="candidate-code">{html_text(row.get("market", ""))} · {html_text(row.get("symbol", ""))} · {html_text(row.get("bucket", ""))}</div>
                </div>
                <div class="score-badge">{fmt_score(row.get("expert_score"))}</div>
              </div>
              <div class="candidate-meta">
                <span class="tag">风格：{html_text(row.get("style_bucket", ""))}</span>
                <span class="tag {fund_class}">基本面：{fmt_score(row.get("fundamental_score"))}</span>
                <span class="tag {tech_class}">技术面：{fmt_score(row.get("technical_score"))}</span>
                <span class="tag">评级：{score_rank(row.get("expert_score"))}</span>
                {theme_tags}
              </div>
            </div>
            """
        )
    st.markdown(f'<div class="candidate-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def refined_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    display = df[
        [
            "bucket",
            "rank_in_bucket",
            "style_bucket",
            "market",
            "symbol",
            "name",
            "expert_score",
            "fundamental_score",
            "technical_score",
            "theme_matches",
            "selection_note",
        ]
    ].copy()
    display["theme_matches"] = display["theme_matches"].apply(render_json_list)
    display["rating"] = display["expert_score"].apply(score_rank)
    return display.rename(
        columns={
            "bucket": "主题卷",
            "rank_in_bucket": "卷内序",
            "style_bucket": "风格",
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "expert_score": "专家分",
            "fundamental_score": "基本面",
            "technical_score": "技术面",
            "theme_matches": "匹配主题",
            "selection_note": "入选说明",
            "rating": "评级",
        }
    )


def expert_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    display = df[
        [
            "market",
            "symbol",
            "name",
            "expert_score",
            "decision",
            "theme_matches",
            "fundamental_score",
            "china_master_score",
            "technical_score",
            "master_score",
            "risk_score",
            "reasons",
        ]
    ].copy()
    display["theme_matches"] = display["theme_matches"].apply(render_json_list)
    display["reasons"] = display["reasons"].apply(render_json_list)
    return display.rename(
        columns={
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "expert_score": "专家分",
            "decision": "决策",
            "theme_matches": "匹配主题",
            "fundamental_score": "基本面",
            "china_master_score": "中国大师框架",
            "technical_score": "技术面",
            "master_score": "通用大师框架",
            "risk_score": "风险分",
            "reasons": "理由",
        }
    )


st.markdown(PARCHMENT_CSS, unsafe_allow_html=True)

scores = load_scores()
expert_scores = load_expert_scores()
refined_candidates = load_refined_candidates()
fundamentals = load_fundamentals()

if scores.empty and expert_scores.empty and refined_candidates.empty:
    st.info("暂无评分数据。请先运行 ah-screener sync-spot --market all，然后运行 ah-screener score。")
    st.stop()

latest_expert = latest_frame(expert_scores, "snapshot_date")
latest_refined = latest_frame(refined_candidates, "snapshot_date")
latest_scores = latest_frame(scores, "snapshot_date")
latest_fundamentals = latest_frame(fundamentals, "snapshot_date")

snapshot_text = ""
for frame, column in (
    (latest_refined, "snapshot_date"),
    (latest_expert, "snapshot_date"),
    (latest_scores, "snapshot_date"),
):
    if not frame.empty and column in frame.columns:
        snapshot_text = str(frame[column].max()).split(" ")[0]
        break
snapshot_text = snapshot_text or "暂无"

show_header(
    snapshot_text=snapshot_text,
    coverage=len(latest_expert) if not latest_expert.empty else len(latest_scores),
    refined_count=len(latest_refined),
)

st.sidebar.markdown("## 卷宗筛选")
available_markets = sorted(
    pd.concat(
        [
            latest_refined.get("market", pd.Series(dtype=object)),
            latest_expert.get("market", pd.Series(dtype=object)),
        ],
        ignore_index=True,
    )
    .dropna()
    .unique()
)
market_filter = st.sidebar.multiselect("市场", available_markets, default=available_markets)
bucket_filter = st.sidebar.multiselect(
    "主题卷",
    sorted(latest_refined["bucket"].dropna().unique()) if not latest_refined.empty else [],
    default=None,
)
available_decisions = (
    sorted(latest_expert["decision"].dropna().unique()) if not latest_expert.empty else []
)
default_decisions = [item for item in ["core_candidate", "watchlist"] if item in available_decisions]
decision_filter = st.sidebar.multiselect(
    "专家决策",
    available_decisions,
    default=default_decisions,
)
min_expert_score = st.sidebar.slider("专家最低分", 0, 100, 55)
min_fundamental_score = st.sidebar.slider("基本面最低分", 0, 100, 0)

filtered_refined = latest_refined.copy()
if market_filter and "market" in filtered_refined.columns:
    filtered_refined = filtered_refined[filtered_refined["market"].isin(market_filter)]
if bucket_filter:
    filtered_refined = filtered_refined[filtered_refined["bucket"].isin(bucket_filter)]
if not filtered_refined.empty:
    filtered_refined = filtered_refined[
        (pd.to_numeric(filtered_refined["expert_score"], errors="coerce") >= min_expert_score)
        & (pd.to_numeric(filtered_refined["fundamental_score"], errors="coerce").fillna(0) >= min_fundamental_score)
    ]

filtered_expert = latest_expert.copy()
if market_filter and "market" in filtered_expert.columns:
    filtered_expert = filtered_expert[filtered_expert["market"].isin(market_filter)]
if decision_filter and "decision" in filtered_expert.columns:
    filtered_expert = filtered_expert[filtered_expert["decision"].isin(decision_filter)]
if not filtered_expert.empty:
    filtered_expert = filtered_expert[
        (pd.to_numeric(filtered_expert["expert_score"], errors="coerce") >= min_expert_score)
        & (pd.to_numeric(filtered_expert["fundamental_score"], errors="coerce").fillna(0) >= min_fundamental_score)
    ]

metric_left, metric_mid, metric_right, metric_more = st.columns(4)
metric_left.metric("提炼候选", f"{len(filtered_refined):,}")
metric_mid.metric("专家候选", f"{len(filtered_expert):,}")
metric_right.metric(
    "核心候选",
    f"{(latest_expert['decision'] == 'core_candidate').sum():,}" if not latest_expert.empty else "0",
)
metric_more.metric("基本面覆盖", f"{len(latest_fundamentals):,}")

st.markdown(
    '<div class="section-note">当前页按主题卷和风格卷做了同类去重。专家分偏向综合研究优先级，不等同于买入信号。</div>',
    unsafe_allow_html=True,
)

refined_tab, expert_tab, fundamental_tab, basic_tab, tag_tab = st.tabs(
    ["精选卷宗", "专家榜", "基本面簿", "基础筛选", "标签索引"]
)

with refined_tab:
    st.markdown("## 精选卷宗")
    candidate_cards(filtered_refined.sort_values("expert_score", ascending=False), limit=9)
    if filtered_refined.empty:
        st.info("当前筛选条件下没有提炼候选。")
    else:
        st.dataframe(
            refined_display_frame(filtered_refined),
            width="stretch",
            hide_index=True,
            height=520,
        )

        bucket_chart = (
            filtered_refined.groupby("bucket")["expert_score"]
            .mean()
            .sort_values(ascending=False)
            .rename("平均专家分")
        )
        st.bar_chart(bucket_chart)

with expert_tab:
    st.markdown("## 专家榜")
    if filtered_expert.empty:
        st.info("当前筛选条件下没有专家榜数据。")
    else:
        st.dataframe(
            expert_display_frame(filtered_expert.head(250)),
            width="stretch",
            hide_index=True,
            height=620,
        )
        left_chart, right_chart = st.columns(2)
        with left_chart:
            st.bar_chart(latest_expert.groupby("decision").size().rename("数量"))
        with right_chart:
            st.bar_chart(latest_expert.groupby("market")["expert_score"].mean().rename("平均专家分"))

with fundamental_tab:
    st.markdown("## 基本面簿")
    if latest_fundamentals.empty:
        st.info("暂无基本面数据。")
    else:
        fund_filtered = latest_fundamentals.copy()
        if market_filter and "market" in fund_filtered.columns:
            fund_filtered = fund_filtered[fund_filtered["market"].isin(market_filter)]
        fund_filtered = fund_filtered[
            pd.to_numeric(fund_filtered["fundamental_score"], errors="coerce").fillna(0)
            >= min_fundamental_score
        ]
        fund_display = fund_filtered[
            [
                "market",
                "symbol",
                "name",
                "report_date",
                "fundamental_score",
                "roe",
                "revenue_yoy",
                "net_profit_yoy",
                "debt_asset_ratio",
                "cashflow_to_profit",
                "warnings",
            ]
        ].copy()
        fund_display["warnings"] = fund_display["warnings"].apply(render_json_list)
        fund_display = fund_display.rename(
            columns={
                "market": "市场",
                "symbol": "代码",
                "name": "名称",
                "report_date": "报告期",
                "fundamental_score": "基本面分",
                "roe": "ROE",
                "revenue_yoy": "收入同比",
                "net_profit_yoy": "利润同比",
                "debt_asset_ratio": "资产负债率",
                "cashflow_to_profit": "现金流/利润",
                "warnings": "预警",
            }
        )
        st.dataframe(fund_display, width="stretch", hide_index=True, height=620)

with basic_tab:
    st.markdown("## 基础筛选")
    if latest_scores.empty:
        st.info("暂无基础评分数据。")
    else:
        basic_filtered = latest_scores.copy()
        if market_filter and "market" in basic_filtered.columns:
            basic_filtered = basic_filtered[basic_filtered["market"].isin(market_filter)]
        basic_filtered = basic_filtered[pd.to_numeric(basic_filtered["total_score"], errors="coerce") >= 50]
        basic_display = basic_filtered[
            [
                "market",
                "symbol",
                "name",
                "total_score",
                "decision",
                "valuation_score",
                "liquidity_score",
                "theme_score",
                "risk_score",
                "reasons",
            ]
        ].copy()
        basic_display["reasons"] = basic_display["reasons"].apply(render_json_list)
        basic_display = basic_display.rename(
            columns={
                "market": "市场",
                "symbol": "代码",
                "name": "名称",
                "total_score": "总分",
                "decision": "决策",
                "valuation_score": "估值",
                "liquidity_score": "流动性",
                "theme_score": "主题",
                "risk_score": "风险",
                "reasons": "理由",
            }
        )
        st.dataframe(basic_display, width="stretch", hide_index=True, height=620)

with tag_tab:
    st.markdown("## 标签索引")
    tags = load_tags()
    if tags.empty:
        st.info("暂无标签数据。")
    else:
        tag_counts = (
            tags.groupby(["tag_type", "tag_name"])
            .size()
            .rename("count")
            .reset_index()
            .sort_values("count", ascending=False)
            .head(80)
            .rename(columns={"tag_type": "标签类型", "tag_name": "标签", "count": "数量"})
        )
        st.dataframe(tag_counts, width="stretch", hide_index=True, height=620)
