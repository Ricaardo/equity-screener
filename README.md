# A/H Stock Screener

本项目是一个面向个人投资者的 A 股 + 港股免费数据筛选工具。第一版以 AKShare 为主要结构化数据入口，使用 DuckDB 做本地缓存，提供 CLI 和 Streamlit 看板。

完整技术方案见 [docs/technical-solution.md](docs/technical-solution.md)。

## 功能

- 同步 A 股和港股市场快照。
- 建立统一证券主数据表。
- 同步 A 股行业和概念标签。
- 基于估值、流动性、主题和风险做可解释评分。
- 接入财报三表、ROE、现金流、负债率等完整基本面字段。
- 内置欧美和中国投资大师框架，面向 A 股和港股做专家筛选。
- 按主题和相似标的去重提炼，每个方向只保留最好几个候选。
- 输出候选池、观察池、剔除池和提炼候选池。
- 提供本地 Streamlit 看板。

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
ah-screener sync-a-tags --kind industry --limit 30
ah-screener sync-a-tags --kind concept --limit 50
ah-screener score
ah-screener export --top 100
streamlit run src/ah_screener/ui/streamlit_app.py
```

## 专家筛选

系统内置 `china_masters_fundamental_theme_technical_v2`，综合中国投资大师框架、完整基本面、热门主题和技术指标，不需要用户手动设定评分。

```bash
ah-screener sync-history --market all --top 120 --lookback-days 430
ah-screener technical
ah-screener sync-fundamentals --market all --top 120
ah-screener fundamentals-status --top 120
ah-screener expert-score
ah-screener expert-export --top 50
ah-screener refined-export --top 50
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

## 数据位置

默认数据库：

```text
data/ah_screener.duckdb
```

数据库文件超过 GitHub 普通文件 100MB 限制，仓库中默认不直接提交 `*.duckdb`。需要共享完整本地库时，将 `data/ah_screener.duckdb` 作为 GitHub Release 附件上传和下载。

可以通过环境变量覆盖：

```bash
export AH_SCREENER_DB=/path/to/ah_screener.duckdb
```

## 注意

本工具只用于研究和筛选，不构成投资建议。免费数据源可能存在延迟、字段变化或接口不稳定，重要结论应回到交易所、巨潮资讯网、HKEXnews 等官方披露源核验。
