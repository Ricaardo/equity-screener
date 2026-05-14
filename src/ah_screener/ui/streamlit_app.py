from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from ah_screener.config import get_settings
from ah_screener.expert_model import STRATEGY_NAME
from ah_screener.storage import Store


st.set_page_config(page_title="A/H Stock Screener", layout="wide")


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
        ORDER BY snapshot_date DESC, bucket, rank_in_bucket
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
        return ", ".join(map(str, parsed))
    return str(parsed)


st.title("A 股 + 港股筛选")

scores = load_scores()
expert_scores = load_expert_scores()
refined_candidates = load_refined_candidates()
if scores.empty and expert_scores.empty and refined_candidates.empty:
    st.info("暂无评分数据。请先运行 ah-screener sync-spot --market all，然后运行 ah-screener score。")
    st.stop()

refined_tab, expert_tab, basic_tab, tag_tab = st.tabs(["提炼候选", "专家筛选", "基础评分", "标签"])

with refined_tab:
    if refined_candidates.empty:
        st.info("暂无提炼候选。请运行 ah-screener expert-score。")
    else:
        latest_refined_date = refined_candidates["snapshot_date"].max()
        latest_refined = refined_candidates[refined_candidates["snapshot_date"] == latest_refined_date].copy()
        left, middle, right = st.columns(3)
        left.metric("快照日期", str(latest_refined_date))
        middle.metric("主题桶", f"{latest_refined['bucket'].nunique():,}")
        right.metric("提炼候选", f"{len(latest_refined):,}")

        refined_bucket_filter = st.sidebar.multiselect(
            "提炼主题",
            sorted(latest_refined["bucket"].dropna().unique()),
            default=None,
        )
        refined_market_filter = st.sidebar.multiselect(
            "提炼市场",
            sorted(latest_refined["market"].dropna().unique()),
            default=None,
        )

        refined_filtered = latest_refined
        if refined_bucket_filter:
            refined_filtered = refined_filtered[refined_filtered["bucket"].isin(refined_bucket_filter)]
        if refined_market_filter:
            refined_filtered = refined_filtered[refined_filtered["market"].isin(refined_market_filter)]

        refined_display = refined_filtered[
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
                "reasons",
            ]
        ].copy()
        refined_display["theme_matches"] = refined_display["theme_matches"].apply(render_json_list)
        refined_display["reasons"] = refined_display["reasons"].apply(render_json_list)
        st.dataframe(refined_display, use_container_width=True, hide_index=True)

with expert_tab:
    if expert_scores.empty:
        st.info("暂无专家筛选数据。请运行 ah-screener technical，然后运行 ah-screener expert-score。")
    else:
        latest_expert_date = expert_scores["snapshot_date"].max()
        latest_expert = expert_scores[expert_scores["snapshot_date"] == latest_expert_date].copy()
        left, middle, right = st.columns(3)
        left.metric("快照日期", str(latest_expert_date))
        middle.metric("覆盖股票", f"{len(latest_expert):,}")
        right.metric("核心候选", f"{(latest_expert['decision'] == 'core_candidate').sum():,}")

        market_filter = st.sidebar.multiselect(
            "专家市场",
            sorted(latest_expert["market"].dropna().unique()),
            default=None,
        )
        decision_filter = st.sidebar.multiselect(
            "专家决策",
            sorted(latest_expert["decision"].dropna().unique()),
            default=["core_candidate", "watchlist"],
        )
        min_expert_score = st.sidebar.slider("专家最低分", 0, 100, 55)

        expert_filtered = latest_expert[latest_expert["expert_score"] >= min_expert_score]
        if market_filter:
            expert_filtered = expert_filtered[expert_filtered["market"].isin(market_filter)]
        if decision_filter:
            expert_filtered = expert_filtered[expert_filtered["decision"].isin(decision_filter)]

        expert_display = expert_filtered[
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
        expert_display["theme_matches"] = expert_display["theme_matches"].apply(render_json_list)
        expert_display["reasons"] = expert_display["reasons"].apply(render_json_list)
        st.dataframe(expert_display, use_container_width=True, hide_index=True)

        left_chart, right_chart = st.columns(2)
        with left_chart:
            st.bar_chart(latest_expert.groupby("decision").size())
        with right_chart:
            st.bar_chart(latest_expert.groupby("market")["expert_score"].mean())

with basic_tab:
    if scores.empty:
        st.info("暂无基础评分数据。")
    else:
        latest_date = scores["snapshot_date"].max()
        latest = scores[scores["snapshot_date"] == latest_date].copy()

        left, middle, right = st.columns(3)
        left.metric("快照日期", str(latest_date))
        middle.metric("覆盖股票", f"{len(latest):,}")
        right.metric("保留候选", f"{(latest['decision'] == 'keep').sum():,}")

        market = st.sidebar.multiselect("基础市场", sorted(latest["market"].dropna().unique()), default=None)
        decision = st.sidebar.multiselect(
            "基础决策", sorted(latest["decision"].dropna().unique()), default=["keep", "watch"]
        )
        min_score = st.sidebar.slider("基础最低分", 0, 100, 50)

        filtered = latest[latest["total_score"] >= min_score]
        if market:
            filtered = filtered[filtered["market"].isin(market)]
        if decision:
            filtered = filtered[filtered["decision"].isin(decision)]

        st.subheader("候选池")
        columns = [
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

        display = filtered[columns].copy()
        display["reasons"] = display["reasons"].apply(render_json_list)
        st.dataframe(display, use_container_width=True, hide_index=True)

        st.subheader("评分分布")
        hist_left, hist_right = st.columns(2)
        with hist_left:
            st.bar_chart(latest.groupby("decision").size())
        with hist_right:
            st.bar_chart(latest.groupby("market")["total_score"].mean())

with tag_tab:
    tags = load_tags()
    if not tags.empty:
        st.subheader("热门标签")
        tag_counts = (
            tags.groupby(["tag_type", "tag_name"])
            .size()
            .rename("count")
            .reset_index()
            .sort_values("count", ascending=False)
            .head(50)
        )
        st.dataframe(tag_counts, use_container_width=True, hide_index=True)
    else:
        st.info("暂无标签数据。")
