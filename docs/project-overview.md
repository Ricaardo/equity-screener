# 项目深度解读：A/H/US Stock Screener

> 本文是对本仓库的整体深度梳理：它解决什么问题、用什么数据、怎么打分、产出什么、如何运行。
> 偏架构与设计意图，细粒度的字段/接口表见 [`technical-solution.md`](./technical-solution.md)，潜力股逻辑见 [`potential-stock-scanner.md`](./potential-stock-scanner.md)。

---

## 1. 一句话定位

**面向个人投资者、本地运行、免费数据源优先、强调"可解释"的 A 股 + 港股 + 美股股票/ETF 筛选系统。**

它不是黑箱选股器，也不是交易系统。它的工作是：把全市场几千只标的，通过"先排雷、再排序、最后留给人研究"的流水线，收敛成几十只带证据链、带评分拆解、带去重的候选池，并落库、出报告、上看板。

核心信念写在代码与文档里：

- **不做黑箱选股，只做可解释筛选**——每个分数都能拆回估值/流动性/基本面/技术/风险的子项，每个标签都保留来源与证据等级。
- **先排雷，再排序**——ST/退市风险、流动性地板、风险罚分先把雷剔掉或重罚，再谈排名。
- **所有外部数据先落本地库**——避免重复抓取和免费接口波动，DuckDB 做单文件本地缓存。
- **A/H/US 统一数据模型**——一套 `securities` 主表，用 `market` 字段区分,同主体（ADR/多地上市）能去重。
- **主题只作上下文，不直接抬分**——热门题材用于解释、分桶、行业化理解，不作为综合分的权重，避免追热点。

---

## 2. 整体架构与数据流

系统是一条单向流水线，每一步都把结果写回 DuckDB，下游从库里读。

```
                    免费/公开数据源
   Futu OpenD(优先) · AKShare · Nasdaq Trader · SEC EDGAR · HKEXnews · Alpha Vantage
                          │
        ┌─────────────────┼─────────────────────────────┐
        ▼                 ▼                              ▼
  [Ingest 采集]     [Classify 分类]              [Enrich 增强]
  sync-spot         classify-securities          sync-a-tags / curated-tags
  sync-us-batch     (板块/ST/港股通/ETF)          sync-hkex-documents (PDF抽取)
  sync-history      sync-delisted-universe        import-tags / industry-map
  sync-fundamentals (退市生命周期=幸存者偏差)       sync-identity-mappings
        │
        ▼  全部落库到 data/ah_screener.duckdb (单文件)
        │
  ┌─────┴───────────────────────────────────────────────┐
  ▼                          ▼                            ▼
[Score 评分]            [Refine 提炼]              [Potential 潜力]
expert-score            refined-export             potential-scan
(大师框架+基本面          (主题/风格/同主体去重)      (筑底+RS+拐点+早期题材)
 +技术+行业分位)         etf-export(ETF工具评分)     potential-walk-forward(样本外验证)
        │
        ▼
  ┌─────┴──────────────────────────────────────┐
  ▼              ▼                ▼              ▼
[Backtest]   [Report]      [UI 看板]      [Schedule]
backtest     report        Streamlit      update-all
(等权/调仓/   (Markdown)    研究台          install-schedule
 费用滑点/                                 (macOS launchd 每日)
 行业中性)
```

**关键设计点：采集与计算解耦。** 采集层（`sources/`）只负责把数据搬进库；评分、提炼、回测全部在库内进行。这样免费接口的延迟和不稳定不会污染分析逻辑，也方便复跑。

---

## 3. 数据源策略：OpenD 优先 + 公开源回退

这是项目的一个核心工程取舍。本地 Futu OpenD（可选依赖 `futu-api`）作为首选行情与主数据源，连不上或字段不覆盖时自动回退到免费/公开入口：

| 数据类型 | 首选 | 回退 |
|---|---|---|
| A/H/US 股票&ETF 主数据、快照、日线 | Futu OpenD | AKShare（北交所现货、US `stock_us_daily`） |
| 美股证券目录 | Futu OpenD | Nasdaq Trader symbol directory |
| A 股行业/概念板块、港股通成份 | Futu OpenD `get_plate_*` / `HK.GangGuTong` | AKShare |
| A/H/US 基准指数 | Futu OpenD | — |
| 美股基本面 | SEC EDGAR Company Facts | — |
| A/H 财务三表 | AKShare 东方财富财务接口 | — |
| 退市/摘牌生命周期 | A股 AKShare退市记录 / 港股 HKEX官方名单 / 美股 Alpha Vantage `LISTING_STATUS`（需 key） | 无 key 时跳过 US，不中断 |
| 港股题材增强 | HKEXnews PDF 搜索+下载+抽取 | 本地 PDF `ingest-document` |

注意 Stooq 已被移除（其免费 CSV 端点现需人工 captcha 取 key，无法自动化）——这反映项目维护时对"是否真的能脚本化"的务实判断。

源码在 `src/ah_screener/sources/`：`futu_client.py`(792行,最大)、`akshare_client.py`(1025行)、`us_client.py`、`hkexnews_client.py`。

---

## 4. 评分体系：专家模型（expert-score）

这是系统的分析核心，落在 `expert_model.py`（1124 行，全仓最大模块）。它把多位中外投资大师的框架编码成可计算的子分，再加权合成 `expert_score`（0-100）。

### 4.1 基础子分

先算几个原子分（`scoring.py`）：估值分、流动性分、风险罚分（`_risk_penalty`），再加上基本面输入分、技术输入分。

### 4.2 大师框架代理（master proxies）

用基础子分线性组合出各大师风格的代理打分，例如：

- **Graham 价值**：`valuation*0.75 + 防御性央国企加成*0.25`
- **Buffett 质量代理**：`liquidity*0.40 + cap*0.35 + risk_inverse*0.25`
- **Fisher 成长**：`fundamental*0.45 + technical*0.35 + liquidity*0.20`
- **Lynch GARP**：`fundamental*0.35 + valuation*0.35 + technical*0.20 + liquidity*0.10`
- **O'Neil 动量**：`technical*0.78 + liquidity*0.22`

中国大师框架（`china_master_score`）单独建模，例如张磊长期主义、邱国鹭质量价值、但斌/林园复利、冯柳逆向等，各有不同子分权重。

### 4.3 行业分位与行业适配（关键防偏差设计）

`_peer_scores` / `_group_rank` 在 `(market, industry_peer_group)` 分组内排名，而非全市场直接比。`_industry_fit_score` 用行业化阈值评估现金流质量、负债率、ROE、营收/利润 CAGR、资本开支效率、研发费率（医疗/科技用更高的研发阈值）。

**意图：避免跨行业直接比较 PE/ROE 的失真**——银行和软件不该用同一把尺子。

### 4.4 最终合成

```
expert_score =  master_score          * 0.20
              + china_master_score    * 0.28   ← 中国大师权重最高
              + fundamental_score     * 0.18
              + industry_fit_score    * 0.10
              + technical_score       * 0.14
              + liquidity             * 0.04
              + peer_score            * 0.06
```

合成后过闸：`penalty>=80 或 expert_score<42` → 剔除；`>=68 且 technical>=55` → 候选；`>=56` → 观察。

题材分（`_theme_score`）只参与解释和分桶，**不进入上面这个加权式**——这是"主题不抬分"原则的代码体现。

---

## 5. 提炼与去重（refined-export）

`expert_score` 排名后会有大量同质标的（同主题、同风格、A/H/US 同一主体多地上市）。`selection.py` 做三层去重：

- 同一**主题桶**默认最多 3 只
- 同一**风格桶**优先最多 2 只
- A/H/US **同主体**（ADR/多地上市/同名）只留 expert 分最高的一只（依赖 `company_identity_mappings`）

ETF 单独走 `etf_model.py`：分类为宽基/行业/主题/跨境/债券/商品/货币，按流动性、规模、动量做"工具型评分"，默认同指数/同赛道合并只留最优，`--raw` 看明细。`selection.py` 里专门防御了"分类失败时不要把识别不出的 ETF 错误合并成一组"。

输出四个池：**候选池 / 观察池 / 剔除池 / 提炼候选池**。

---

## 6. 潜力股扫描（potential-scan）：与专家模型相反的相位

这是一个刻意独立于 expert 模型的模块（`potential.py`，499 行），因为目标相反：

> expert 模型奖励**已经在强趋势里的赢家**；潜力扫描要找**还没起涨、正在慢慢筑底**的标的。在 expert 上调参做不到，所以独立打分。

四根支柱（每根 0-100，按市场加权——A 股偏题材/RS，港股均衡，美股偏基本面 CANSLIM 风格）：

- **A 技术筑底**：波动率处自身一年低位且下降（VCP）、箱体收窄、MA20 走平转上收复 MA120/200、距高点 -25%~-5%（近枢轴不追高）、`return_20d` 不大（没暴涨过）
- **B 相对强度**：RS 线创新高、RS 排名分位抬升
- **C 基本面拐点（二阶导）**：不是看高 ROE，而是看"在变好"——增速加速、扭亏、毛利扩张
- **D 题材早期**：带新兴题材标签但同组排名靠后（"未被发现"）

合成后过排雷闸（跌破箱体/MA200、"已被发现"过滤、基本面驱动市场若减速则重罚），并为每只候选生成结构化情景卡（触发/目标/证伪/时间止损 8-12 周）。

### 防过拟合：这是项目最严谨的部分

- `RS_RANK_CUT=70` 等阈值在代码注释里被明确标注为"**仅是 operating threshold，来自 in-sample sweep，不能当 edge 证明**"。
- `potential-validate`：用前瞻 8 周超额收益（vs 同日全宇宙中位数）验证信号，**前瞻收益只作 label，绝不用来定义 setup**（防 look-ahead）。
- `_sampled_setups` 做非重叠历史采样（120 日预热 + 步长抽样）。
- `potential-sweep` 看 in-sample 参数敏感度，`potential-walk-forward` 做**样本外**阈值验证——只有走完 walk-forward 才能谈 edge。

---

## 7. 回测（backtest）：诚实优先

`backtest.py`（428 行）对 `refined_candidates` 快照做等权回测：支持 snapshot/monthly/quarterly 调仓、手续费、滑点、行业分散约束、A/H/US 免费基准对比。

最值得注意的是**自然快照 vs 历史回放快照**的区分：

- 真实候选快照随时间自然累积。
- 只有一天真实快照时，可用 `backfill-refined-snapshots` 基于已存真实日线生成历史回放快照（写入 `snapshot_source=historical_replay`、`is_replay=true`）。
- **`backtest` 默认排除历史回放**，只用自然快照；要诊断回放必须显式 `--include-replay`，且文档明确警告"不能把该收益当实盘 edge 证明"。

这种对"哪些数字可以拿来吹"的克制，是全项目反复出现的工程态度。

---

## 8. 幸存者偏差审计

`sync-delisted-universe` 拉退市/摘牌生命周期（A股 AKShare、港股 HKEX 官方、美股 Alpha Vantage），写入 `security_lifecycle_events`，配合 `security_universe_snapshots`（universe 快照）做幸存者偏差审计——确保回测和筛选不是只在"今天还活着的股票"上做，避免高估历史表现。

---

## 9. 数据模型（DuckDB 表）

单文件 `data/ah_screener.duckdb`，主要表（建表见 `storage.py`）：

| 表 | 内容 |
|---|---|
| `securities` | 统一主表：market/symbol/asset_type/board/exchange/is_st/is_hk_connect/status |
| `market_snapshots` | 现货快照：价格/涨跌/量额/换手/PE/PB/市值 |
| `daily_prices` | 日线 OHLCV |
| `security_universe_snapshots` / `security_lifecycle_events` | universe 快照 + 退市生命周期（幸存者偏差） |
| `technical_indicators` | 技术指标 |
| `financial_statement_items` / `financial_metrics` | 三表原始项 + 派生财务指标 |
| `company_tags` | 行业/概念/题材/风险标签，带 evidence_level A/B/C |
| `company_identity_mappings` | A/H/US 同主体映射（去重用） |
| `company_documents` / `document_extractions` | HKEXnews PDF 文档 + 抽取结果 |
| `hot_theme_definitions` | 策展题材定义 + 权重 |
| `expert_screening_results` | 专家评分结果 |
| `industry_valuation_stats` | 行业估值分位统计 |
| `refined_candidates` | 去重提炼候选（回测输入） |
| `potential_candidates` | 潜力扫描结果 |
| `ingest_failures` | 采集步骤失败留痕（可观测性，定位覆盖率下降） |

数据库 >100MB 不进 git，需共享时作 GitHub Release 附件（`scripts/upload_release_db.sh`），可用 `AH_SCREENER_DB` 环境变量覆盖路径。

---

## 10. 代码地图（src/ah_screener/）

| 模块 | 行数 | 职责 |
|---|---|---|
| `expert_model.py` | 1124 | 专家评分核心：大师框架 + 行业分位 + 合成 |
| `pipeline.py` | 1139 | 编排各步骤，`update-all` 全量刷新（单步失败不中断） |
| `ui/streamlit_app.py` | 1191 | 本地研究台看板 |
| `sources/akshare_client.py` | 1025 | AKShare 回退源 |
| `fundamentals.py` | 909 | 三表/财务指标/CAGR/Piotroski 类计算 |
| `cli.py` | 867 | Typer CLI，36 个子命令，入口 `ah-screener` |
| `reporting.py` | 809 | Markdown 报告生成 |
| `sources/futu_client.py` | 792 | Futu OpenD 首选源 |
| `sources/us_client.py` | 541 | Nasdaq Trader / SEC EDGAR / Alpha Vantage |
| `potential.py` | 499 | 潜力股扫描 + 样本外验证 |
| `storage.py` | 442 | DuckDB 建表与读写 |
| `backtest.py` | 428 | 等权回测引擎 |
| `etf_model.py` | 303 | ETF 分类与工具评分 |
| `documents.py` | 223 | PDF/公告文本抽取 |
| `hkexnews_client.py` | 213 | HKEXnews 搜索下载 |
| `technical.py` | 192 | 技术指标计算 |
| `selection.py` | 144 | 去重服务层 |
| `classification.py` | 139 | 板块/ST/港股通/ETF 分类 |
| `identity.py` | 128 | 同主体映射 |
| `scheduler.py` | 92 | macOS launchd 定时安装 |
| `point_in_time.py` | 80 | point-in-time 基本面（防前视） |
| `scoring.py` | 69 | 共享原子打分原语 |
| `universe.py` | 52 | universe 快照 |
| `config.py` | 23 | Settings |

---

## 11. CLI 命令全景（36 个）

入口 `ah-screener`（Typer）。按流水线阶段分组：

**初始化与采集**：`init-db` · `sync-spot` · `sync-us-spot` · `sync-us-batch` · `sync-history` · `sync-benchmarks` · `sync-fundamentals`

**分类与增强**：`classify-securities` · `sync-delisted-universe` · `sync-a-tags` · `sync-curated-tags` · `sync-identity-mappings` · `import-tags` · `import-industry-map` · `ingest-document` · `sync-hkex-documents`

**计算与评分**：`technical` · `expert-score` · `industry-valuation-stats`

**导出与提炼**：`expert-export` · `refined-export` · `etf-export` · `candidate-changes`

**潜力扫描**：`potential-scan` · `potential-validate` · `potential-sweep` · `potential-walk-forward`

**回测与状态**：`backfill-refined-snapshots` · `backtest` · `coverage-status` · `fundamentals-status` · `etf-cluster-validate`

**产出与运维**：`report` · `update-all` · `install-schedule` · `uninstall-schedule`

一键全量：`ah-screener update-all --top 120 --lookback-days 430`
macOS 定时（默认每天 18:30，生成 launchd plist + `.update.lock` 互斥锁）：`ah-screener install-schedule --hour 18 --minute 30`

---

## 12. 工程实践

- **包管理**：`uv`（`uv sync`，UI 用 `--extra ui`）。
- **质量门**：`Makefile` 统一入口——`make validate`(提交前核心检查) / `format` / `lint`(ruff) / `typecheck` / `test`。`.pre-commit-config.yaml` + GitHub Actions(ruff+pytest)。
- **测试**：最小套件覆盖基准回测、同类去重、ETF 分类、基本面评分边界。UI 冒烟用 `browser-use` 截图（`scripts/check_ui_screenshots.sh`）。
- **容错**：`update-all` 单步失败不中断整轮；定时任务 `.update.lock` 防 DuckDB 写锁冲突与重复拉免费接口；US lifecycle 缺 key 时静默跳过。

---

## 13. 边界与免责

文档明确声明：**仅用于研究和筛选，不构成投资建议。** 免费数据源可能有延迟、字段变化或接口不稳定，重要结论应回到交易所、巨潮资讯网、HKEXnews 等官方披露源核验。

系统不做下单/交易，不预测价格，只把全市场收敛成"值得人工进一步研究的小集合"，并把每个判断的依据摆在台面上。

---

## 14. 近期增强（产品化 + 模型可信度）

在原有架构上叠加了一轮"产品 + 可信度"增强：

**产品化（给 AI 用）**
- 报告从单一 Markdown 扩展为 **Markdown + JSON 双产物**，并写 `latest` 固定指针。JSON 用稳定英文键，每只候选带评分拆解 + `reasons` 证据链，AI 直接读 `reports/ah-screening-report-latest.json`。
- Streamlit 从交互式研究台改为**只读报告查看器**：默认展示最新报告（核心/提炼/潜力/ETF 候选卡 + 完整 Markdown 渲染），不再做实时筛选，数据源从 DB 改为报告 JSON。

**模型可信度**
- **风险闸**接入 `security_lifecycle_events`（退市/摘牌命中即重罚），并补港股仙股/低价、美股低价退市、清盘类风险名——不再只对 A 股名称匹配。
- **数据缺失**按不确定性折扣处理（缺失 ≠ 中性），技术+基本面均缺失视为不可评估并降权。
- **模型参数外置**到 `weights.py`（合成权重/决策阈值/大师代理/风险罚分），附来源标注；纯重构由特征化测试锁定零漂移。
- **主题元数据**（优先级 + 风格桶）上移到 `HotTheme` 定义，单一真相源。
- **同主体模糊匹配** `derive_fuzzy_identity_mappings`：策展之外按规范化名称做跨市场补充（策展优先、停用词防误配），实测补出数百个真实 A/H 同主体。
- **专家决策前瞻验证** `expert-validate`：复用潜力模块的样本外纪律，对 core/watchlist/reserve/reject 分桶检验前瞻超额收益与单调性，附幸存者偏差/无前视声明——样本不足时诚实报 0（与回测口径一致）。
- **采集可观测性**：`ingest_failures` 表 + `ingest-status` 命令，让"为什么这次候选比上次少"可追溯。

> 这轮增强把"最严谨标准没有均匀覆盖"的问题收敛：potential 模块的样本外纪律推广到了主力 expert 模型，"先排雷"原则补齐了 HK/US 盲区，散落的魔法数字集中且可审阅。

## 15. 一句话总结

> 这是一个把"先排雷、再排序、保留证据、克制吹牛"做成代码的本地化 A/H/US 选股流水线：OpenD+免费源采集 → DuckDB 落库 → 可解释专家评分 + 反相位潜力扫描 → 去重提炼 → 诚实回测（区分自然/回放快照、样本外验证、幸存者偏差审计）→ 报告与看板。
