# A 股 + 港股 + 美股免费数据筛选系统技术方案

## 1. 目标

面向个人投资者，建立一个本地运行、免费数据源优先、可解释、可扩展的股票筛选系统。

当前阶段覆盖：

- A 股：沪深北上市公司。
- 港股：港交所上市普通股。
- 美股：以 Nasdaq Trader、Futu/OpenD、AKShare、SEC EDGAR 和 Alpha Vantage delisted CSV（可选 key）为免费/公开源。
- ETF：已接入 A 股、港股和美股 ETF。
- 数据源：本地 OpenD 优先，免费/公开源回退，可脚本化、可本地缓存。
- 输出：剔除名单、高风险名单、行业候选池、概念候选池、专家候选、同类去重精选、Markdown/JSON 报告和本地 React 看板。

已增强能力：

- 年报文本解析：从官方 PDF 或文本中抽取主营业务、风险、审计意见和主题标签。
- 回测：支持真实日线上的多期历史回放候选快照。

## 2. 设计原则

- 不做黑箱选股，只做可解释筛选。
- 先排雷，再排序，最后人工研究。
- 所有外部数据先落本地库，避免重复抓取和接口波动。
- 每个标签和评分都保留来源，方便追溯。
- A 股、港股和美股使用统一数据模型。
- 港股免费概念数据弱，因此行业和主题标签必须支持自建、覆盖和证据等级。

## 3. 免费数据源

### A 股

| 数据类型 | 首选数据源 | 用途 |
| --- | --- | --- |
| 股票池 | Futu/OpenD 优先；北交所由 AKShare 补齐 | 建立 securities 主表 |
| ETF | Futu/OpenD 优先，AKShare 回退 | 建立 ETF 池、成交额和主题替代工具 |
| 实时快照/行情 | Futu/OpenD 优先，AKShare 回退 | 估值、成交额、流动性、过滤 |
| 历史行情 | Futu/OpenD 优先，AKShare 回退 | 趋势、相对强弱、回测 |
| 行业板块 | Futu/OpenD `get_plate_list/get_plate_stock` 优先，AKShare 回退 | 行业内分位数 |
| 概念板块 | Futu/OpenD `get_plate_list/get_plate_stock` 优先，AKShare 回退 | 主题候选池 |
| 财报/公告 | 巨潮资讯网、交易所官网、中国资本市场信息披露平台 | 官方校验、后续文本解析 |
| 三表与财务指标 | AKShare 东方财富财务接口 | 利润表、资产负债表、现金流量表、ROE、利润率、负债率、现金流质量 |

### 港股

| 数据类型 | 首选数据源 | 用途 |
| --- | --- | --- |
| 证券列表 | Futu/OpenD 优先，HKEX/AKShare 回退 | 港股股票池 |
| 港股通 | Futu/OpenD `HK.GangGuTong` 优先，AKShare 港股通成份股回退 | 标记港股通可投资范围 |
| 实时快照/行情 | Futu/OpenD 优先，AKShare 回退 | 估值、成交额、流动性 |
| 历史行情 | Futu/OpenD 优先、AKShare 回退 | 趋势、回测 |
| 公告/年报 | HKEXnews 披露易 | 官方校验、后续文本解析 |
| 行业/主题 | AKShare、自建标签、HKEXnews 文档抽取 | 初始分类和人工增强 |
| 三表与财务指标 | AKShare 东方财富港股财务接口 | 利润表、资产负债表、现金流量表、ROE、利润率、负债率、现金流质量 |

### 美股

| 数据类型 | 首选数据源 | 用途 |
| --- | --- | --- |
| 证券列表 | Futu/OpenD 优先，Nasdaq Trader symbol directory 回退 | 建立 `market = US` 主表、交易所和 ETF 标识 |
| 历史行情/快照 | Futu/OpenD 优先，AKShare `stock_us_daily` 回退 | 趋势、回测、最新收盘快照 |
| 基本面 | SEC EDGAR Company Facts | 美元收入、利润、资产负债、现金流、研发和资本开支 |
| 同主体映射 | 内置策展映射表 | A/H/US ADR 或多地上市主体去重 |
| 摘牌生命周期 | Alpha Vantage `LISTING_STATUS state=delisted`（需 key） | US 幸存者偏差审计 |

## 4. 数据模型

核心表：

```text
securities
- market: A / HK / US
- symbol
- name
- asset_type: stock / etf
- board: 主板 / 创业板 / 科创板 / 北交所 / 港股通 / 非港股通 / ETF
- exchange
- currency
- status
- is_st
- is_hk_connect
- updated_at

market_snapshots
- market
- symbol
- asset_type
- board
- trade_date
- last_price
- pct_change
- volume
- amount
- turnover_rate
- pe_ttm
- pb
- market_cap
- source
- updated_at

daily_prices
- market
- symbol
- trade_date
- open
- high
- low
- close
- volume
- amount
- adj_type
- source

company_tags
- market
- symbol
- tag_type: industry / concept / theme / risk
- tag_name
- evidence_level: A / B / C
- source
- updated_at

company_identity_mappings
- canonical_id
- market
- symbol
- listing_type
- source
- confidence
- updated_at

company_documents
- document_id
- market
- symbol
- document_type
- report_date
- title
- source_url
- local_path
- file_sha256
- source
- updated_at

document_extractions
- document_id
- market
- symbol
- extract_type
- extract_key
- extract_value
- evidence_text
- evidence_level
- source
- updated_at

screening_scores
- snapshot_date
- market
- symbol
- quality_score
- growth_score
- valuation_score
- liquidity_score
- risk_score
- theme_score
- total_score
- decision: keep / watch / reject
- reasons
- updated_at

hot_theme_definitions
- snapshot_date
- theme_name
- market
- weight
- keywords
- rationale
- source
- updated_at

technical_indicators
- snapshot_date
- market
- symbol
- close
- ma20
- ma60
- ma120
- return_20d
- return_60d
- pct_from_120d_high
- rsi14
- technical_score
- technical_signal
- updated_at

financial_statement_items
- market
- symbol
- statement_type: income / balance / cashflow
- report_date
- report_type
- item_code
- item_name
- amount
- currency
- source
- updated_at

financial_metrics
- snapshot_date
- market
- symbol
- report_date
- revenue
- revenue_yoy
- gross_profit
- parent_net_profit
- net_profit_yoy
- deducted_net_profit
- operating_cashflow
- total_assets
- total_liabilities
- total_equity
- roe
- roa
- gross_margin
- net_margin
- debt_asset_ratio
- current_ratio
- cashflow_to_profit
- ocf_to_revenue
- rd_expense
- rd_expense_ratio
- capex
- capex_to_revenue
- capex_to_operating_cashflow
- innovation_efficiency_score
- revenue_cagr_3y
- net_profit_cagr_3y
- roe_avg_3y
- roe_stability_score
- margin_stability_score
- fundamental_trend_score
- quality_score
- growth_score
- balance_score
- cashflow_score
- fundamental_score
- warnings
- updated_at

expert_screening_results
- snapshot_date
- strategy
- market
- symbol
- name
- canonical_id
- expert_score
- master_score
- china_master_score
- fundamental_score
- detailed_industry
- industry_peer_group
- peer_score
- industry_fit_score
- valuation_percentile
- theme_score
- technical_score
- liquidity_score
- valuation_score
- risk_score
- decision
- theme_matches
- reasons
- updated_at

refined_candidates
- snapshot_date
- strategy
- bucket
- rank_in_bucket
- peer_group
- style_bucket
- market
- symbol
- name
- canonical_id
- expert_score
- fundamental_score
- technical_score
- detailed_industry
- industry_peer_group
- peer_score
- industry_fit_score
- valuation_percentile
- theme_matches
- selection_note
- reasons
- updated_at
```

## 5. 筛选框架

### 5.1 硬过滤

A 股剔除规则：

- 股票名称包含 ST、*ST、退。
- 最新价、成交额、市值缺失。
- 成交额低于最低流动性阈值。
- PE、PB 明显异常且缺少成长或主题支撑。

港股剔除规则：

- 成交额极低。
- 市值过小。
- 长期停牌或关键行情字段缺失。
- HKEXnews 公告解析命中的频繁合股/供股/配股、延迟刊发财报、审计意见异常等风险标签。

### 5.2 综合评分

第一版使用可解释规则，不引入机器学习：

```text
total_score =
  30% valuation_score
+ 25% liquidity_score
+ 20% theme_score
+ 15% quality_score
+ 10% growth_score
- risk_penalty
```

基础评分用于快速排雷和排序；专家评分会进一步接入三表、ROE、毛利率、现金流、收入增速、利润增速、资产负债率等指标。

### 5.3 主题标签

主题标签采用多来源和证据等级：

- A 级：主营收入或分部收入明确支持。
- B 级：年报、公告、业务描述多次出现。
- C 级：来自概念板块或市场标签，未验证收入占比。

第一版先导入 A 股概念板块作为 C 级标签；港股先保留自建入口。

### 5.4 专家筛选框架

系统内置 `china_masters_fundamental_theme_technical_v2`，目标是由系统自动完成第一轮筛选，而不是让个人投资者自己手工设评分。

核心输入：

- 基本面：ROE、收入增速、利润增速、毛利率、净利率、资产负债率、流动比率、经营现金流/净利润、经营现金流/收入。
- 行业和概念：A 股行业板块、概念板块、港股自建主题关键词和重点公司覆盖。
- 时代主题：AI 算力硬件、半导体国产替代、人形机器人与高端制造、创新药与医疗科技、高股息央国企防御、电力储能与能源转型、资源涨价与安全资产、港股 AI 互联网平台、汽车智能化与出海。
- 技术面：MA20/MA60/MA120、20 日和 60 日收益、距离 120 日高点、RSI、20 日波动率。
- 风险项：ST/退市风险、流动性不足、过热追高、财报预警、缺少关键数据等。

大师框架分两层：

- 通用大师：格雷厄姆估值安全边际、巴菲特质量和风险、费雪成长、彼得林奇 GARP、欧奈尔动量。
- 中国和港 A 适配：张磊长期主义和产业趋势、邱国鹭低估值高质量、但斌/林园长坡厚雪和高 ROE、邓晓峰产业周期和资源制造、陈光明质量估值风险平衡。

最终专家分：

```text
expert_score =
  20% 通用大师框架
+ 28% 中国大师框架
+ 18% 基本面
+ 18% 时代主题
+ 12% 技术面
+  4% 流动性
- 风险扣分
```

最新版本额外引入行业/同类分位数作为 6% 左右的校正项，并相应下调其他权重。分位数在同一市场和行业/板块内比较基本面、估值、技术面和流动性，避免银行、医药、科技、资源等行业直接横向比较造成偏差。

### 5.5 同类去重提炼

专家筛选后再做二次提炼，避免同一类标的挤满候选列表。

- 先按主题进入 bucket，例如 AI 算力硬件、半导体国产替代、港股 AI 互联网平台、高股息资源防御。
- 再按风格进入 style_bucket，例如科技成长、科技成长偏估值、红利防御、资源周期、医药成长、智能汽车、能源转型。
- 同时计算 detailed_industry、industry_peer_group、peer_score、valuation_percentile 和 industry_fit_score，用于同主题内排序和人工复核。
- A/H/US 多地上市或同名主体进入同一个 peer_group，只保留专家分最高的一只。
- 每个主题默认最多保留 3 只；同一风格优先最多保留 2 只，不足时按总分补齐。
- 提炼结果落库到 `refined_candidates`，保留 `selection_note` 说明为什么入选。

## 6. 项目结构

```text
ah-stock-screener/
  docs/technical-solution.md
  data/
  src/ah_screener/
    cli.py
    config.py
    expert_model.py
    fundamentals.py
    pipeline.py
    reporting.py
    scheduler.py
    scoring.py
    storage.py
    technical.py
    sources/akshare_client.py
    ui/streamlit_app.py
  frontend/
    src/App.tsx
    vite.config.ts
  reports/
  pyproject.toml
  README.md
```

## 7. 执行路线

第一步：建本地库。

```bash
ah-screener init-db
```

第二步：同步 A 股、港股和美股快照。

```bash
ah-screener sync-spot --market all
ah-screener classify-securities
```

`sync-spot --market all` 会同步 A 股股票、港股股票、美股默认池和 A 股 ETF；`classify-securities` 会回填主板、创业板、科创板、北交所、港股通、美股交易所、ST/退市风险和 ETF 类型。

第三步：同步 A 股行业/概念标签，并写入内置策展主题标签。

```bash
ah-screener sync-a-tags --kind industry --limit 50
ah-screener sync-a-tags --kind concept --limit 100
ah-screener sync-curated-tags
ah-screener sync-identity-mappings
```

如果需要自建标签，可复制 `data/custom_tags.example.csv` 为 `data/custom_tags.csv`，然后运行：

```bash
ah-screener import-tags --path data/custom_tags.csv
```

如果需要治理细分行业口径，可复制 `data/industry_mapping.example.csv` 为 `data/industry_mapping.csv`，然后运行：

```bash
ah-screener import-industry-map --path data/industry_mapping.csv
```

第四步：同步退市/摘牌生命周期。

```bash
ah-screener sync-delisted-universe
```

第五步：同步历史行情、技术指标和三表基本面。

```bash
ah-screener sync-history --market all --top 120 --lookback-days 430
ah-screener technical
ah-screener sync-fundamentals --market all --top 120
ah-screener fundamentals-status --top 120
ah-screener coverage-status
```

第六步：运行专家筛选和同类提炼。

```bash
ah-screener expert-score
ah-screener expert-export --top 50
ah-screener refined-export --top 50
ah-screener candidate-changes
ah-screener etf-export --top 50
ah-screener sync-benchmarks --lookback-days 430
ah-screener backtest --rebalance quarterly --industry-neutral --fee-bps 5 --slippage-bps 10 --benchmark A:000300
ah-screener backtest --include-replay --rebalance quarterly
```

当只有一个自然候选快照时，可先用 `backfill-refined-snapshots` 生成历史回放候选；回放快照会写入 `snapshot_source = historical_replay` 和 `is_replay = true`。当前严格口径的 `backtest` 默认排除 replay；只有诊断历史回放时才显式传 `--include-replay`。

港股公告可自动从 HKEXnews 搜索下载，并进入同一套 PDF 解析和风险标签流程：

```bash
ah-screener sync-hkex-documents --symbol 00700 --limit 5
```

美股可按 Nasdaq Trader 全量列表分页扩展覆盖：

```bash
ah-screener sync-us-batch --offset 0 --limit 100 --stocks-only
```

第七步：生成报告并打开 React 看板。

```bash
ah-screener report
cd frontend
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。前端只读 `reports/ah-screening-report-latest.json` 和 `reports/ah-screening-appendix-latest.md`。

第八步：如需只生成当前研究报告。

```bash
ah-screener report
```

报告默认输出：

```text
reports/ah-screening-report-YYYY-MM-DD.md
```

第九步：一键全量刷新。

```bash
ah-screener update-all --top 120 --lookback-days 430
```

第十步：安装每日自动更新。

```bash
ah-screener install-schedule --hour 18 --minute 30
```

该命令会生成 `scripts/update_all.sh`，并注册 macOS LaunchAgent：

```text
~/Library/LaunchAgents/com.ah-screener.update.plist
```

默认每天本地时间 18:30 运行全量刷新，并把日志写到 `logs/`。
生成的 `scripts/update_all.sh` 使用 `.update.lock` 做互斥保护；如果上一轮更新没有结束，下一轮会跳过，避免 DuckDB 写锁冲突。

## 8. 增强能力完成状态

- 严格点时回测：`refined_candidates` 已记录 `snapshot_source` 和 `is_replay`，默认 `backtest` 只使用自然生成快照；诊断回放才传 `--include-replay`。
- 港股公告自动下载：`sync-hkex-documents` 已接入 HKEXnews 官方搜索端点，自动下载 PDF 并复用 `ingest-document` 解析。
- 行业口径治理：`import-industry-map` 已支持可编辑 CSV，把细分行业映射写入 `company_tags`。
- 美股覆盖扩展：`sync-us-batch` 已支持按证券目录分页同步；OpenD 不可用时回退 Nasdaq Trader，遇到免费源限流可调小批次。
- 风险规则扩展：PDF/公告解析已识别频繁合股/供股/配股、延迟刊发财报和异常审计意见，并纳入专家模型风险扣分。

## 9. 看板和自动化

本地看板主入口使用 React + Vite 实现，定位为“每日研究摘要”而不是交易终端。看板只读报告 JSON 和附录 Markdown，不直接查询 DuckDB，默认展示短摘要，完整长表进入附录。Streamlit 入口保留为备用查看器。

看板分为五个主要视图：

- 今日摘要：展示当前结论、数据新鲜度、今日新增和大幅变化。
- 优先研究：展示同类去重后的股票候选卡，卡片包含入选理由、主要风险、买前核验和证据链展开。
- ETF工具箱：按用途展示 ETF，而不是默认宽表；用途包括核心配置、主题进攻、防御与现金、跨境与 T+0、商品资源，并把完整宽表收进展开明细。
- 潜力情景：展示潜力候选的触发、目标、止损、RR 和证伪条件。
- 证据附录：展示候选变化、完整覆盖率、长表和偏差说明。

自动化链路由 `ah-screener update-all` 串起：

- 同步 A 股、港股和美股行情快照。
- 更新 A 股行业和概念标签。
- 计算基础评分。
- 同步历史行情并计算技术指标。
- 同步三表财务数据并生成标准化基本面指标。
- 执行专家筛选和同类去重提炼。
- 生成当日 Markdown 研究报告。
