# US Screener — 数据源、ZIP 下载与更新节奏

美股线（`src/us_screener/`，独立 DuckDB `data/us_screener.duckdb`）完全使用免费 / 本地批量数据，**零逐个 API**。数据按更新机制分三类。

## 1. 全自动增量（零下载，`us-screener update` 每次实时拉）

| 数据 | 源 | 机制 |
|---|---|---|
| 今日价 / 市值 / PE | Sina 批量行情（`hq.sinajs.cn` gb_） | 每次 update 重拉，~35s |
| 宏观（信用利差 / 2s10s / VIX / 美元） | FRED（无需 key） | live CSV，每次拉 |
| 财报日历 | Nasdaq calendar API | live，每次拉 |
| 行业 / 板块分类 | FinanceDatabase | 本地 parquet 缓存 `data/us_fd_equities.parquet`，不用下载 |

→ **日常盘前：`us-screener update`，不下载任何 zip。**

## 2. 需要周期性重下的 ZIP（不是每天）

### stooq 全量日 K 线（历史基线）
- **下载页**：<https://stooq.com/db/h/>
- **文件**：`d_us_txt.zip`（美股）。多市场：`d_hk_txt.zip` / `d_jp_txt.zip` / `d_uk_txt.zip` / `d_world_txt.zip`（crypto/FX/指数/债券）
- **直链**：`https://static.stooq.com/db/h/d_us_txt.zip`（注意：常返回 `401`，需在 stooq 站内/登录后从下载页获取；手动下载到本地）
- **格式**：`<TICKER>.US,D,<YYYYMMDD>,<TIME>,O,H,L,C,<VOL>,<OPENINT>`，**复权**，全历史
- **频率**：周 / 月（只为延伸历史基线；日常价由 Sina 快照覆盖，中间技术面滞后几天，均线/52周高一周内几乎不动）
- **载入**：
  ```bash
  export US_SCREENER_STOOQ_ZIP=~/Downloads/d_us_txt.zip   # backfill 时自动优先用它
  us-screener load-stooq ~/Downloads/d_us_txt.zip --since 2022-01-01 [--delete-zip]
  ```

### SEC companyfacts.zip（全市场基本面 + PB/PE/市值）
- **直链**：<https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip>（~1.4 GB）
- **必须带 User-Agent**（含联系邮箱),否则 403：
  ```bash
  curl -L -A "your-name your-email@example.com" \
    -o ~/Downloads/companyfacts.zip \
    https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
  ```
- **频率**：季度（财报季度才变）
- **载入**：
  ```bash
  export US_SCREENER_SEC_FACTS_ZIP=~/Downloads/companyfacts.zip   # backfill 时自动优先用它
  us-screener load-sec-facts ~/Downloads/companyfacts.zip
  ```

## 3. 其它可选 SEC bulk（按需）
- **submissions.zip**（公司元数据 / SIC 行业码 / 历史名）：<https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip>（行业分类 FD 已覆盖，通常冗余）
- **company_tickers.json**（ticker↔CIK 映射，小文件，已在用）：<https://www.sec.gov/files/company_tickers.json>
- SEC bulk data 官方说明：<https://www.sec.gov/search-filings/edgar-application-programming-interfaces>

## 更新节奏小结

| 周期 | 动作 |
|---|---|
| **每天（盘前）** | `us-screener update` —— 零下载,Sina 快照 + FRED + Nasdaq 财报 + 选股 + 报告 |
| **每周 / 每月** | 重下 `d_us_txt.zip` → `us-screener load-stooq`（或日常用 `us-screener update --refresh-history` 走 Alpaca 复权增量,免重下） |
| **每季度** | 重下 `companyfacts.zip` → `us-screener load-sec-facts` |

> `--refresh-history`：日常 update 默认只刷快照(不动历史,避免复权拼接)。开此项用 Alpaca 复权增量补当日 bar(与 stooq 复权口径一致),daily_prices 天天新,stooq zip 也不必频繁重下；代价是 IEX 免费源覆盖比 stooq 略少。

## 全球历史银行（`data/global_history.duckdb`）

非美市场历史，纯价格技术筛选用：

| ZIP | 市场（按 ticker 后缀/目录归类） | 载入 |
|---|---|---|
| `d_hk_txt.zip` | HK | `us-screener load-stooq d_hk_txt.zip --markets HK --delete-zip` |
| `d_jp_txt.zip` | JP | 同上 `--markets JP` |
| `d_uk_txt.zip` | UK | 同上 `--markets UK` |
| `d_world_txt.zip` | CRYPTO / FX / BOND / INDEX / MM（按目录） | loader 内置 `path_market_map`，整包载入 |

> 注意：global db 与美股 `us_screener.duckdb` 分开；载入需把 `US_SCREENER_DB`/store 指向 `data/global_history.duckdb`。技术筛选：`us-screener global-screen HK --min-amount 5000000`。
