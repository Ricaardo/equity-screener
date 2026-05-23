# A/H/US Stock Screener

本项目是一个面向个人投资者的 A 股 + 港股 + 美股免费数据筛选工具。第一版以 AKShare、Nasdaq Trader、SEC EDGAR 等免费/公开数据入口为主，使用 DuckDB 做本地缓存，提供 CLI 和 Streamlit 看板。

完整技术方案见 [docs/technical-solution.md](docs/technical-solution.md)。

## 功能

- 同步 A 股、港股、美股和 A 股 ETF 市场快照。
- 建立统一证券主数据表，并细分主板、创业板、科创板、北交所、港股通、美股交易所、ST/退市风险和 ETF。
- 同步 A 股行业和概念标签，支持可编辑 CSV 细分行业映射。
- 支持内置策展主题标签、自建 CSV 标签、HKEXnews 自动公告下载和官方 PDF/公告解析标签导入，补足港股免费概念数据不足。
- 基于估值、流动性、主题和风险做可解释评分。
- 接入财报三表、ROE、现金流、负债率等完整基本面字段。
- 基本面分纳入多期收入/利润 CAGR、ROE 均值、稳定性、研发费用率和资本开支效率。
- 专家模型加入细分行业、行业/同类估值分位数和行业化基本面阈值，降低跨行业直接比较的偏差。
- 内置欧美和中国投资大师框架，面向 A 股、港股和美股做专家筛选。
- 按主题、相似标的和 A/H/US 同主体去重提炼，每个方向只保留最好几个候选。
- 对 ETF 做分类、工具评分和观察建议。
- 提供全市场覆盖率、候选变化和带成本/滑点/行业分散约束的等权回测命令。
- 区分自然生成候选快照和历史回放快照，严格点时回测可用 `--natural-only` 排除回放数据。
- 输出候选池、观察池、剔除池和提炼候选池。
- 提供本地 Streamlit 研究台，支持按市场、类型、板块、港股通和 ST 状态筛选。
- 生成 Markdown 研究报告。
- 支持一键全量刷新和 macOS 定时更新。

## 安装

推荐使用 `uv` 管理本地虚拟环境和依赖：

```bash
uv sync
```

如果需要 Streamlit 看板：

```bash
uv sync --extra ui
```

如果要做本地开发：

```bash
make install-dev
```

常用命令统一放在 `Makefile`：

```bash
make format        # 格式化 src/ 和 tests/
make format-check  # 检查是否需要格式化
make lint          # 静态检查
make typecheck     # Python 语法/字节码检查
make test          # 运行 unittest 测试
make validate      # 提交前核心本地检查
make hooks         # 安装本仓库 pre-commit hook
make pre-commit    # 对全仓库手动跑 hook
```

## 快速开始

```bash
uv run ah-screener init-db
uv run ah-screener sync-spot --market all
uv run ah-screener sync-us-spot --symbols AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,BABA,SPY,QQQ
uv run ah-screener sync-us-batch --offset 0 --limit 100 --stocks-only
uv run ah-screener classify-securities
uv run ah-screener sync-a-tags --kind industry --limit 30
uv run ah-screener sync-a-tags --kind concept --limit 50
uv run ah-screener sync-curated-tags
uv run ah-screener sync-identity-mappings
uv run ah-screener score
uv run ah-screener export --top 100
uv run --extra ui streamlit run src/ah_screener/ui/streamlit_app.py
```

只刷新 A 股和港股 ETF：

```bash
uv run ah-screener sync-spot --market ETF
```

## 专家筛选

系统内置 `china_masters_fundamental_theme_technical_v2`，综合中国投资大师框架、完整基本面、热门主题和技术指标，不需要用户手动设定评分。

```bash
ah-screener sync-history --market all --top 120 --lookback-days 430
ah-screener technical
ah-screener sync-fundamentals --market all --top 120
ah-screener import-tags --path data/custom_tags.csv
ah-screener import-industry-map --path data/industry_mapping.csv
ah-screener ingest-document --market HK --symbol 00700 --path /path/to/annual-report.pdf --source hkexnews_pdf
ah-screener sync-hkex-documents --symbol 00700 --limit 5
ah-screener fundamentals-status --top 120
ah-screener coverage-status
ah-screener expert-score
ah-screener industry-valuation-stats
ah-screener expert-export --top 50
ah-screener refined-export --top 50
ah-screener candidate-changes
uv run ah-screener etf-export --top 50
uv run ah-screener etf-export --market HK --top 30
uv run ah-screener etf-export --raw --top 100
ah-screener sync-benchmarks --lookback-days 430
ah-screener backfill-refined-snapshots --min-snapshots 6 --rebalance quarterly
ah-screener backtest --rebalance quarterly --industry-neutral --fee-bps 5 --slippage-bps 10 --benchmark A:000300
ah-screener backtest --rebalance quarterly --natural-only
```

结果会落库到：

```text
hot_theme_definitions
technical_indicators
financial_statement_items
financial_metrics
expert_screening_results
industry_valuation_stats
company_identity_mappings
company_documents
document_extractions
refined_candidates
```

`refined_candidates` 会按主题桶、风格桶和 A/H/US 同主体去重：同一主题默认最多 3 只，同一风格优先最多 2 只，A/H/US 多地上市或同名主体只保留专家分最高的一只。

`coverage-status` 会按市场、资产类型和板块展示全市场覆盖率，包括技术指标、基本面和专家评分覆盖。`etf-export` 会对 A 股和港股 ETF 做宽基、行业、主题、跨境、债券、商品和货币分类，并按流动性、规模和动量给出工具型评分；默认按同指数或同赛道合并，只输出每组最优候选，使用 `--raw` 可查看未合并明细。`candidate-changes` 和 `backtest` 会在积累多日快照后输出候选变化和等权回测，回测支持 snapshot/monthly/quarterly 调仓、手续费、滑点、行业分散约束和 A/H/US 免费基准对比。只有一个真实候选快照时，可先用 `backfill-refined-snapshots` 基于已存真实日线生成历史回放候选快照；回放快照会写入 `snapshot_source = historical_replay` 和 `is_replay = true`，严格点时回测使用 `--natural-only` 排除。

## 免费数据源

- A 股/港股行情、板块、财务：AKShare 免费接口。
- 美股证券目录：Nasdaq Trader symbol directory。
- 美股历史行情：AKShare `stock_us_daily`，Stooq CSV 作为可选备用源，使用 `STOOQ_API_KEY` 或 `AH_SCREENER_STOOQ_API_KEY`。
- 美股基本面：SEC EDGAR Company Facts。
- 美股批量扩展：`sync-us-batch` 按 Nasdaq Trader 全量列表分页同步，免费源限流时调小 `--limit`。
- 港股主题增强：`sync-hkex-documents` 自动搜索 HKEXnews、下载 PDF 并抽取业务结构、研发投入、客户集中度、审计意见、风险提示、股本动作、延迟刊发财报和主题标签；本地 PDF 仍可用 `ingest-document` 导入。

## 报告

基于当前 DuckDB 生成 Markdown 研究报告：

```bash
ah-screener report
```

默认输出到：

```text
reports/ah-screening-report-YYYY-MM-DD.md
```

后续事项统一记录在 [docs/todo.md](docs/todo.md)。

## 全量更新和定时任务

一键刷新行情、标签、历史价格、技术指标、三表基本面、专家评分和报告：

```bash
ah-screener update-all --top 120 --lookback-days 430
```

在 macOS 上安装每日定时任务，默认每天 18:30 运行：

```bash
ah-screener install-schedule --hour 18 --minute 30
```

定时任务会生成：

```text
scripts/update_all.sh
~/Library/LaunchAgents/com.ah-screener.update.plist
logs/scheduled-update.out.log
logs/scheduled-update.err.log
```

生成的 `scripts/update_all.sh` 内置 `.update.lock` 互斥锁。如果上一轮更新仍在运行，下一轮会跳过，避免 DuckDB 写锁冲突和重复拉取免费接口。

卸载定时任务：

```bash
ah-screener uninstall-schedule
```

## 本地测试

个人开发时优先跑统一验证入口：

```bash
make validate
```

最小测试套件覆盖基准回测、同类去重、ETF 分类和基本面评分边界；只跑测试时使用 `make test`。

UI 截图冒烟检查需要本机安装 `browser-use` CLI，截图默认输出到 `reports/ui-screenshots/`：

```bash
scripts/check_ui_screenshots.sh http://localhost:8501
```

## 数据位置

默认数据库：

```text
data/ah_screener.duckdb
```

数据库文件超过 GitHub 普通文件 100MB 限制，仓库中默认不直接提交 `*.duckdb`。需要共享完整本地库时，将 `data/ah_screener.duckdb` 作为 GitHub Release 附件上传和下载。

上传当前数据库到 GitHub Release：

```bash
scripts/upload_release_db.sh data-YYYY-MM-DD
```

可以通过环境变量覆盖：

```bash
export AH_SCREENER_DB=/path/to/ah_screener.duckdb
```

## 注意

本工具只用于研究和筛选，不构成投资建议。免费数据源可能存在延迟、字段变化或接口不稳定，重要结论应回到交易所、巨潮资讯网、HKEXnews 等官方披露源核验。
