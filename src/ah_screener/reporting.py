from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ah_screener.aggregations import candidate_diff
from ah_screener.config import get_settings
from ah_screener.selection import dedup_etf_pool, etf_category_overview
from ah_screener.universe import ETFS, select_assets
from ah_screener.expert_model import STRATEGY_NAME
from ah_screener.storage import Store


EXTERNAL_CONTEXT = [
    {
        "name": "国务院“人工智能+”行动意见",
        "url": "https://www.cac.gov.cn/2025-08/27/c_1758018277755538.htm",
        "note": "政策层面强调 AI 与产业、科技、消费、治理等重点领域融合。",
    },
    {
        "name": "国家发展改革委、国家能源局“人工智能+能源”实施意见",
        "url": "https://www.nda.gov.cn/sjj/zwgk/zcfb/0908/20250908201317566927066_pc.html",
        "note": "算力与电力协同、智能电网、储能和新能源是政策明确支持方向。",
    },
    {
        "name": "中国证券报：港股结构性机会",
        "url": "https://www.cs.com.cn/gppd/ggzx/2026/05/06/detail_2026050610009410.html",
        "note": "港股更适合聚焦科技 AI、高股息和创新医药等结构性机会。",
    },
    {
        "name": "财联社转载中信证券科技策略",
        "url": "https://www.cls.cn/detail/2365601",
        "note": "科技硬件端景气度领先，关注 AI 算力、光通信、半导体设备和上游涨价链。",
    },
]


REPORT_SCHEMA_VERSION = "1.1"
DISCLAIMER = "本报告仅用于研究和候选筛选，不构成投资建议或买卖指令。"
# A-share ETF categories that trade T+0 (cross-border / bond / commodity / money);
# onshore equity ETFs (宽基/行业/主题) and all A-share stocks are T+1.
T0_ETF_CATEGORIES = frozenset({"跨境ETF", "债券ETF", "商品ETF", "货币ETF"})


def _trading_system(
    market: object, asset_type: object = "stock", etf_category: object = None
) -> str:
    """Trading settlement regime shown at a glance: HK/US intraday round-trip = T+0;
    A-share stocks = T+1; A-share ETFs depend on category."""
    m = str(market or "").upper()
    if m in {"US", "HK"}:
        return "T+0"
    if m == "A":
        if str(asset_type).lower() == "etf":
            return "T+0" if str(etf_category) in T0_ETF_CATEGORIES else "T+1"
        return "T+1"
    return "T+1"


CONCLUSION_LINES = [
    "当前模型倾向采用“科技成长进攻 + 红利资源防御 + 医药质量观察”的结构，而不是押注单一主题。",
    "AI 算力、半导体、港股 AI 互联网、创新药、高股息资源和电力储能仍是本轮筛选中最值得持续跟踪的方向。",
]


def _json_list(value: object) -> str:
    return "、".join(_parse_json_list(value))


def _clean(value: object) -> object:
    """Coerce a single cell into a JSON-serializable scalar (NaN/NaT -> None)."""
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if (math.isnan(number) or math.isinf(number)) else number
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return None if pd.isna(value) else value.strftime("%Y-%m-%d")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value) if not isinstance(value, (int, str)) else value


def _parse_json_list(value: object) -> list[str]:
    """Parse a JSON-string list column (theme_matches / reasons) into a Python list."""
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if item is not None and str(item) != ""]
    return [str(parsed)]


def _records(
    df: pd.DataFrame, fields: list[str], list_fields: tuple[str, ...] = ()
) -> list[dict[str, object]]:
    """Project a frame into JSON-safe records, keeping only present fields.

    ``list_fields`` are JSON-string columns (e.g. theme_matches / reasons) that get
    parsed back into real lists so an AI consumer reads the evidence chain directly.
    """
    if df is None or df.empty:
        return []
    present = [field for field in fields if field in df.columns]
    rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        record: dict[str, object] = {}
        for field in present:
            if field in list_fields:
                record[field] = _parse_json_list(row.get(field))
            else:
                record[field] = _clean(row.get(field))
        rows.append(record)
    return rows


def _fmt(value: object, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    return str(value)


def _fmt_amount(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    number = float(value)
    if abs(number) >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿"
    if abs(number) >= 10_000:
        return f"{number / 10_000:.1f}万"
    return f"{number:.0f}"


def _table(df: pd.DataFrame, columns: list[str]) -> str:
    if df.empty:
        return "暂无数据。\n"
    out = df[columns].copy()
    for column in out.columns:
        if out[column].dtype.kind in {"f", "i"}:
            out[column] = out[column].map(lambda value: _fmt(value))
    return out.to_markdown(index=False)


def market_date_health(
    snapshots: pd.DataFrame, max_spread_days: int = 3
) -> tuple[pd.DataFrame, str]:
    """Per-market latest snapshot date + a warning when markets diverge.

    expert/report use the global-latest snapshot date, so if markets sit on
    different dates (partial syncs) the freshest market dominates the candidate
    list. Surface this so a stale-market artifact isn't silent.
    """
    if snapshots is None or snapshots.empty or "market" not in snapshots.columns:
        return pd.DataFrame(columns=["市场", "最新日期"]), ""
    dates = (
        snapshots.assign(_d=pd.to_datetime(snapshots["trade_date"], errors="coerce"))
        .groupby("market")["_d"]
        .max()
        .sort_values()
    )
    table = dates.reset_index().rename(columns={"market": "市场", "_d": "最新日期"})
    table["最新日期"] = table["最新日期"].dt.strftime("%Y-%m-%d")
    spread = (dates.max() - dates.min()).days if dates.notna().all() and len(dates) > 1 else 0
    warning = ""
    if spread > max_spread_days:
        warning = (
            f"⚠ 各市场快照日期相差 {spread} 天（{dates.idxmin()} 最旧 / {dates.idxmax()} 最新）。"
            "专家与候选取全局最新日期，结果会偏向最新市场——建议同日重跑 update-all。"
        )
    return table, warning


def _load_report_data(store: Store) -> dict[str, pd.DataFrame]:
    store.init_db()
    refined = store.query_df(
        """
        SELECT *
        FROM refined_candidates
        WHERE strategy = ?
        ORDER BY snapshot_date DESC, bucket, rank_in_bucket
        """,
        [STRATEGY_NAME],
    )
    expert = store.query_df(
        """
        SELECT *
        FROM expert_screening_results
        WHERE strategy = ?
        ORDER BY snapshot_date DESC, expert_score DESC
        """,
        [STRATEGY_NAME],
    )
    fundamentals = store.query_df("SELECT * FROM financial_metrics")
    snapshots = store.query_df("SELECT * FROM market_snapshots")
    technicals = store.query_df("SELECT * FROM technical_indicators")
    securities = store.query_df("SELECT * FROM securities")
    potential = store.query_df(
        """
        SELECT *
        FROM potential_candidates
        ORDER BY snapshot_date DESC, potential_score DESC
        LIMIT 30
        """
    )
    lifecycle = store.query_df("SELECT * FROM security_lifecycle_events")
    return {
        "refined": refined,
        "expert": expert,
        "fundamentals": fundamentals,
        "snapshots": snapshots,
        "technicals": technicals,
        "securities": securities,
        "potential": potential,
        "lifecycle": lifecycle,
    }


def _latest(df: pd.DataFrame, date_column: str) -> pd.DataFrame:
    if df.empty or date_column not in df.columns:
        return df
    latest_date = df[date_column].max()
    return df[df[date_column] == latest_date].copy()


def _latest_by_security(df: pd.DataFrame, date_column: str) -> pd.DataFrame:
    if df.empty or date_column not in df.columns:
        return df
    frame = df.copy()
    frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
    return frame.sort_values(date_column).drop_duplicates(["market", "symbol"], keep="last")


def _bucket_recommendations(refined: pd.DataFrame) -> list[str]:
    if refined.empty:
        return ["当前没有提炼候选，先检查行情、技术指标和财报同步是否完整。"]

    bucket_scores = (
        refined.groupby("bucket")
        .agg(
            count=("symbol", "count"),
            expert_avg=("expert_score", "mean"),
            fundamental_avg=("fundamental_score", "mean"),
            technical_avg=("technical_score", "mean"),
        )
        .reset_index()
        .sort_values(["expert_avg", "fundamental_avg"], ascending=False)
    )

    lines: list[str] = []
    for _, row in bucket_scores.iterrows():
        bucket = str(row["bucket"])
        expert = float(row["expert_avg"])
        fundamental = float(row["fundamental_avg"])
        technical = float(row["technical_avg"])
        if expert >= 68 and fundamental >= 65:
            stance = "优先研究"
        elif expert >= 62 and (fundamental >= 60 or technical >= 75):
            stance = "观察跟踪"
        else:
            stance = "谨慎跟踪"
        lines.append(
            f"- {bucket}：{stance}。平均专家分 {expert:.1f}，基本面 {fundamental:.1f}，技术面 {technical:.1f}。"
        )
    return lines


def _portfolio_notes(refined: pd.DataFrame) -> list[str]:
    if refined.empty:
        return []
    ai_like = refined[refined["bucket"].isin(["AI算力硬件", "半导体国产替代", "港股AI互联网平台"])]
    defensive = refined[refined["bucket"].isin(["高股息央国企防御", "高股息资源防御"])]
    healthcare = refined[refined["bucket"] == "创新药与医疗科技"]
    resource = refined[refined["bucket"] == "资源涨价与安全资产"]

    notes = [
        "组合上不建议把所有候选集中在单一景气方向，科技成长、红利防御、资源周期和医药成长需要分桶跟踪。",
        "买入前应逐只复核最新公告、业绩会、估值分位、股东结构、减持和再融资风险。",
    ]
    if len(ai_like) >= 6:
        notes.append(
            "AI/半导体相关候选较多，容易出现同涨同跌和估值拥挤，适合用分批和回撤条件控制节奏。"
        )
    if len(defensive) >= 4:
        notes.append("红利和能源资产提供防御属性，但仍需关注油煤价格、利率和分红可持续性。")
    if not healthcare.empty:
        notes.append("创新药候选基本面分较高，但港股医药波动大，需要关注临床、BD、集采和现金消耗。")
    if not resource.empty:
        notes.append(
            "资源品候选受价格周期影响明显，不能只看历史 ROE，要同步跟踪商品价格和资本开支。"
        )
    return notes


def _coverage_by_board(
    snapshots: pd.DataFrame,
    technicals: pd.DataFrame,
    fundamentals: pd.DataFrame,
    expert: pd.DataFrame,
) -> pd.DataFrame:
    if snapshots.empty:
        return pd.DataFrame()
    df = snapshots.drop_duplicates(["market", "symbol"], keep="last").copy()
    if "asset_type" not in df.columns:
        df["asset_type"] = "stock"
    if "board" not in df.columns:
        df["board"] = "未分类"
    df["asset_type"] = df["asset_type"].fillna("stock")
    df["board"] = df["board"].fillna("未分类")

    for source_name, source_df in [
        ("technical", technicals),
        ("fundamental", fundamentals),
        ("expert", expert),
    ]:
        flag = f"has_{source_name}"
        keys = (
            source_df[["market", "symbol"]].drop_duplicates().assign(**{flag: True})
            if not source_df.empty
            else pd.DataFrame(columns=["market", "symbol", flag])
        )
        df = df.merge(keys, on=["market", "symbol"], how="left")
        df[flag] = df[flag].eq(True)

    coverage = (
        df.groupby(["market", "asset_type", "board"], dropna=False)
        .agg(
            universe=("symbol", "count"),
            technical=("has_technical", "sum"),
            fundamental=("has_fundamental", "sum"),
            expert=("has_expert", "sum"),
        )
        .reset_index()
    )
    for column in ["technical", "fundamental", "expert"]:
        coverage[f"{column}_pct"] = (
            coverage[column] / coverage["universe"].replace(0, pd.NA) * 100
        ).fillna(0)
    return coverage.rename(
        columns={
            "market": "市场",
            "asset_type": "类型",
            "board": "板块",
            "universe": "证券数",
            "technical": "技术覆盖",
            "technical_pct": "技术覆盖率",
            "fundamental": "基本面覆盖",
            "fundamental_pct": "基本面覆盖率",
            "expert": "专家覆盖",
            "expert_pct": "专家覆盖率",
        }
    ).sort_values(["市场", "类型", "证券数"], ascending=[True, True, False])


def _candidate_changes(refined: pd.DataFrame) -> pd.DataFrame:
    """Markdown/Chinese view of the canonical cross-snapshot diff (single source)."""
    diff = candidate_diff(refined)
    if diff.empty:
        return pd.DataFrame()
    status_cn = {"new": "新增", "removed": "移出", "kept": "保留"}
    out = diff.copy()
    out["status"] = out["status"].map(status_cn)
    return out.rename(
        columns={
            "status": "变化",
            "bucket": "主题桶",
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "latest_score": "最新分",
            "previous_score": "上期分",
            "score_delta": "分数变化",
        }
    )[["变化", "主题桶", "市场", "代码", "名称", "最新分", "上期分", "分数变化"]]


def build_report_payload(store: Store | None = None) -> dict:
    """Return the machine-readable report payload (the AI product) — no file IO.

    Programmatic entry point for AI consumers / tests. ``generate_report`` runs the
    same path and additionally writes the Markdown, JSON and ``latest`` pointers.
    The JSON contract is documented in ``docs/report-schema.md``.
    """
    store = store or Store(get_settings().db_path)
    _, payload = _report_artifacts(store, datetime.now())
    validate_report_payload(payload)
    return payload


def _report_artifacts(store: Store, generated_at: datetime) -> tuple[str, dict]:
    """Build ``(markdown_text, json_payload)`` from the database; pure, no file IO.

    The Markdown and the JSON product are rendered from the same prepared frames, so
    the two views cannot drift apart.
    """
    data = _load_report_data(store)

    refined_all = data["refined"]
    refined = _latest(data["refined"], "snapshot_date")
    expert = _latest(data["expert"], "snapshot_date")
    fundamentals = _latest(data["fundamentals"], "snapshot_date")
    for column in [
        "revenue_cagr_3y",
        "net_profit_cagr_3y",
        "roe_avg_3y",
        "roe_stability_score",
        "margin_stability_score",
        "fundamental_trend_score",
        "rd_expense_ratio",
        "capex_to_revenue",
        "capex_to_operating_cashflow",
        "innovation_efficiency_score",
    ]:
        if column not in fundamentals.columns:
            fundamentals[column] = pd.NA
    snapshots = _latest_by_security(data["snapshots"], "trade_date")
    technicals = _latest(data["technicals"], "snapshot_date")
    securities = data["securities"]
    potential = _latest(data["potential"], "snapshot_date")
    lifecycle = data["lifecycle"]

    report_date = generated_at.strftime("%Y-%m-%d")
    markdown_relpath = f"ah-screening-report-{report_date}.md"

    refined_display = refined.copy()
    if not refined_display.empty:
        for column, default in [
            ("peer_score", pd.NA),
            ("industry_fit_score", pd.NA),
            ("valuation_percentile", pd.NA),
            ("detailed_industry", ""),
            ("industry_peer_group", ""),
        ]:
            if column not in refined_display.columns:
                refined_display[column] = default
        refined_display["theme_matches_text"] = refined_display["theme_matches"].map(_json_list)
        refined_display = refined_display.rename(
            columns={
                "bucket": "主题桶",
                "rank_in_bucket": "桶内排名",
                "style_bucket": "风格",
                "market": "市场",
                "symbol": "代码",
                "name": "名称",
                "expert_score": "专家分",
                "fundamental_score": "基本面",
                "technical_score": "技术面",
                "detailed_industry": "细分行业",
                "peer_score": "同类分位",
                "industry_fit_score": "行业适配",
                "valuation_percentile": "估值分位",
                "industry_peer_group": "同类组",
                "theme_matches_text": "匹配主题",
            }
        )

    core = expert[expert["decision"] == "core_candidate"].head(20).copy()
    if not core.empty:
        for column, default in [
            ("peer_score", pd.NA),
            ("industry_fit_score", pd.NA),
            ("valuation_percentile", pd.NA),
            ("detailed_industry", ""),
            ("industry_peer_group", ""),
        ]:
            if column not in core.columns:
                core[column] = default
        core["theme_matches_text"] = core["theme_matches"].map(_json_list)
        core = core.rename(
            columns={
                "market": "市场",
                "symbol": "代码",
                "name": "名称",
                "expert_score": "专家分",
                "fundamental_score": "基本面",
                "china_master_score": "中国大师框架",
                "technical_score": "技术面",
                "detailed_industry": "细分行业",
                "peer_score": "同类分位",
                "industry_fit_score": "行业适配",
                "valuation_percentile": "估值分位",
                "industry_peer_group": "同类组",
                "theme_matches_text": "匹配主题",
            }
        )
    fundamental_display = (
        fundamentals.sort_values(["fundamental_score", "fundamental_trend_score"], ascending=False)
        .head(20)
        .copy()
        if not fundamentals.empty
        else pd.DataFrame()
    )
    if not fundamental_display.empty:
        fundamental_display = fundamental_display.rename(
            columns={
                "market": "市场",
                "symbol": "代码",
                "name": "名称",
                "fundamental_score": "基本面",
                "revenue_cagr_3y": "收入CAGR",
                "net_profit_cagr_3y": "利润CAGR",
                "roe_avg_3y": "ROE均值",
                "roe_stability_score": "ROE稳定",
                "fundamental_trend_score": "多期趋势",
                "rd_expense_ratio": "研发费用率",
                "capex_to_revenue": "资本开支/收入",
                "capex_to_operating_cashflow": "资本开支/经营现金流",
                "innovation_efficiency_score": "研发资本效率",
                "cashflow_to_profit": "现金流/利润",
                "debt_asset_ratio": "资产负债率",
            }
        )

    coverage = {
        "证券快照": len(snapshots),
        "技术指标": len(technicals),
        "标准化基本面": len(fundamentals),
        "专家评分": len(expert),
        "提炼候选": len(refined),
        "退市生命周期": len(lifecycle),
    }
    snapshot_sources = pd.DataFrame()
    if not refined_all.empty and "snapshot_source" in refined_all.columns:
        source_frame = refined_all.copy()
        source_frame["snapshot_source"] = source_frame["snapshot_source"].fillna("natural")
        if "is_replay" not in source_frame.columns:
            source_frame["is_replay"] = False
        snapshot_sources = (
            source_frame.groupby(["snapshot_source", "is_replay"], dropna=False)
            .agg(
                行数=("symbol", "count"),
                快照数=("snapshot_date", "nunique"),
                最早日期=("snapshot_date", "min"),
                最新日期=("snapshot_date", "max"),
            )
            .reset_index()
            .rename(columns={"snapshot_source": "来源", "is_replay": "回放"})
        )
    if lifecycle.empty:
        lifecycle_note = "暂无退市/摘牌生命周期记录；历史验证仍保留幸存者偏差。"
    else:
        lifecycle_counts = (
            lifecycle.groupby("market", dropna=False)["symbol"].nunique().sort_index().to_dict()
        )
        lifecycle_parts = ", ".join(
            f"{market} {int(count):,}" for market, count in lifecycle_counts.items()
        )
        lifecycle_note = (
            "当前 active universe 已按日期留痕；退市/摘牌生命周期已入库"
            f"（{lifecycle_parts}）。US 历史摘牌依赖 `AH_SCREENER_ALPHA_VANTAGE_KEY`；"
            "自然快照样本积累前，早期历史验证仍保留幸存者偏差。"
        )
    coverage_board = _coverage_by_board(snapshots, technicals, fundamentals, expert)
    board_counts = (
        securities.groupby(["market", "asset_type", "board"]).size().rename("数量").reset_index()
        if not securities.empty and {"market", "asset_type", "board"}.issubset(securities.columns)
        else pd.DataFrame(columns=["market", "asset_type", "board", "数量"])
    )
    if not board_counts.empty:
        board_counts = board_counts.rename(
            columns={"market": "市场", "asset_type": "类型", "board": "板块"}
        ).sort_values("数量", ascending=False)
    decision_counts = (
        expert.groupby("decision")
        .size()
        .rename("数量")
        .reset_index()
        .sort_values("数量", ascending=False)
        if not expert.empty
        else pd.DataFrame(columns=["decision", "数量"])
    )
    if not decision_counts.empty:
        decision_counts = decision_counts.rename(columns={"decision": "决策"})
    etf_pool = select_assets(snapshots, ETFS).copy() if not snapshots.empty else pd.DataFrame()
    # Two-table layout (decision D1): ① full-pool size by category; ② double-layer
    # de-duplicated leaders. Both go through the selection seam (R14).
    etf_category_counts = etf_category_overview(etf_pool)
    etf_deduped = dedup_etf_pool(etf_pool, technicals=technicals, top=20)
    etf_display = etf_deduped.copy()
    if not etf_display.empty:
        etf_display["涨跌幅"] = pd.to_numeric(etf_display.get("pct_change"), errors="coerce").map(
            lambda value: f"{float(value):.2f}%" if pd.notna(value) else ""
        )
        etf_display["成交额"] = etf_display.get("amount").map(_fmt_amount)
        etf_display["同组数"] = (
            pd.to_numeric(etf_display.get("peer_count"), errors="coerce").fillna(1).astype(int)
        )
        etf_display = etf_display.rename(
            columns={
                "symbol": "代码",
                "name": "名称",
                "etf_cluster": "簇",
                "etf_track": "跟踪",
                "etf_score": "ETF分",
                "etf_recommendation": "建议",
                "peer_alternatives": "同类备选",
            }
        )
        etf_display["ETF分"] = pd.to_numeric(etf_display["ETF分"], errors="coerce").map(
            lambda value: f"{float(value):.1f}" if pd.notna(value) else ""
        )
    potential_display = potential.copy()
    if not potential_display.empty:
        for column in [
            "potential_score",
            "technical_setup_score",
            "relative_strength_score",
            "pivot_price",
            "target_price",
            "stop_price",
            "rr_ratio",
            "hist_win_rate",
        ]:
            potential_display[column] = pd.to_numeric(
                potential_display[column], errors="coerce"
            ).map(lambda value: f"{float(value):.1f}" if pd.notna(value) else "")
        potential_display = potential_display.rename(
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
            }
        )
    change_display = _candidate_changes(refined_all)

    bias_notes = [
        "回测默认只使用定时/手动自然生成的候选快照；历史回放快照必须显式传 `--include-replay`，只作诊断，不作 edge 证明。",
        "`potential-sweep` 是同一历史样本内的阈值扫描；RS 阈值证据必须以 `potential-walk-forward` 的样本外结果为准。",
        lifecycle_note,
    ]

    lines = [
        "# A/H/US 股票筛选研究报告",
        "",
        f"- 生成时间：{generated_at:%Y-%m-%d %H:%M:%S}",
        f"- 数据库：`{store.db_path}`",
        f"- 策略：`{STRATEGY_NAME}`",
        f"- 声明：{DISCLAIMER}",
        "",
        "## 1. 当前结论",
        "",
        *CONCLUSION_LINES,
        "",
        "## 2. 外部背景（不计入评分）",
        "",
        "本节只作为阅读报告时的宏观和产业上下文，不参与专家分、ETF 分、潜力分或回测。",
        "",
    ]
    for item in EXTERNAL_CONTEXT:
        lines.append(f"- [{item['name']}]({item['url']})：{item['note']}")

    lines.extend(
        [
            "",
            "## 3. 数据覆盖",
            "",
            "| 项目 | 数量 |",
            "| --- | ---: |",
        ]
    )
    for key, value in coverage.items():
        lines.append(f"| {key} | {value:,} |")

    date_table, date_warning = market_date_health(snapshots)
    if not date_table.empty:
        lines.extend(["", "### 3.1 数据新鲜度（各市场最新快照日）", ""])
        if date_warning:
            lines.append(f"> {date_warning}")
            lines.append("")
        lines.append(_table(date_table, ["市场", "最新日期"]))

    lines.extend(
        [
            "",
            "### 3.2 证据口径与偏差控制",
            "",
            *[f"- {note}" for note in bias_notes],
            "",
            _table(snapshot_sources, ["来源", "回放", "行数", "快照数", "最早日期", "最新日期"])
            if not snapshot_sources.empty
            else "暂无提炼快照来源明细。",
        ]
    )

    lines.extend(
        [
            "",
            "## 4. 专家评分分布",
            "",
            _table(decision_counts, ["决策", "数量"])
            if not decision_counts.empty
            else "暂无数据。",
            "",
            "## 5. 市场与板块覆盖",
            "",
            _table(board_counts, ["市场", "类型", "板块", "数量"])
            if not board_counts.empty
            else "暂无数据。",
            "",
            "### 5.1 覆盖率明细",
            "",
            _table(
                coverage_board,
                [
                    "市场",
                    "类型",
                    "板块",
                    "证券数",
                    "技术覆盖",
                    "技术覆盖率",
                    "基本面覆盖",
                    "基本面覆盖率",
                    "专家覆盖",
                    "专家覆盖率",
                ],
            )
            if not coverage_board.empty
            else "暂无数据。",
            "",
            "## 6. ETF 工具池",
            "",
            "### 6.1 完整池规模（按分类）",
            "",
            _table(etf_category_counts, ["分类", "数量"])
            if not etf_category_counts.empty
            else "暂无数据。",
            "",
            "### 6.2 双层去重精选（同指数折叠 → 相关簇代表）",
            "",
            _table(
                etf_display,
                [
                    "代码",
                    "名称",
                    "簇",
                    "跟踪",
                    "ETF分",
                    "建议",
                    "同组数",
                    "涨跌幅",
                    "成交额",
                    "同类备选",
                ],
            )
            if not etf_display.empty
            else "暂无 ETF 数据。",
            "",
            "## 7. 潜力扫描（价格形态试运行）",
            "",
            "- 口径：price-only；RS 阈值仍是运行参数，不是 edge 证明；历史胜率含幸存者偏差，仅作相对参考；基本面/题材在 v1 中为中性占位。",
            "",
            _table(
                potential_display,
                [
                    "市场",
                    "代码",
                    "名称",
                    "潜力分",
                    "筑底",
                    "RS",
                    "触发价",
                    "目标价",
                    "止损价",
                    "RR",
                    "历史胜率",
                ],
            )
            if not potential_display.empty
            else "暂无潜力扫描结果。运行 `ah-screener potential-scan` 后刷新。",
            "",
            "## 8. 主题建议",
            "",
            *_bucket_recommendations(refined),
            "",
            "## 9. 提炼候选",
            "",
            _table(
                refined_display,
                [
                    "主题桶",
                    "桶内排名",
                    "风格",
                    "市场",
                    "代码",
                    "名称",
                    "专家分",
                    "基本面",
                    "技术面",
                    "细分行业",
                    "同类分位",
                    "行业适配",
                    "估值分位",
                    "同类组",
                    "匹配主题",
                ],
            )
            if not refined_display.empty
            else "暂无数据。",
            "",
            "## 10. 核心候选",
            "",
            _table(
                core,
                [
                    "市场",
                    "代码",
                    "名称",
                    "专家分",
                    "基本面",
                    "中国大师框架",
                    "技术面",
                    "细分行业",
                    "同类分位",
                    "行业适配",
                    "估值分位",
                    "同类组",
                    "匹配主题",
                ],
            )
            if not core.empty
            else "暂无核心候选。",
            "",
            "## 11. 候选变化",
            "",
            _table(
                change_display,
                ["变化", "主题桶", "市场", "代码", "名称", "最新分", "上期分", "分数变化"],
            )
            if not change_display.empty
            else "当前只有一个提炼快照，下一次定时更新后会生成新增、移出和分数变化。",
            "",
            "## 12. 多期基本面",
            "",
            _table(
                fundamental_display,
                [
                    "市场",
                    "代码",
                    "名称",
                    "基本面",
                    "收入CAGR",
                    "利润CAGR",
                    "ROE均值",
                    "ROE稳定",
                    "多期趋势",
                    "研发费用率",
                    "资本开支/收入",
                    "资本开支/经营现金流",
                    "研发资本效率",
                    "现金流/利润",
                    "资产负债率",
                ],
            )
            if not fundamental_display.empty
            else "暂无多期基本面数据。",
            "",
            "## 13. 操作建议",
            "",
            *_portfolio_notes(refined),
            "",
            "## 14. 后续自动刷新",
            "",
            "建议每天收盘后或每周固定运行完整刷新流程，重新同步行情、技术指标、三表基本面、专家评分和报告。",
            "本项目已提供 `ah-screener update-all` 与 `ah-screener install-schedule` 两个命令用于自动化。",
        ]
    )
    markdown_text = "\n".join(lines).strip() + "\n"
    payload = _build_payload(
        generated_at=generated_at,
        report_date=report_date,
        db_path=str(store.db_path),
        refined=refined,
        expert=expert,
        potential=potential,
        etf_leaders=etf_deduped,
        change_display=change_display,
        date_table=date_table,
        date_warning=date_warning,
        coverage=coverage,
        decision_counts=expert,
        bias_notes=bias_notes,
        markdown_relpath=markdown_relpath,
    )
    return markdown_text, payload


def generate_report(output_dir: Path | None = None) -> Path:
    """Render the report and write Markdown + JSON + stable ``latest`` pointers."""
    store = Store(get_settings().db_path)
    generated_at = datetime.now()
    report_date = generated_at.strftime("%Y-%m-%d")
    output = output_dir or Path("reports")
    output.mkdir(parents=True, exist_ok=True)

    markdown_text, payload = _report_artifacts(store, generated_at)
    validate_report_payload(payload)

    path = output / f"ah-screening-report-{report_date}.md"
    path.write_text(markdown_text, encoding="utf-8")
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    (output / f"ah-screening-report-{report_date}.json").write_text(json_text, encoding="utf-8")
    # Stable pointers so an AI consumer / the UI can always read the freshest report
    # at a fixed path without globbing by date.
    (output / "ah-screening-report-latest.json").write_text(json_text, encoding="utf-8")
    (output / "ah-screening-report-latest.md").write_text(markdown_text, encoding="utf-8")
    return path


def _build_payload(
    *,
    generated_at: datetime,
    report_date: str,
    db_path: str,
    refined: pd.DataFrame,
    expert: pd.DataFrame,
    potential: pd.DataFrame,
    etf_leaders: pd.DataFrame,
    change_display: pd.DataFrame,
    date_table: pd.DataFrame,
    date_warning: str,
    coverage: dict[str, int],
    decision_counts: pd.DataFrame,
    bias_notes: list[str],
    markdown_relpath: str,
) -> dict[str, object]:
    """Assemble the machine-readable report payload (stable English keys).

    This is the AI-facing product: every candidate carries its score breakdown and
    parsed ``reasons`` evidence chain, mirroring exactly what the Markdown shows.
    """
    # Stock candidates are A/HK/US equities (the expert universe is stocks-only).
    refined = refined.copy()
    if not refined.empty:
        refined["trading_system"] = [_trading_system(m, "stock") for m in refined["market"]]
    refined_fields = [
        "bucket",
        "rank_in_bucket",
        "style_bucket",
        "market",
        "trading_system",
        "symbol",
        "name",
        "expert_score",
        "fundamental_score",
        "technical_score",
        "detailed_industry",
        "industry_peer_group",
        "peer_score",
        "industry_fit_score",
        "valuation_percentile",
        "theme_matches",
        "reasons",
        "selection_note",
    ]
    core = (
        expert[expert["decision"] == "core_candidate"].head(20)
        if not expert.empty and "decision" in expert.columns
        else expert.head(0)
    )
    core = core.copy()
    if not core.empty:
        core["trading_system"] = [_trading_system(m, "stock") for m in core["market"]]
    core_fields = [
        "market",
        "trading_system",
        "symbol",
        "name",
        "expert_score",
        "master_score",
        "china_master_score",
        "fundamental_score",
        "technical_score",
        "detailed_industry",
        "industry_peer_group",
        "peer_score",
        "industry_fit_score",
        "valuation_percentile",
        "decision",
        "theme_matches",
        "reasons",
    ]
    potential = potential.copy()
    if not potential.empty:
        potential["trading_system"] = [_trading_system(m, "stock") for m in potential["market"]]
    potential_fields = [
        "market",
        "trading_system",
        "symbol",
        "name",
        "potential_score",
        "technical_setup_score",
        "relative_strength_score",
        "fundamental_turn_score",
        "theme_early_score",
        "pivot_price",
        "target_price",
        "stop_price",
        "rr_ratio",
        "time_stop_days",
        "hist_win_rate",
        "bias_note",
    ]
    etf_leaders = etf_leaders.copy()
    if not etf_leaders.empty:
        categories = (
            etf_leaders["etf_category"]
            if "etf_category" in etf_leaders.columns
            else [None] * len(etf_leaders)
        )
        markets = (
            etf_leaders["market"] if "market" in etf_leaders.columns else ["A"] * len(etf_leaders)
        )
        etf_leaders["trading_system"] = [
            _trading_system(m, "etf", c) for m, c in zip(markets, categories)
        ]
    etf_fields = [
        "market",
        "trading_system",
        "symbol",
        "name",
        "etf_category",
        "etf_cluster",
        "etf_track",
        "etf_score",
        "etf_recommendation",
        "peer_count",
        "peer_alternatives",
        "pct_change",
        "amount",
    ]

    freshness = []
    if not date_table.empty:
        for _, row in date_table.iterrows():
            freshness.append(
                {"market": _clean(row.get("市场")), "latest_date": _clean(row.get("最新日期"))}
            )

    decision_distribution = []
    if not decision_counts.empty and "decision" in decision_counts.columns:
        counts = decision_counts.groupby("decision").size().sort_values(ascending=False)
        decision_distribution = [
            {"decision": str(name), "count": int(value)} for name, value in counts.items()
        ]

    changes = []
    change_key_map = {
        "变化": "change",
        "主题桶": "bucket",
        "市场": "market",
        "代码": "symbol",
        "名称": "name",
        "最新分": "latest_score",
        "上期分": "previous_score",
        "分数变化": "score_delta",
    }
    if not change_display.empty:
        renamed = change_display.rename(columns=change_key_map)
        changes = _records(renamed, list(change_key_map.values()))

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": "ah-screening",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "report_date": report_date,
        "strategy": STRATEGY_NAME,
        "database": db_path,
        "disclaimer": DISCLAIMER,
        "markdown_report": markdown_relpath,
        "conclusion": CONCLUSION_LINES,
        "bias_notes": bias_notes,
        "external_context": EXTERNAL_CONTEXT,
        "data_freshness": freshness,
        "data_freshness_warning": date_warning or None,
        "coverage_counts": {key: int(value) for key, value in coverage.items()},
        "decision_distribution": decision_distribution,
        "counts": {
            "refined_candidates": int(len(refined)),
            "core_candidates": int(len(core)),
            "potential_candidates": int(len(potential)),
            "etf_leaders": int(len(etf_leaders)),
            "refined_by_market": (
                {str(k): int(v) for k, v in refined["market"].value_counts().items()}
                if not refined.empty
                else {}
            ),
        },
        "refined_candidates": _records(
            refined, refined_fields, list_fields=("theme_matches", "reasons")
        ),
        "core_candidates": _records(core, core_fields, list_fields=("theme_matches", "reasons")),
        "potential_candidates": _records(potential, potential_fields),
        "etf_leaders": _records(etf_leaders, etf_fields),
        "candidate_changes": changes,
    }


# --- JSON product contract (see docs/report-schema.md) --------------------------

REPORT_REQUIRED_TOP_KEYS: tuple[str, ...] = (
    "schema_version",
    "report_type",
    "generated_at",
    "report_date",
    "strategy",
    "database",
    "disclaimer",
    "conclusion",
    "bias_notes",
    "coverage_counts",
    "decision_distribution",
    "counts",
    "refined_candidates",
    "core_candidates",
    "potential_candidates",
    "etf_leaders",
    "candidate_changes",
)

# Fields every record in a candidate list must carry (the consumer-facing contract).
REPORT_REQUIRED_RECORD_FIELDS: dict[str, tuple[str, ...]] = {
    "refined_candidates": ("market", "trading_system", "symbol", "name", "expert_score", "bucket"),
    "core_candidates": ("market", "trading_system", "symbol", "name", "expert_score", "decision"),
    "potential_candidates": ("market", "trading_system", "symbol", "name", "potential_score"),
    "etf_leaders": ("market", "trading_system", "symbol", "name"),
}


def validate_report_payload(payload: dict) -> None:
    """Fail loudly if the JSON product drifts from its documented contract.

    Called before the payload is written/returned so a schema regression surfaces at
    generation time instead of silently breaking an AI consumer. See
    ``docs/report-schema.md`` for the field reference.
    """
    missing = [key for key in REPORT_REQUIRED_TOP_KEYS if key not in payload]
    if missing:
        raise ValueError(f"report payload missing required keys: {missing}")
    if not str(payload.get("schema_version") or "").strip():
        raise ValueError("report payload missing schema_version")
    for list_key, required_fields in REPORT_REQUIRED_RECORD_FIELDS.items():
        records = payload.get(list_key) or []
        if not isinstance(records, list):
            raise ValueError(f"{list_key} must be a list")
        for index, record in enumerate(records):
            absent = [field for field in required_fields if field not in record]
            if absent:
                raise ValueError(f"{list_key}[{index}] missing fields: {absent}")
