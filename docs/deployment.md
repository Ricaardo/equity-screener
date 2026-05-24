# 部署文档

本文档记录本仓库在本机 macOS 环境的部署方式。部署目标是把 A/H/US 筛选器安装到稳定的 `uv` 虚拟环境，配置每日收盘后自动刷新，并生成 Markdown 研究报告。

## 1. 部署范围

- 代码目录：`/Users/x/ah-stock-screener`
- 主分支：`main`
- 默认数据库：`data/ah_screener.duckdb`
- 默认报告目录：`reports/`
- 默认计划任务：`~/Library/LaunchAgents/com.ah-screener.update.plist`
- 默认执行脚本：`scripts/update_all.sh`
- 默认日志：
  - `logs/scheduled-update.out.log`
  - `logs/scheduled-update.err.log`

## 2. 前置条件

- 已安装 `uv`。
- Python 版本满足 `>=3.10,<3.14`。
- 建议本机启动 Futu OpenD；行情、主数据、板块和基准会优先走 OpenD。
- 网络可访问 AKShare、Nasdaq Trader、SEC EDGAR、HKEXnews 等免费/公开回退源。
- 未启动 OpenD 时系统会使用免费源回退；OpenD 不覆盖的北交所现货、A/H 财务和生命周期数据仍依赖对应公开源。
- 如需同步美股历史摘牌生命周期，设置 `AH_SCREENER_ALPHA_VANTAGE_KEY`；未设置时 `sync-delisted-universe` 仍会同步 A 股和港股，并自动跳过 US lifecycle。

## 3. 部署步骤

从主分支拉取最新代码：

```bash
git switch main
git pull --ff-only
```

安装依赖：

```bash
uv sync --extra dev --extra ui --extra pdf --extra futu
```

初始化或迁移数据库：

```bash
uv run ah-screener init-db
```

安装每日定时任务，默认每天 18:30 本机时间运行：

```bash
uv run ah-screener install-schedule --hour 18 --minute 30 --top 120 --lookback-days 430
```

部署后可立即执行一次前台刷新：

```bash
uv run ah-screener update-all --top 120 --lookback-days 430 --fundamentals-top 120
```

刷新完成后生成筛选报告：

```bash
uv run ah-screener report
```

可选启动本地 Streamlit 研究台：

```bash
uv run --extra ui streamlit run src/ah_screener/ui/streamlit_app.py
```

## 4. 验证

基础验证：

```bash
make validate
```

检查覆盖率：

```bash
uv run ah-screener coverage-status
uv run ah-screener fundamentals-status --top 120
uv run ah-screener sync-delisted-universe
```

检查定时任务文件：

```bash
test -x scripts/update_all.sh
test -f ~/Library/LaunchAgents/com.ah-screener.update.plist
```

检查最近报告：

```bash
ls -lt reports/ah-screening-report-*.md
```

## 5. 回测与证据口径

默认回测只使用自然生成的候选快照，排除历史回放：

```bash
uv run ah-screener backtest --rebalance quarterly --industry-neutral --fee-bps 5 --slippage-bps 10 --benchmark A:000300
```

历史回放快照必须显式打开，只能作为诊断，不作为 edge 证明：

```bash
uv run ah-screener backtest --include-replay --rebalance quarterly --industry-neutral --fee-bps 5 --slippage-bps 10 --benchmark A:000300
```

RS 阈值证据分两层：

```bash
uv run ah-screener potential-sweep
uv run ah-screener potential-walk-forward
```

- `potential-sweep` 是 in-sample 阈值扫描，只用于观察参数敏感度。
- `potential-walk-forward` 先用过去窗口选阈值，再在后续窗口验证，是判断阈值是否稳健的最低证据口径。
- 当前已记录 active universe、A 股退市、HKEX 摘牌名单；US 历史摘牌依赖 `AH_SCREENER_ALPHA_VANTAGE_KEY`。历史验证仍需把自然快照持续积累后再判断 edge。

## 6. 回滚与停用

停用定时任务：

```bash
uv run ah-screener uninstall-schedule
```

保留数据库但回滚代码：

```bash
git switch main
git pull --ff-only
uv sync
```

如需换库，设置环境变量：

```bash
export AH_SCREENER_DB=/path/to/ah_screener.duckdb
```
