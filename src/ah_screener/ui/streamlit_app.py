from __future__ import annotations

import json
from html import escape

import streamlit as st

from ah_screener.config import PROJECT_ROOT
from ah_screener.reporting import generate_report


st.set_page_config(page_title="A/H/US Daily Brief", layout="wide", initial_sidebar_state="expanded")


REPORTS_DIR = PROJECT_ROOT / "reports"
MARKET_LABELS = {"A": "A股", "HK": "港股", "US": "美股"}


DESK_CSS = """
<style>
:root {
  --page: #f6f7f5;
  --surface: #ffffff;
  --surface-soft: #eef3f1;
  --ink: #121417;
  --muted: #667085;
  --line: #d8dedb;
  --accent: #0f766e;
  --accent-soft: #dff3ef;
  --warn: #b42318;
  --warn-soft: #fde7e4;
  --blue: #245d82;
  --amber: #8a5a12;
  --shadow: rgba(16, 24, 40, 0.08);
}

.stApp {
  color: var(--ink);
  background: var(--page);
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

.block-container {
  max-width: 1480px;
  padding: 1.05rem 1.35rem 2.4rem !important;
}

section[data-testid="stSidebar"] {
  background: #151a1f;
  border-right: 1px solid #252d34;
}

section[data-testid="stSidebar"] * {
  color: #f6f7f5;
}

section[data-testid="stSidebar"] div[data-baseweb="select"] > div,
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea {
  background: #202830;
  border-color: #3b4650;
}

section[data-testid="stSidebar"] button {
  background: #202830;
  border: 1px solid #3b4650;
}

section[data-testid="stSidebar"] button p {
  color: #f6f7f5 !important;
}

h1, h2, h3 {
  color: var(--ink);
  letter-spacing: 0;
}

div[data-testid="stTabs"] button {
  color: var(--muted);
  font-size: 0.94rem;
  background: transparent;
  border-bottom: 2px solid transparent;
  border-radius: 0;
}

div[data-testid="stTabs"] button[aria-selected="true"] {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 310px;
  gap: 0.85rem;
  align-items: stretch;
  margin-bottom: 0.85rem;
}

.hero-main,
.hero-side,
.panel,
.card,
.etf-card,
.potential-card {
  background: var(--surface);
  border: 1px solid var(--line);
  box-shadow: 0 12px 30px var(--shadow);
}

.hero-main {
  padding: 1.1rem 1.18rem;
  border-left: 4px solid var(--accent);
}

.eyebrow {
  color: var(--accent);
  font-size: 0.74rem;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.hero h1 {
  margin: 0.2rem 0 0.42rem;
  font-size: clamp(1.75rem, 3vw, 2.8rem);
  line-height: 1.08;
}

.hero-subtitle {
  color: var(--muted);
  font-size: 0.96rem;
  line-height: 1.58;
  max-width: 940px;
}

.hero-side {
  padding: 1rem;
  background: #111827;
  color: #f9fafb;
}

.hero-side .side-label {
  color: #aab5c0;
  font-size: 0.76rem;
  margin-top: 0.72rem;
}

.hero-side .side-label:first-child {
  margin-top: 0;
}

.hero-side .side-value {
  color: #ffffff;
  font-size: 0.98rem;
  line-height: 1.45;
  word-break: break-word;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(130px, 1fr));
  gap: 0.65rem;
  margin: 0.75rem 0 0.95rem;
}

.kpi {
  background: var(--surface);
  border: 1px solid var(--line);
  padding: 0.72rem 0.78rem;
}

.kpi .label {
  color: var(--muted);
  font-size: 0.75rem;
}

.kpi .value {
  color: var(--ink);
  font-size: 1.52rem;
  line-height: 1.1;
  font-weight: 760;
  margin-top: 0.22rem;
}

.panel {
  padding: 0.95rem;
  margin-bottom: 0.82rem;
}

.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  border-bottom: 1px solid var(--line);
  padding-bottom: 0.5rem;
  margin-bottom: 0.72rem;
}

.panel-title strong {
  font-size: 1.02rem;
}

.hint {
  color: var(--muted);
  font-size: 0.78rem;
}

.brief-list {
  display: grid;
  gap: 0.48rem;
}

.brief-item {
  display: flex;
  align-items: flex-start;
  gap: 0.55rem;
  line-height: 1.62;
  color: var(--ink);
}

.dot {
  width: 0.5rem;
  height: 0.5rem;
  margin-top: 0.58rem;
  border-radius: 999px;
  background: var(--accent);
  flex: 0 0 auto;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(285px, 1fr));
  gap: 0.72rem;
}

.card,
.etf-card,
.potential-card {
  padding: 0.86rem;
  min-height: 168px;
  border-left: 4px solid var(--accent);
}

.etf-card {
  border-left-color: var(--blue);
}

.potential-card {
  border-left-color: var(--amber);
}

.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.65rem;
  margin-bottom: 0.6rem;
}

.name {
  font-weight: 760;
  font-size: 1rem;
  line-height: 1.28;
}

.meta {
  color: var(--muted);
  font-size: 0.76rem;
  line-height: 1.42;
  margin-top: 0.16rem;
}

.score {
  min-width: 54px;
  text-align: center;
  border: 1px solid var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
  padding: 0.23rem 0.35rem;
  font-weight: 780;
}

.score.blue {
  border-color: var(--blue);
  color: var(--blue);
  background: #e7f0f6;
}

.score.amber {
  border-color: var(--amber);
  color: var(--amber);
  background: #fbefd9;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.32rem;
  margin: 0.44rem 0;
}

.chip {
  border: 1px solid var(--line);
  background: var(--surface-soft);
  color: #344054;
  padding: 0.13rem 0.38rem;
  font-size: 0.72rem;
  white-space: nowrap;
}

.chip.good {
  border-color: #8fcfc4;
  background: var(--accent-soft);
  color: #0f5f58;
}

.chip.warn {
  border-color: #f1b3ad;
  background: var(--warn-soft);
  color: var(--warn);
}

.mini-block {
  margin-top: 0.52rem;
  font-size: 0.82rem;
  line-height: 1.56;
}

.mini-label {
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 760;
  margin-bottom: 0.12rem;
}

.action-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 0.58rem;
}

.action {
  background: var(--surface);
  border: 1px solid var(--line);
  padding: 0.7rem;
}

.action .label {
  color: var(--accent);
  font-size: 0.75rem;
  font-weight: 780;
}

.action .body {
  margin-top: 0.22rem;
  font-size: 0.92rem;
  line-height: 1.48;
}

.section-gap {
  height: 0.3rem;
}

div[data-testid="stAlert"] {
  background: #fff8e8;
  border: 1px solid #eed096;
  color: var(--ink);
}

@media (max-width: 980px) {
  .hero {
    grid-template-columns: 1fr;
  }
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(120px, 1fr));
  }
}
</style>
"""


def _safe(value: object) -> str:
    if value is None:
        return ""
    return escape(str(value), quote=True)


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _score(value: object) -> str:
    number = _number(value)
    return "--" if number is None else f"{number:.1f}"


def _price(value: object) -> str:
    number = _number(value)
    return "--" if number is None else f"{number:.2f}"


def _amount(value: object) -> str:
    number = _number(value)
    if number is None:
        return ""
    if abs(number) >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿"
    if abs(number) >= 10_000:
        return f"{number / 10_000:.1f}万"
    return f"{number:.0f}"


def _first(items: object, default: str = "") -> str:
    if isinstance(items, list) and items:
        return str(items[0])
    return default


def _bullets(items: object, limit: int = 3) -> str:
    if not isinstance(items, list) or not items:
        return ""
    return "<br>".join(f"- {_safe(item)}" for item in items[:limit])


def _chip(text: object, cls: str = "") -> str:
    if text is None or str(text) == "":
        return ""
    class_name = f"chip {cls}".strip()
    return f'<span class="{class_name}">{_safe(text)}</span>'


def _filter_market(records: list[dict], markets: list[str]) -> list[dict]:
    return [item for item in records if not markets or item.get("market") in markets]


def _filter_min_score(records: list[dict], field: str, min_score: float) -> list[dict]:
    out = []
    for item in records:
        number = _number(item.get(field))
        if number is not None and number >= min_score:
            out.append(item)
    return out


def list_report_dates() -> list[str]:
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
def load_markdown(date: str, *, appendix: bool = False) -> str:
    filename = f"ah-screening-appendix-{date}.md" if appendix else f"ah-screening-report-{date}.md"
    path = REPORTS_DIR / filename
    if not path.exists() and appendix:
        path = REPORTS_DIR / f"ah-screening-report-{date}.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def render_hero(report: dict) -> None:
    counts = report.get("counts") or {}
    by_market = counts.get("refined_by_market") or {}
    st.markdown(
        f"""
        <div class="hero">
          <div class="hero-main">
            <div class="eyebrow">A/H/US Daily Brief</div>
            <h1>每日筛选摘要</h1>
            <div class="hero-subtitle">
              {_safe(_first(report.get("conclusion")))}
            </div>
          </div>
          <div class="hero-side">
            <div class="side-label">报告日期</div>
            <div class="side-value">{_safe(report.get("report_date"))}</div>
            <div class="side-label">策略</div>
            <div class="side-value">{_safe(report.get("strategy"))}</div>
            <div class="side-label">附录</div>
            <div class="side-value">{_safe(report.get("appendix_report"))}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cells = [
        ("提炼", counts.get("refined_candidates", 0)),
        ("核心", counts.get("core_candidates", 0)),
        ("ETF", counts.get("etf_leaders", 0)),
        ("潜力", counts.get("potential_candidates", 0)),
        ("A/HK/US", f"{by_market.get('A', 0)}/{by_market.get('HK', 0)}/{by_market.get('US', 0)}"),
    ]
    html = "".join(
        f'<div class="kpi"><div class="label">{_safe(label)}</div>'
        f'<div class="value">{_safe(value)}</div></div>'
        for label, value in cells
    )
    st.markdown(f'<div class="kpi-grid">{html}</div>', unsafe_allow_html=True)


def render_brief(report: dict) -> None:
    brief = report.get("daily_brief") or {}
    data_health = brief.get("data_health") or {}
    freshness = data_health.get("freshness") or report.get("data_freshness") or []
    fresh_text = " · ".join(
        f"{item.get('market')} {item.get('latest_date')}" for item in freshness if item
    )
    lines = [brief.get("headline"), brief.get("focus")]
    if fresh_text:
        lines.append(f"数据日期：{fresh_text}")
    if data_health.get("warning"):
        lines.append(str(data_health["warning"]))
    body = "".join(
        f'<div class="brief-item"><span class="dot"></span><span>{_safe(line)}</span></div>'
        for line in lines
        if line
    )
    st.markdown(
        f"""
        <div class="panel">
          <div class="panel-title"><strong>今日结论</strong><span class="hint">摘要优先</span></div>
          <div class="brief-list">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_actions(report: dict) -> None:
    actions = report.get("top_actions") or []
    if not actions:
        return
    html = ""
    for item in actions[:8]:
        delta = item.get("delta")
        delta_text = "" if delta is None else f"｜变化 {_score(delta)}"
        html += (
            '<div class="action">'
            f'<div class="label">{_safe(item.get("label"))}</div>'
            f'<div class="body">{_safe(item.get("market"))} {_safe(item.get("symbol"))} '
            f"{_safe(item.get('name'))}<br>分数 {_score(item.get('score'))}{_safe(delta_text)}</div>"
            "</div>"
        )
    st.markdown(
        f"""
        <div class="panel">
          <div class="panel-title"><strong>今日变化</strong><span class="hint">新增与大幅变化</span></div>
          <div class="action-list">{html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def candidate_card(item: dict) -> str:
    themes = item.get("theme_matches") if isinstance(item.get("theme_matches"), list) else []
    why = _bullets(item.get("why_selected"), 3)
    risks = _bullets(item.get("key_risks"), 2)
    checks = _bullets(item.get("verify_before_action"), 2)
    theme_html = "".join(_chip(theme, "good") for theme in themes[:2])
    return (
        '<div class="card">'
        '<div class="card-head"><div>'
        f'<div class="name">{_safe(item.get("name"))}</div>'
        f'<div class="meta">{_safe(MARKET_LABELS.get(str(item.get("market")), item.get("market")))} '
        f"{_safe(item.get('symbol'))} · {_safe(item.get('style_bucket'))}</div>"
        "</div>"
        f'<div class="score">{_score(item.get("expert_score"))}</div>'
        "</div>"
        '<div class="chip-row">'
        f"{_chip(item.get('trading_system'), 'good')}"
        f"{_chip('基本面 ' + _score(item.get('fundamental_score')))}"
        f"{_chip('技术 ' + _score(item.get('technical_score')))}"
        f"{theme_html}"
        "</div>"
        f'<div class="mini-block"><div class="mini-label">入选理由</div>{why}</div>'
        f'<div class="mini-block"><div class="mini-label">主要风险</div>{risks}</div>'
        f'<div class="mini-block"><div class="mini-label">买前核验</div>{checks}</div>'
        "</div>"
    )


def render_priority(report: dict, markets: list[str], min_score: float) -> None:
    candidates = report.get("refined_candidates") or []
    candidates = _filter_market(candidates, markets)
    candidates = _filter_min_score(candidates, "expert_score", min_score)
    if not candidates:
        st.info("当前条件下没有提炼候选。")
        return
    cards = "".join(candidate_card(item) for item in candidates[:24])
    st.markdown(
        f"""
        <div class="panel">
          <div class="panel-title"><strong>优先研究</strong><span class="hint">{len(candidates)} 只</span></div>
          <div class="card-grid">{cards}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    rows = [item for item in candidates if item.get("reasons")]
    if rows:
        with st.expander("证据链"):
            for item in rows[:24]:
                st.markdown(
                    f"**{item.get('market')} {item.get('symbol')} {item.get('name')}** · "
                    f"专家分 {_score(item.get('expert_score'))}"
                )
                st.markdown("\n".join(f"- {reason}" for reason in item.get("reasons", [])[:12]))
                st.divider()


def etf_card(item: dict) -> str:
    alternatives = item.get("alternatives") if isinstance(item.get("alternatives"), list) else []
    why = _bullets(item.get("why_selected"), 3)
    alt_text = "；".join(str(alt) for alt in alternatives[:3]) or "暂无"
    return (
        '<div class="etf-card">'
        '<div class="card-head"><div>'
        f'<div class="name">{_safe(item.get("name"))}</div>'
        f'<div class="meta">{_safe(item.get("market"))} {_safe(item.get("symbol"))} · '
        f"{_safe(item.get('etf_cluster') or item.get('etf_category'))}</div>"
        "</div>"
        f'<div class="score blue">{_score(item.get("etf_score"))}</div>'
        "</div>"
        '<div class="chip-row">'
        f"{_chip(item.get('trading_system'), 'good')}"
        f"{_chip(item.get('etf_track'))}"
        f"{_chip('同组 ' + _safe(item.get('peer_count') or 1))}"
        f"{_chip('成交额 ' + _amount(item.get('amount')))}"
        "</div>"
        f'<div class="mini-block"><div class="mini-label">为什么选它</div>{why}</div>'
        f'<div class="mini-block"><div class="mini-label">备选</div>{_safe(alt_text)}</div>'
        f'<div class="mini-block"><div class="mini-label">注意</div>{_safe(item.get("caution"))}</div>'
        "</div>"
    )


def render_etf_toolbox(report: dict, use_case_filter: list[str]) -> None:
    use_cases = report.get("etf_use_cases") or []
    selected = set(use_case_filter)
    rendered = 0
    for case in use_cases:
        if selected and case.get("title") not in selected:
            continue
        leaders = case.get("leaders") if isinstance(case.get("leaders"), list) else []
        if not leaders:
            continue
        rendered += 1
        cards = "".join(etf_card(item) for item in leaders[:5])
        st.markdown(
            f"""
            <div class="panel">
              <div class="panel-title">
                <strong>{_safe(case.get("title"))}</strong>
                <span class="hint">{_safe(case.get("description"))}</span>
              </div>
              <div class="card-grid">{cards}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    if rendered == 0:
        st.info("当前条件下没有 ETF 工具。")
    with st.expander("完整 ETF 明细"):
        rows = []
        for item in report.get("etf_leaders") or []:
            rows.append(
                {
                    "用途": item.get("use_case"),
                    "市场": item.get("market"),
                    "交易": item.get("trading_system"),
                    "代码": item.get("symbol"),
                    "名称": item.get("name"),
                    "分类": item.get("etf_category"),
                    "簇": item.get("etf_cluster"),
                    "跟踪": item.get("etf_track"),
                    "ETF分": _score(item.get("etf_score")),
                    "同组数": item.get("peer_count"),
                    "成交额": _amount(item.get("amount")),
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)


def potential_card(item: dict) -> str:
    scenario = item.get("scenario") if isinstance(item.get("scenario"), dict) else {}
    return (
        '<div class="potential-card">'
        '<div class="card-head"><div>'
        f'<div class="name">{_safe(item.get("name"))}</div>'
        f'<div class="meta">{_safe(item.get("market"))} {_safe(item.get("symbol"))}</div>'
        "</div>"
        f'<div class="score amber">{_score(item.get("potential_score"))}</div>'
        "</div>"
        '<div class="chip-row">'
        f"{_chip(item.get('trading_system'), 'good')}"
        f"{_chip('筑底 ' + _score(item.get('technical_setup_score')))}"
        f"{_chip('RS ' + _score(item.get('relative_strength_score')))}"
        f"{_chip('RR ' + _score(item.get('rr_ratio')))}"
        "</div>"
        '<div class="mini-block"><div class="mini-label">情景</div>'
        f"触发 {_safe(scenario.get('trigger'))}；目标 {_safe(scenario.get('target'))}；"
        f"止损 {_safe(scenario.get('stop'))}</div>"
        f'<div class="mini-block"><div class="mini-label">证伪</div>{_safe(item.get("invalid_if"))}</div>'
        "</div>"
    )


def render_potential(report: dict, markets: list[str]) -> None:
    candidates = _filter_market(report.get("potential_candidates") or [], markets)
    if not candidates:
        st.info("当前报告没有潜力扫描结果。")
        return
    cards = "".join(potential_card(item) for item in candidates[:18])
    st.markdown(
        f"""
        <div class="panel">
          <div class="panel-title"><strong>潜力情景</strong><span class="hint">price-only</span></div>
          <div class="card-grid">{cards}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_appendix(report: dict, selected_date: str) -> None:
    st.markdown("##### 候选变化")
    changes = []
    for item in report.get("candidate_changes") or []:
        changes.append(
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
        )
    if changes:
        st.dataframe(changes, width="stretch", hide_index=True)
    else:
        st.caption("当前缺少可比较的候选变化。")
    st.divider()
    text = load_markdown(selected_date, appendix=True)
    if text:
        st.markdown(text)
    else:
        st.info("未找到附录 Markdown。")


st.markdown(DESK_CSS, unsafe_allow_html=True)

st.sidebar.markdown("## 报告")
available_dates = list_report_dates()

if st.sidebar.button("重新生成报告", width="stretch"):
    with st.spinner("正在生成报告..."):
        try:
            generate_report()
            load_report.clear()
            load_markdown.clear()
            available_dates = list_report_dates()
            st.sidebar.success("报告已刷新。")
        except Exception as exc:
            st.sidebar.error(f"生成失败：{exc}")

if not available_dates:
    st.warning("还没有筛选报告。请先运行 `ah-screener update-all` 或生成报告。")
    st.stop()

selected_date = st.sidebar.selectbox("报告日期", available_dates, index=0)
report = load_report(selected_date)
if report is None:
    st.error(f"无法读取报告 {selected_date}。")
    st.stop()

market_options = list(MARKET_LABELS.keys())
selected_markets = st.sidebar.multiselect(
    "市场",
    market_options,
    default=market_options,
    format_func=lambda key: MARKET_LABELS.get(key, key),
)
min_score = st.sidebar.slider("专家分下限", 0, 100, 55)
case_titles = [
    str(case.get("title"))
    for case in report.get("etf_use_cases", [])
    if isinstance(case, dict) and case.get("leaders")
]
selected_cases = st.sidebar.multiselect("ETF 用途", case_titles, default=case_titles)
st.sidebar.caption(report.get("disclaimer", ""))

render_hero(report)

summary_tab, priority_tab, etf_tab, potential_tab, appendix_tab = st.tabs(
    ["今日摘要", "优先研究", "ETF工具箱", "潜力情景", "证据附录"]
)

with summary_tab:
    render_brief(report)
    render_actions(report)
    render_priority(report, selected_markets, float(min_score))

with priority_tab:
    render_priority(report, selected_markets, float(min_score))

with etf_tab:
    render_etf_toolbox(report, selected_cases)

with potential_tab:
    render_potential(report, selected_markets)

with appendix_tab:
    render_appendix(report, selected_date)
