# 筛选报告 JSON 产物契约（report schema）

本项目的主产物之一是**给 AI / 程序消费的结构化筛选报告**。每次生成报告时产出：

```text
reports/ah-screening-report-YYYY-MM-DD.md       # 给人读的每日短摘要
reports/ah-screening-appendix-YYYY-MM-DD.md     # 长表、覆盖率和完整证据附录
reports/ah-screening-report-YYYY-MM-DD.json     # 给程序/AI 读的结构化报告
reports/ah-screening-report-latest.json         # 固定指针，始终指向最新
reports/ah-screening-report-latest.md           # 固定指针，每日短摘要
reports/ah-screening-appendix-latest.md         # 固定指针，完整附录
```

AI 消费方应固定读取 `reports/ah-screening-report-latest.json`。本文档是该 JSON 的字段契约。

- 生成入口：`ah_screener.reporting.generate_report()`（写文件）/ `build_report_payload()`（返回 dict，不写文件）。
- 写出前会经过 `validate_report_payload()` 校验，schema 漂移会在生成时直接报错。
- 兼容性以 `schema_version` 标记；**新增字段不升主版本，删除/改名/改义升版本**。当前 `1.2`。

---

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | 契约版本，如 `"1.2"`。 |
| `report_type` | string | 固定 `"ah-screening"`。 |
| `generated_at` | string | ISO 时间戳（本地时区），如 `2026-05-24T18:30:00`。**唯一每次必变的字段。** |
| `report_date` | string | `YYYY-MM-DD`。 |
| `strategy` | string | 策略标识，如 `china_masters_fundamental_theme_technical_v2`。 |
| `database` | string | 生成所用 DuckDB 路径。 |
| `disclaimer` | string | 免责声明（仅研究、非投资建议）。 |
| `markdown_report` | string | 每日短摘要 Markdown 文件名。 |
| `appendix_report` | string | 完整附录 Markdown 文件名。 |
| `conclusion` | string[] | 当前结论（数据驱动的定性判断）。 |
| `bias_notes` | string[] | 证据口径与偏差控制说明（回测口径、RS 阈值、幸存者偏差）。 |
| `external_context` | object[] | 宏观/产业背景，`{name,url,note}`；**不计入任何评分**。 |
| `data_freshness` | object[] | 各市场最新快照日，`{market, latest_date}`。 |
| `data_freshness_warning` | string \| null | 各市场快照日期分叉的警告（无则 null）。 |
| `coverage_counts` | object | 各数据维度的行数（证券快照/技术指标/基本面/专家评分/提炼候选/退市生命周期）。 |
| `decision_distribution` | object[] | 专家决策分布，`{decision, count}`，decision ∈ core_candidate/watchlist/reserve/reject。 |
| `counts` | object | 计数汇总，见下。 |
| `daily_brief` | object | UI 优先消费的每日摘要，含优先候选、ETF 用途分组、潜力情景和数据健康。 |
| `top_actions` | object[] | 今日动作摘要，优先展示新增候选和大幅分数变化。 |
| `etf_use_cases` | object[] | ETF 按使用场景分组后的工具箱视图，见下。 |
| `refined_candidates` | object[] | 去重提炼候选（核心筛选结果），见下。 |
| `core_candidates` | object[] | 专家核心候选（decision=core_candidate，最多 20），见下。 |
| `potential_candidates` | object[] | 潜力扫描候选，见下。 |
| `etf_leaders` | object[] | ETF 去重精选，见下。 |
| `candidate_changes` | object[] | 与上一快照相比的候选变化（新增/移出/分数变化）。 |

### `counts`
| 键 | 类型 | 说明 |
|---|---|---|
| `refined_candidates` / `core_candidates` / `potential_candidates` / `etf_leaders` | int | 各列表条数。 |
| `refined_by_market` | object | 提炼候选按市场计数，如 `{"A":14,"HK":14}`。 |

---

## 候选记录字段

所有候选记录的 `market` ∈ `A` / `HK` / `US`；`trading_system` ∈ `T+0` / `T+1`：
**美股、港股 = T+0；A 股个股 = T+1；A 股 ETF 按类别（跨境/债券/商品/货币 = T+0，宽基/行业/主题 = T+1）。**

### `refined_candidates`（必含 `market,trading_system,symbol,name,expert_score,bucket`）
`bucket`(主题桶) · `rank_in_bucket` · `style_bucket`(风格) · `market` · `trading_system` · `symbol` · `name` · `expert_score` · `fundamental_score` · `technical_score` · `detailed_industry` · `industry_peer_group` · `peer_score` · `industry_fit_score` · `valuation_percentile` · `theme_matches`(string[]) · `reasons`(string[]，证据链) · `selection_note` · `why_selected`(string[]) · `key_risks`(string[]) · `verify_before_action`(string[]) · `invalid_if`

### `core_candidates`（必含 `market,trading_system,symbol,name,expert_score,decision`）
`market` · `trading_system` · `symbol` · `name` · `expert_score` · `master_score` · `china_master_score` · `fundamental_score` · `technical_score` · `detailed_industry` · `industry_peer_group` · `peer_score` · `industry_fit_score` · `valuation_percentile` · `decision` · `theme_matches`(string[]) · `reasons`(string[]) · `why_selected`(string[]) · `key_risks`(string[]) · `verify_before_action`(string[]) · `invalid_if`

### `potential_candidates`（必含 `market,trading_system,symbol,name,potential_score`）
`market` · `trading_system` · `symbol` · `name` · `potential_score` · `technical_setup_score` · `relative_strength_score` · `fundamental_turn_score` · `theme_early_score` · `pivot_price` · `target_price` · `stop_price` · `rr_ratio` · `time_stop_days` · `hist_win_rate` · `bias_note` · `setup_note` · `scenario` · `invalid_if`

> 口径：price-only；触发/目标/止损为情景参考；RS 阈值是运行参数而非 edge 证明。

### `etf_leaders`（必含 `market,trading_system,symbol,name`）
`market` · `trading_system` · `symbol` · `name` · `etf_category`(分类) · `etf_cluster`(簇) · `etf_track`(跟踪) · `etf_score` · `etf_recommendation` · `peer_count` · `peer_alternatives` · `pct_change` · `amount` · `use_case` · `why_selected`(string[]) · `alternatives`(string[]) · `caution`

### `etf_use_cases`
`key` · `title` · `description` · `leaders`(object[]) · `count`

当前用途分组：`核心配置`、`主题进攻`、`防御与现金`、`跨境与T+0`、`商品资源`、`其他工具`。每组的 `leaders` 引用 `etf_leaders` 同结构记录。

### `candidate_changes`
`change`(新增/移出/保留) · `bucket` · `market` · `symbol` · `name` · `latest_score` · `previous_score` · `score_delta`

---

## 给 AI 消费方的注意

- **数值含义**：所有 `*_score` 为 0–100；缺失值为 `null`（不会出现 `NaN`/`Infinity`，JSON 严格可解析）。
- **证据链**：`reasons` 是模型对该候选打分/决策的可读理由列表，可直接作为分析依据。
- **不要把分数当 edge**：报告是研究筛选辅助，回测/前瞻验证样本不足时不构成有效性证明（见 `bias_notes`）。
- **缺字段即契约破坏**：消费方可假设上表"必含"字段一定存在；其余字段可能因数据缺失而省略。
