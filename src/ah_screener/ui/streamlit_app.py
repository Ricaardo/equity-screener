from __future__ import annotations

import json
from html import escape

import streamlit as st

from ah_screener.config import PROJECT_ROOT
from ah_screener.reporting import generate_report


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


REPORTS_DIR = PROJECT_ROOT / "reports"
DECISION_LABELS = {
    "core_candidate": "核心候选",
    "watchlist": "观察",
    "reserve": "储备",
    "reject": "剔除",
}


# --- formatters -----------------------------------------------------------------


def _safe(value: object) -> str:
    if value is None:
        return ""
    return escape(str(value), quote=True)


def _score(value: object) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return _safe(value)


def _price(value: object) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return _safe(value)


def _amount(value: object) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _safe(value)
    if abs(number) >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿"
    if abs(number) >= 10_000:
        return f"{number / 10_000:.1f}万"
    return f"{number:.0f}"


# --- report discovery / loading -------------------------------------------------


def list_report_dates() -> list[str]:
    """Available dated report payloads, newest first (latest pointer excluded)."""
    if not REPORTS_DIR.exists():
        return []
    dates = []
    for path in REPORTS_DIR.glob("ah-screening-report-*.json"):
        stem = path.stem.replace("ah-screening-report-", "")
        if stem != "latest":
            dates.append(stem)
    return sorted(dates, reverse=True)


@st.cache_data(ttl=120)
def load_report(date: str) -> dict | None:
    path = REPORTS_DIR / f"ah-screening-report-{date}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


@st.cache_data(ttl=120)
def load_markdown(date: str) -> str:
    path = REPORTS_DIR / f"ah-screening-report-{date}.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# --- rendering ------------------------------------------------------------------


def render_hero(report: dict) -> None:
    counts = report.get("counts", {})
    st.markdown(
        f"""
        <div class="desk-hero">
          <div class="desk-title">
            <div class="eyebrow">A/H/US Research Desk</div>
            <h1>A/H/US 选股报告</h1>
            <div class="desk-subtitle">
              本页直接展示模型筛选出的研究报告：核心候选、提炼候选、潜力扫描与 ETF 工具池，
              每只候选保留评分拆解和证据链。仅用于研究，不构成投资建议。
            </div>
          </div>
          <div class="desk-status">
            <div class="status-label">报告日期</div>
            <div class="status-value">{_safe(report.get("report_date"))}</div>
            <div class="status-label" style="margin-top:0.7rem;">策略</div>
            <div class="status-value">{_safe(report.get("strategy"))}</div>
            <div class="status-label" style="margin-top:0.7rem;">生成时间</div>
            <div class="status-value">{_safe(report.get("generated_at"))}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cells = [
        ("提炼候选", counts.get("refined_candidates", 0)),
        ("核心候选", counts.get("core_candidates", 0)),
        ("潜力标的", counts.get("potential_candidates", 0)),
        ("ETF 精选", counts.get("etf_leaders", 0)),
    ]
    for item in report.get("data_freshness", [])[:2]:
        cells.append((f"{item.get('market')} 最新", item.get("latest_date") or "--"))
    html = "".join(
        f'<div class="kpi"><div class="label">{_safe(label)}</div>'
        f'<div class="value">{_safe(value)}</div></div>'
        for label, value in cells[:6]
    )
    st.markdown(f'<div class="kpi-grid">{html}</div>', unsafe_allow_html=True)


def render_conclusion(report: dict) -> None:
    conclusion = report.get("conclusion") or []
    body = "<br>".join(_safe(line) for line in conclusion)
    distribution = report.get("decision_distribution", [])
    chips = []
    for item in distribution:
        label = DECISION_LABELS.get(str(item.get("decision")), str(item.get("decision")))
        cls = "chip green" if item.get("decision") == "core_candidate" else "chip"
        chips.append(f'<span class="{cls}">{_safe(label)} {int(item.get("count", 0)):,}</span>')
    warning = report.get("data_freshness_warning")
    warning_html = (
        f'<div style="margin-top:0.6rem;color:var(--accent);">⚠ {_safe(warning)}</div>'
        if warning
        else ""
    )
    st.markdown(
        f"""
        <div class="panel">
          <div class="panel-title"><strong>当前结论</strong><span class="hint">数据驱动 · 不构成投资建议</span></div>
          <div style="padding:0.3rem 0.2rem;line-height:1.75;">{body}</div>
          <div class="chip-row" style="margin-top:0.6rem;">{"".join(chips)}</div>
          {warning_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _candidate_card(item: dict) -> str:
    themes = item.get("theme_matches") or []
    theme_html = "".join(f'<span class="chip green">{_safe(t)}</span>' for t in themes[:3])
    industry = item.get("detailed_industry") or item.get("industry_peer_group") or ""
    meta = " · ".join(
        part for part in [_safe(item.get("market")), _safe(item.get("symbol")), _safe(industry)] if part
    )
    return (
        '<div class="candidate-card">'
        '<div class="candidate-head"><div>'
        f'<div class="candidate-name">{_safe(item.get("name"))}</div>'
        f'<div class="candidate-meta">{meta}</div>'
        "</div>"
        f'<div class="score">{_score(item.get("expert_score"))}</div>'
        "</div>"
        '<div class="chip-row">'
        f'<span class="chip">{_safe(item.get("style_bucket"))}</span>'
        f'<span class="chip">基本面 {_score(item.get("fundamental_score"))}</span>'
        f'<span class="chip">技术 {_score(item.get("technical_score"))}</span>'
        f'<span class="chip">同类 {_score(item.get("peer_score"))}</span>'
        f'<span class="chip">行业 {_score(item.get("industry_fit_score"))}</span>'
        f"{theme_html}"
        "</div>"
        "</div>"
    )


def render_candidate_cards(candidates: list[dict]) -> None:
    if not candidates:
        st.info("当前报告没有提炼候选。运行 `ah-screener update-all` 后重新生成报告。")
        return
    by_bucket: dict[str, list[dict]] = {}
    for item in candidates:
        by_bucket.setdefault(str(item.get("bucket") or "未分组"), []).append(item)
    for bucket, items in by_bucket.items():
        st.markdown(f"##### {_safe(bucket)}　<span class='hint'>{len(items)} 只</span>", unsafe_allow_html=True)
        cards = "".join(_candidate_card(item) for item in items)
        st.markdown(f'<div class="card-grid">{cards}</div>', unsafe_allow_html=True)


def render_core_cards(candidates: list[dict]) -> None:
    if not candidates:
        st.info("当前报告没有核心候选。")
        return
    cards = "".join(_candidate_card(item) for item in candidates)
    st.markdown(f'<div class="card-grid">{cards}</div>', unsafe_allow_html=True)


def _potential_card(item: dict) -> str:
    return (
        '<div class="candidate-card">'
        '<div class="candidate-head"><div>'
        f'<div class="candidate-name">{_safe(item.get("name"))}</div>'
        f'<div class="candidate-meta">{_safe(item.get("market"))} · {_safe(item.get("symbol"))}</div>'
        "</div>"
        f'<div class="score">{_score(item.get("potential_score"))}</div>'
        "</div>"
        '<div class="chip-row">'
        f'<span class="chip">筑底 {_score(item.get("technical_setup_score"))}</span>'
        f'<span class="chip">RS {_score(item.get("relative_strength_score"))}</span>'
        f'<span class="chip green">触发 {_price(item.get("pivot_price"))}</span>'
        f'<span class="chip green">目标 {_price(item.get("target_price"))}</span>'
        f'<span class="chip red">止损 {_price(item.get("stop_price"))}</span>'
        f'<span class="chip">RR {_score(item.get("rr_ratio"))}</span>'
        "</div>"
        "</div>"
    )


def render_potential_cards(candidates: list[dict]) -> None:
    if not candidates:
        st.info("当前报告没有潜力扫描结果。运行 `ah-screener potential-scan` 后重新生成报告。")
        return
    st.caption("口径：price-only；触发/目标/止损为情景参考，RS 阈值仍是运行参数，非 edge 证明。")
    cards = "".join(_potential_card(item) for item in candidates)
    st.markdown(f'<div class="card-grid">{cards}</div>', unsafe_allow_html=True)


def render_evidence(candidates: list[dict]) -> None:
    """Per-candidate reasoning (the evidence chain), folded to keep the page calm."""
    rows = [item for item in candidates if item.get("reasons")]
    if not rows:
        return
    with st.expander("查看完整推理（证据链）"):
        for item in rows:
            header = f"**{_safe(item.get('name'))}** · {_safe(item.get('market'))} {_safe(item.get('symbol'))} · 专家分 {_score(item.get('expert_score'))}"
            st.markdown(header)
            st.markdown("\n".join(f"- {reason}" for reason in item.get("reasons", [])))
            st.divider()


def render_etf(leaders: list[dict]) -> None:
    if not leaders:
        st.info("当前报告没有 ETF 工具池。")
        return
    table = [
        {
            "代码": item.get("symbol"),
            "名称": item.get("name"),
            "簇": item.get("etf_cluster"),
            "跟踪": item.get("etf_track"),
            "ETF分": _score(item.get("etf_score")),
            "建议": item.get("etf_recommendation"),
            "同组数": item.get("peer_count"),
            "成交额": _amount(item.get("amount")),
        }
        for item in leaders
    ]
    st.dataframe(table, width="stretch", hide_index=True)


def render_changes(changes: list[dict]) -> None:
    if not changes:
        st.caption("只有一个提炼快照，下一次刷新后会出现新增 / 移出 / 分数变化。")
        return
    table = [
        {
            "变化": item.get("change"),
            "主题桶": item.get("bucket"),
            "市场": item.get("market"),
            "代码": item.get("symbol"),
            "名称": item.get("name"),
            "最新分": _score(item.get("latest_score")),
            "上期分": _score(item.get("previous_score")),
            "分数变化": _score(item.get("score_delta")),
        }
        for item in changes
    ]
    st.dataframe(table, width="stretch", hide_index=True)


# --- page -----------------------------------------------------------------------

st.markdown(DESK_CSS, unsafe_allow_html=True)

st.sidebar.markdown("## 报告")
available_dates = list_report_dates()

if st.sidebar.button("🔄 立即重新生成报告", width="stretch"):
    with st.spinner("正在基于当前数据库生成报告…"):
        try:
            generate_report()
            load_report.clear()
            load_markdown.clear()
            available_dates = list_report_dates()
            st.sidebar.success("报告已刷新。")
        except Exception as exc:  # surfaced to the user, not swallowed
            st.sidebar.error(f"生成失败：{exc}")

if not available_dates:
    st.warning(
        "还没有筛选报告。请先运行 `ah-screener update-all` 或在左侧点击「立即重新生成报告」。"
    )
    st.stop()

selected_date = st.sidebar.selectbox("选择报告日期", available_dates, index=0)
st.sidebar.caption("默认显示最新报告。本页只读，不做实时筛选。")

report = load_report(selected_date)
if report is None:
    st.error(f"无法读取报告 {selected_date}。")
    st.stop()

render_hero(report)
render_conclusion(report)

core_tab, refined_tab, potential_tab, etf_tab, full_tab = st.tabs(
    ["核心候选", "提炼候选", "潜力扫描", "ETF 工具池", "完整报告"]
)

with core_tab:
    render_core_cards(report.get("core_candidates", []))
    render_evidence(report.get("core_candidates", []))

with refined_tab:
    render_candidate_cards(report.get("refined_candidates", []))
    render_evidence(report.get("refined_candidates", []))
    st.markdown("##### 候选变化")
    render_changes(report.get("candidate_changes", []))

with potential_tab:
    render_potential_cards(report.get("potential_candidates", []))

with etf_tab:
    render_etf(report.get("etf_leaders", []))

with full_tab:
    markdown_text = load_markdown(selected_date)
    if markdown_text:
        st.markdown(markdown_text)
    else:
        st.info("未找到对应的 Markdown 报告文件。")

st.sidebar.markdown("---")
st.sidebar.caption(report.get("disclaimer", ""))
