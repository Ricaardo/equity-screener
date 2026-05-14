# A/H Stock Screener

本项目是一个面向个人投资者的 A 股 + 港股免费数据筛选工具。第一版以 AKShare 为主要结构化数据入口，使用 DuckDB 做本地缓存，提供 CLI 和 Streamlit 看板。

完整技术方案见 [docs/technical-solution.md](docs/technical-solution.md)。

## 功能

- 同步 A 股、港股和 A 股 ETF 市场快照。
- 建立统一证券主数据表，并细分主板、创业板、科创板、北交所、港股通、ST/退市风险和 ETF。
- 同步 A 股行业和概念标签。
- 支持内置策展主题标签和自建 CSV 标签导入，补足港股免费概念数据不足。
- 基于估值、流动性、主题和风险做可解释评分。
- 接入财报三表、ROE、现金流、负债率等完整基本面字段。
- 基本面分纳入多期收入/利润 CAGR、ROE 均值、稳定性、研发费用率和资本开支效率。
- 专家模型加入行业/同类分位数和行业化基本面阈值，降低跨行业直接比较的偏差。
- 内置欧美和中国投资大师框架，面向 A 股和港股做专家筛选。
- 按主题和相似标的去重提炼，每个方向只保留最好几个候选。
- 对 ETF 做分类、工具评分和观察建议。
- 提供全市场覆盖率、候选变化和带成本/滑点/行业分散约束的等权回测命令。
- 输出候选池、观察池、剔除池和提炼候选池。
- 提供本地 Streamlit 研究台，支持按市场、类型、板块、港股通和 ST 状态筛选。
- 生成 Markdown 研究报告。
- 支持一键全量刷新和 macOS 定时更新。

## 安装

```bash
/Users/bilibili/.local/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e .
```

如果需要 Streamlit 看板：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[ui]"
```

## 快速开始

```bash
ah-screener init-db
ah-screener sync-spot --market all
ah-screener classify-securities
ah-screener sync-a-tags --kind industry --limit 30
ah-screener sync-a-tags --kind concept --limit 50
ah-screener sync-curated-tags
ah-screener score
ah-screener export --top 100
streamlit run src/ah_screener/ui/streamlit_app.py
```

只刷新 ETF：

```bash
ah-screener sync-spot --market ETF
```

## 专家筛选

系统内置 `china_masters_fundamental_theme_technical_v2`，综合中国投资大师框架、完整基本面、热门主题和技术指标，不需要用户手动设定评分。

```bash
ah-screener sync-history --market all --top 120 --lookback-days 430
ah-screener technical
ah-screener sync-fundamentals --market all --top 120
ah-screener import-tags --path data/custom_tags.csv
ah-screener fundamentals-status --top 120
ah-screener coverage-status
ah-screener expert-score
ah-screener expert-export --top 50
ah-screener refined-export --top 50
ah-screener candidate-changes
ah-screener etf-export --top 50
ah-screener sync-benchmarks --lookback-days 430
ah-screener backtest --rebalance quarterly --industry-neutral --fee-bps 5 --slippage-bps 10 --benchmark A:000300
```

结果会落库到：

```text
hot_theme_definitions
technical_indicators
financial_statement_items
financial_metrics
expert_screening_results
refined_candidates
```

`refined_candidates` 会按主题桶、风格桶和 A/H 同主体去重：同一主题默认最多 3 只，同一风格优先最多 2 只，A/H 两地上市或同名主体只保留专家分最高的一只。

`coverage-status` 会按市场、资产类型和板块展示全市场覆盖率，包括技术指标、基本面和专家评分覆盖。`etf-export` 会对 A 股场内 ETF 做宽基、行业、主题、跨境、债券、商品和货币分类，并按流动性、规模和动量给出工具型评分。`candidate-changes` 和 `backtest` 会在积累多日快照后输出候选变化和等权回测，回测支持 snapshot/monthly/quarterly 调仓、手续费、滑点、行业分散约束和 A/H 免费指数基准对比。

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

最小测试套件覆盖基准回测、同类去重、ETF 分类和基本面评分边界：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
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
