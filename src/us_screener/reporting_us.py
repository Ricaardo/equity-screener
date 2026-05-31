"""US pre-market reporting helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ah_screener.db import get_store
from us_screener.config import get_us_config, use_us_database
from us_screener.llm_opinion import generate_us_llm_opinion
from us_screener.scoring_us import run_us_screen


REPORT_TYPE = "us-premarket"
REPORT_SCHEMA_VERSION = "0.1"
DISCLAIMER = "US screener output is for research and candidate review only, not investment advice."


def _clean(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        if np.isnan(number) or np.isinf(number):
            return None
        return number
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.strftime("%Y-%m-%d")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value if isinstance(value, (str, int, dict, list)) else str(value)


def _records(df: pd.DataFrame, fields: list[str]) -> list[dict[str, object]]:
    if df.empty:
        return []
    present = [field for field in fields if field in df.columns]
    rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        rows.append({field: _clean(row.get(field)) for field in present})
    return rows


def build_us_premarket_payload(store=None, *, screen_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a JSON-serializable US pre-market payload without writing files."""
    if store is None:
        use_us_database()
        store = get_store()
    result = screen_result or run_us_screen(store=store, persist=True)
    scored = result["results"].copy()
    top = scored.loc[~scored["is_filtered"]].head(20).copy() if not scored.empty else pd.DataFrame()
    rejected = scored.loc[scored["is_filtered"]].copy() if not scored.empty else pd.DataFrame()

    fields = [
        "market",
        "symbol",
        "name",
        "expert_score",
        "decision",
        "fundamental_score_final",
        "technical_score",
        "valuation_score",
        "market_cap",
        "pe_ttm",
        "pb",
        "peg",
        "liquidity_score",
        "heat_score",
        "rs_score",
        "short_ratio",
        "macro_score",
        "concept_boards",
        "filter_reasons",
        "score_components",
        "heat_components",
        "macro_components",
        "reasons_list",
    ]
    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": REPORT_TYPE,
        "report_date": result["snapshot_date"] or datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "database": str(get_us_config().db_path),
        "disclaimer": DISCLAIMER,
        "macro_context": result["macro_context"],
        "counts": {
            "universe": int(len(scored)),
            "candidates": int((~scored["is_filtered"]).sum()) if not scored.empty else 0,
            "filtered": int(scored["is_filtered"].sum()) if not scored.empty else 0,
            "core_candidates": int((top["decision"] == "core_candidate").sum()) if not top.empty else 0,
        },
        "filtered_summary": {
            key: int(value)
            for key, value in (
                rejected["filter_reasons"].explode().value_counts().to_dict() if not rejected.empty else {}
            ).items()
        },
        "top_candidates": _records(top, fields),
        "rejected_candidates": _records(rejected.head(30), fields),
    }
    _annotate_earnings(payload, store)
    _annotate_squeeze(payload)
    _annotate_forward(payload, store)
    _annotate_themes(payload, store, scored)
    payload["llm_opinion"] = generate_us_llm_opinion(payload)
    return payload


def _annotate_themes(payload: dict[str, Any], store, scored: pd.DataFrame | None = None) -> None:
    """Rank concept boards by constituents' relative strength (what the market is
    bidding up) and flag candidates riding a hot theme."""
    from us_screener.theme_momentum import compute_theme_momentum

    # Reuse the RS the screen already computed for the whole universe (avoid a second
    # daily-history pass). Hot themes are based on tradable candidates, not the long
    # tail of names already rejected by hard filters.
    rs = None
    if scored is not None and not scored.empty and "rs_score" in scored.columns:
        tradable = scored.loc[~scored["is_filtered"]] if "is_filtered" in scored.columns else scored
        rs = tradable[["market", "symbol", "rs_score"]].copy()
    try:
        frame = compute_theme_momentum(store, rs=rs)
    except Exception:  # noqa: BLE001 — never let theme momentum break the report
        frame = pd.DataFrame()
    if frame.empty:
        payload["hot_themes"] = []
        return
    hot = frame.head(8).to_dict("records")
    payload["hot_themes"] = [
        {"board": r["board"], "momentum_score": r["momentum_score"], "members": int(r["members"]),
         "leaders_pct": r["leaders_pct"]}
        for r in hot
    ]
    hot_set = {r["board"] for r in hot[:5] if r["momentum_score"] >= 55}
    for item in payload.get("top_candidates") or []:
        boards = item.get("concept_boards") or []
        riding = [b for b in boards if b in hot_set]
        if riding:
            item["hot_themes"] = riding


def _annotate_squeeze(payload: dict[str, Any]) -> None:
    """Flag squeeze watch: elevated short-volume ratio + market leadership (high RS)."""
    watch: list[dict[str, Any]] = []
    for item in payload.get("top_candidates") or []:
        sr = item.get("short_ratio")
        rs = item.get("rs_score")
        if isinstance(sr, (int, float)) and sr >= 0.5 and isinstance(rs, (int, float)) and rs >= 70:
            watch.append({"symbol": item.get("symbol"), "short_ratio": round(float(sr), 3), "rs_score": rs})
    payload["squeeze_watch"] = sorted(watch, key=lambda r: r["short_ratio"], reverse=True)


def _annotate_forward(payload: dict[str, Any], store) -> None:
    """Attach best-effort forward PE (analyst-expectation colour) to each top candidate.

    Read-only: reads whatever ``forward_estimates`` cached into ``company_tags`` (the
    pipeline enriches a bounded top-liquid set). Display-only — forward PE never feeds
    the score (free forward estimates are sparse / fragile; see forward_estimates.py).
    """
    from us_screener.forward_estimates import forward_pe_map

    try:
        fmap = forward_pe_map(store)
    except Exception:  # noqa: BLE001 — never let the forward overlay break the report
        fmap = {}
    covered = 0
    for item in payload.get("top_candidates") or []:
        forward_pe = fmap.get(str(item.get("symbol") or "").strip().upper())
        if forward_pe is not None:
            item["forward_pe"] = round(float(forward_pe), 2)
            covered += 1
    payload["forward_pe_coverage"] = covered


def _annotate_earnings(payload: dict[str, Any], store) -> None:
    """Tag each candidate with its next earnings date and collect names reporting
    within a week (single-name gap risk = the classic pre-market caution)."""
    from us_screener.earnings import earnings_map

    try:
        emap = earnings_map(store)
    except Exception:  # noqa: BLE001 — never let earnings break the report
        emap = {}
    if not emap:
        payload["earnings_soon"] = []
        return
    today = datetime.now().date()
    soon: list[dict[str, Any]] = []
    for item in payload.get("top_candidates") or []:
        info = emap.get(str(item.get("symbol") or "").strip().upper())
        if not info:
            continue
        item["earnings_date"] = info["date"]
        item["earnings_when"] = info["when"]
        try:
            days = (datetime.strptime(info["date"], "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        item["earnings_in_days"] = days
        if 0 <= days <= 7:
            soon.append({"symbol": item.get("symbol"), "earnings_date": info["date"], "in_days": days})
    payload["earnings_soon"] = sorted(soon, key=lambda r: r["in_days"])


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# US Pre-Market Screener",
        "",
        f"- Report date: {payload.get('report_date')}",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Disclaimer: {payload.get('disclaimer')}",
        "",
        "## Macro context",
        "",
        f"- Regime: {(payload.get('macro_context') or {}).get('regime')}",
        f"- Market score: {(payload.get('macro_context') or {}).get('market_score')}",
        f"- Summary: {(payload.get('macro_context') or {}).get('summary')}",
        "",
    ]
    hot_themes = payload.get("hot_themes") or []
    if hot_themes:
        lines.extend(["## Hot themes (price momentum)", ""])
        for theme in hot_themes[:6]:
            lines.append(
                f"- {theme['board']}: momentum {theme['momentum_score']} "
                f"({theme['members']} 标的, {theme['leaders_pct']}% 领先)"
            )
        lines.append("")
    lines.extend(["## Top candidates", ""])
    for item in payload.get("top_candidates") or []:
        earnings = (
            f", earnings {item['earnings_date']} (in {item.get('earnings_in_days')}d)"
            if item.get("earnings_date")
            else ""
        )
        fwd = f", fwdPE {item['forward_pe']}" if item.get("forward_pe") is not None else ""
        lines.append(
            "- "
            f"{item.get('symbol')} {item.get('name')}: score {item.get('expert_score')}, "
            f"decision {item.get('decision')}, boards {', '.join(item.get('concept_boards') or []) or '--'}"
            f"{fwd}{earnings}"
        )
    if not (payload.get("top_candidates") or []):
        lines.append("- No candidates.")

    soon = payload.get("earnings_soon") or []
    if soon:
        lines.extend(["", "## Earnings within 7 days (gap risk)", ""])
        for entry in soon:
            lines.append(f"- {entry['symbol']}: {entry['earnings_date']} (in {entry['in_days']}d)")

    lines.extend(["", "## Filtered summary", ""])
    for key, value in (payload.get("filtered_summary") or {}).items():
        lines.append(f"- {key}: {value}")
    if not (payload.get("filtered_summary") or {}):
        lines.append("- No filtered names.")

    opinion = payload.get("llm_opinion") or {}
    lines.extend(["", "## LLM opinion", ""])
    lines.append(f"- Status: {opinion.get('status')}")
    if opinion.get("summary"):
        lines.append(f"- Summary: {opinion.get('summary')}")
    if opinion.get("stance"):
        lines.append(f"- Stance: {opinion.get('stance')}")
    for risk in opinion.get("risks") or []:
        lines.append(f"- Risk: {risk}")
    for action in opinion.get("actions") or []:
        lines.append(f"- Action: {action}")
    return "\n".join(lines).strip() + "\n"


def generate_us_premarket_report(
    output_dir: Path | None = None,
    *,
    store=None,
    screen_result: dict[str, Any] | None = None,
) -> Path:
    """Write dated and latest JSON/Markdown report artifacts."""
    cfg = get_us_config()
    payload = build_us_premarket_payload(store=store, screen_result=screen_result)
    output = output_dir or cfg.reports_dir
    output.mkdir(parents=True, exist_ok=True)

    report_date = str(payload["report_date"])
    json_path = output / f"us-premarket-{report_date}.json"
    md_path = output / f"us-premarket-{report_date}.md"
    latest_json = output / "us-premarket-latest.json"
    latest_md = output / "us-premarket-latest.md"

    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    md_text = _render_markdown(payload)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    latest_md.write_text(md_text, encoding="utf-8")
    return md_path
