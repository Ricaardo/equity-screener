---
name: us-screener
description: Independent US-only stock auto-screener — full localization, daily pre-market candidate report, China-concept exclusion, heat + macro factors, optional LLM opinion. Use when the user wants to screen the US market, refresh the local US universe, or read the latest US pre-market report.
---

# US Screener

`us-screener` is the standalone US-only pre-market workflow. It keeps its **own**
DuckDB (separate from the A/HK store) via `US_SCREENER_DB` → `AH_SCREENER_DB`
routing, hard-excludes China-concept names before scoring, and blends
fundamental / technical / valuation / liquidity / **heat** / **macro** factors.

## When to use
- "刷新美股本地库 / 全量本地化" → `backfill` (first run) then `update` (daily).
- "美股盘前选股 / 今天美股有什么候选" → `screen` or read the latest `report`.
- "给个 go/no-go 意见" → `opinion` (needs an LLM key; skips cleanly without one).

## Commands (all support `--json` for agent parsing)
- `us-screener info --json` — resolved config: db path, reports dir, filters, LLM provider, schedule.
- `us-screener backfill` — first-run full localization (universe + history + SEC fundamentals).
- `us-screener update` — incremental daily refresh, then screen + report.
- `us-screener screen --top 20 --json` — run the US-tuned screen, return ranked candidates.
- `us-screener report --json` — write `reports/us-premarket/us-premarket-YYYY-MM-DD.{json,md}` + `-latest` pointers.
- `us-screener opinion --json` — optional LLM go/no-go opinion (status `skipped` when no key).
- `us-screener schedule --hour 20 --minute 30` — install the macOS LaunchAgent for daily pre-market runs.
- `us-screener mcp` — serve the FastMCP server (tools: `us_screen`, `us_report_latest`, `us_security_detail`, `us_generate_opinion`).

## Notes
- China-concept names are tagged in `company_tags` (risk/china_concept) and **dropped** before scoring (not merely penalized).
- Free data sources are flaky; the pipeline records per-step failures and keeps going.
- Report artifacts default to `reports/us-premarket/`; LLM key is read from env/config and never printed.
- Decisions: `core_candidate` / `watchlist` / `reserve` / `reject` — research candidates only, not investment advice.
