# 多资产筛选系统重构与优化方案

更新时间：2026-05-23
状态：草案（待评审）
关联文档：[`technical-solution.md`](./technical-solution.md)、[`todo.md`](./todo.md)

---

## 1. 背景与目标

项目最初目标是**筛选股票**（A 股 + 港股），后续增量接入了 A/港股 ETF，并计划接入**美股市场**与**美股 ETF**。在快速增量过程中暴露出三类结构性问题：

1. **股票与 ETF 是两套割裂的链路**：股票走 `score → technical → fundamentals → expert → refine`，ETF 走独立的 `etf_model`，二者评分口径、去重逻辑、报告呈现都不统一。
2. **技术指标不一致**：`technical.py` 本身是资产无关的（任何 `(market, symbol)` 都能算 MA/RSI/趋势），但 ETF 因为被 `asset_type == 'stock'` 过滤掉而**拿不到真实技术指标**，只能用 `etf_model` 里的 `50 + pct_change × 6` 粗略动量代理。
3. **横向扩展成本高**：每新增一个市场（美股）或资产类（美股 ETF），都要在 `pipeline.py`、`cli.py`、`sources/`、`reporting.py` 多处手工改动，缺少统一的"资产类别"抽象。

本方案目标：

- **统一技术指标引擎**：股票和 ETF 共用同一套技术评分（用户明确诉求："技术指标应该一致"）。
- **分离筛选标准**：股票按"基本面 + 大师框架 + 行业适配 + 主题"筛，ETF 按"流动性 + 规模 + 跟踪标的 + 同质化去重"筛——技术引擎共用，筛选标准分治。
- **可扩展到美股 / 美股 ETF**：新增市场或资产类别只需实现适配器 + 注册策略，不改核心链路。
- **ETF 双层去重**（本轮已确认）：先按指数折叠同指数多家基金，再按相关性簇保留每簇代表。

> 评审范围：本方案是**渐进式重构**，不是推倒重来。现有 DuckDB schema、回测、文档解析、Streamlit 看板均保留，按阶段替换内部结构。

---

## 2. 现状架构与问题诊断

### 2.1 当前数据流

```
sync-spot ──► market_snapshots / securities        (A/HK/US 股票 + A/HK ETF，asset_type 区分)
   │
   ├─ run-scores ──────────► screening_scores       (简单流动性/估值/主题分，scoring.py)
   │
   ├─ sync-history(top150) ─► daily_prices           (⚠ _latest_snapshots 过滤掉 ETF)
   │     └─ run-technical ──► technical_indicators    (technical.py，资产无关，但无 ETF 数据)
   │
   ├─ sync-fundamentals ───► financial_metrics        (⚠ 同样只取 stock)
   │
   ├─ run-expert ──────────► expert_screening_results (⚠ expert_model 内再次过滤 stock)
   │     └─ refine_candidates ─► refined_candidates    (主题桶/风格桶/A-H 同主体去重)
   │
   └─ ETF 独立旁路：enrich_etf_snapshot / consolidate_etf_candidates
                          └─► 报告 section 6（raw enrich + head(20)，无去重）
```

### 2.2 模块与体量

| 模块 | 行数 | 职责 | 问题 |
| --- | ---: | --- | --- |
| `pipeline.py` | 1188 | 编排 + ETF 导出 + 回测 + 身份映射 | God module，职责过载 |
| `expert_model.py` | 1008 | 大师框架 + 行业适配 + 主题 + 精选去重 | 股票专用，无法复用到 ETF |
| `fundamentals.py` | 909 | A/HK/US 三表抓取与打分 | 与市场强耦合 |
| `cli.py` | 814 | 30+ 命令手工串联 | 编排逻辑散落在 CLI |
| `akshare_client.py` | 742 | A + HK + A/HK ETF 抓取 | 抓取 + 清洗混在一起 |
| `etf_model.py` | 357 | ETF 分类 / 评分 / track 去重 | 与股票链路平行，技术分是假代理 |
| `scoring.py` | 177 | 简单分（与 expert 重复） | 两套打分系统并存 |
| `technical.py` | 134 | MA/RSI/趋势/动量 | **已是资产无关，可复用** |

### 2.3 核心问题清单

1. **`asset_type == 'stock'` 过滤散落多处**（`_latest_snapshots` L250、`run_expert_scores` L325），没有统一的"宇宙（universe）"抽象，导致 ETF 想接入技术链路必须改多处。
2. **三套评分系统**：`scoring.py`（简单）、`expert_model.py`（股票深度）、`etf_model.py`（ETF）。`scoring.py` 实际已被 expert 覆盖，属冗余。
3. **ETF 技术分是伪指标**：`etf_model.py` 的 `momentum_score = 50 + pct_change×6` 与股票的 MA/RSI/趋势完全不同口径——直接违反"技术指标一致"。
4. **ETF 去重未启用且粒度不够**：`consolidate_etf_candidates` 只在 `etf-export --consolidate` 触发，报告 section 6 用 raw `head(20)`，所以出现 5 只中证 A500、2 只中证 1000、2 只恒生科技。即便启用，也只到 track 级，未处理"大盘宽基簇 / 中国互联网簇"等高相关同质。
5. **规则硬编码**：`ETF_RULES`、`ETF_TRACK_RULES`、热门主题关键词全写死在代码里，市场扩张后维护成本高。
6. **数据源无统一接口**：`akshare_client` / `us_client` 函数签名不一致，新增美股 ETF 没有可插拔位置。

---

## 3. 设计原则

1. **一个技术引擎，多套筛选标准**：技术指标（趋势/动量/RSI/相对强弱）对所有可交易标的统一计算；"是否值得买"的标准按资产类别分治。
2. **资产类别（AssetClass）是一等公民**：`STOCK / ETF`（未来可扩 `BOND / COMMODITY`），每类声明自己的富集器（enrichers）和打分策略（scorer）。
3. **市场（Market）与资产类别正交**：`US × ETF`、`A × STOCK` 是 (market, asset_class) 的组合，新增组合 = 注册一个适配器，不改链路。
4. **先排雷、再排序、最后去同质**：保留现有"过滤 → 打分 → 去重"哲学，把去重升级为可配置的多层。
5. **规则数据化**：分类/簇/主题等规则尽量外置为配置（YAML/CSV），代码只负责加载与执行。
6. **渐进迁移**：每阶段都能独立上线、独立回滚，旧链路与新链路可并行一段时间。

---

## 4. 目标架构

### 4.1 分层

```
┌─────────────────────────────────────────────────────────────┐
│ sources/        市场适配器（抓取，纯 I/O）                       │
│   SpotSource / HistorySource / FundamentalSource 协议           │
│   akshare(A,HK) · us(US) · 未来 us_etf                          │
├─────────────────────────────────────────────────────────────┤
│ ingest/         落库（market_snapshots / daily_prices / ...）    │
├─────────────────────────────────────────────────────────────┤
│ universe.py     统一标的宇宙：按 (market, asset_class, board)    │
│                 切片，集中替代散落的 asset_type 过滤              │
├─────────────────────────────────────────────────────────────┤
│ enrich/         富集器（资产无关 + 资产特定）                     │
│   technical.py        ← 股票 & ETF 共用（统一技术指标）          │
│   fundamentals.py     ← 仅 STOCK                                │
│   etf_classify.py     ← 仅 ETF（分类 / track / cluster）         │
├─────────────────────────────────────────────────────────────┤
│ score/          打分策略（按 AssetClass 分治）                   │
│   StockScorer  = 大师框架 + 行业适配 + 主题 + 基本面 + 技术       │
│   EtfScorer    = 流动性 + 规模 + 跟踪质量 + 技术（统一）          │
├─────────────────────────────────────────────────────────────┤
│ select/         去重与精选                                       │
│   refine_stocks   主题桶/风格桶/A-H 同主体                       │
│   dedup_etf       双层：track 折叠 → cluster 代表                │
├─────────────────────────────────────────────────────────────┤
│ report/ + ui/   报告与看板                                       │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 关键抽象

```python
# universe.py
class AssetClass(str, Enum):
    STOCK = "stock"
    ETF = "etf"

def universe(store, *, market=None, asset_class=None) -> pd.DataFrame:
    """统一切片入口，取代各处 asset_type == 'stock' 的散落过滤。"""

# score/base.py
class Scorer(Protocol):
    asset_class: AssetClass
    def score(self, enriched: pd.DataFrame) -> pd.DataFrame: ...

SCORERS = {AssetClass.STOCK: StockScorer(), AssetClass.ETF: EtfScorer()}
```

技术富集对两类都跑；`StockScorer` 额外消费 `fundamentals`，`EtfScorer` 额外消费 `etf_classify` 与流动性/规模——**技术分字段 (`technical_score`, `trend_score`, `momentum_score`) 完全一致**。

---

## 5. 关键重构点

### 5.1 统一技术指标引擎（最高优先级，直接回应用户诉求）

**问题**：ETF 拿不到真实技术分。
**改动**：

1. `sync_history` 的宇宙不再写死 `asset_type=='stock'`：改为可传 `asset_classes=[STOCK, ETF]`，ETF 也进 `top-N by amount` 抓日线。
2. 新增/复用 ETF 日线抓取：A/HK ETF 用 `ak.fund_etf_hist_em`，美股 ETF 复用 `fetch_us_history`（同股票端点）。`fetch_history` 增加 ETF 分支。
3. `run_technical_indicators` 已是资产无关，无需改——ETF 日线进 `daily_prices` 后自动产出 `technical_indicators`。
4. `etf_model` 中的 `momentum_score = 50 + pct_change×6` **删除**，改为 join `technical_indicators.technical_score`。ETF 综合分 = 流动性/规模/跟踪 + **统一技术分**。

**收益**：ETF 与股票技术口径一致，可直接比较；趋势/动量真实可解释。

### 5.2 资产类别抽象，收敛过滤逻辑

把 `_latest_snapshots`、`run_expert_scores`、`export_etf_candidates`、`reporting` 里重复的 `asset_type` 过滤统一到 `universe.py`。后续新增资产类别只在一处登记。

### 5.3 ETF 筛选标准独立 + 双层去重（本轮确认实现）

**ETF 评分**（与股票不同，无基本面/估值）：
```
etf_score = 流动性分(0.45) + 规模分(0.20) + 技术分(0.20，统一) + 跟踪质量分(0.15) + 类别偏好 − 风险罚分
```
（`跟踪质量` = 规模充足 + 折溢价/跟踪误差，后续接入；首版可先用规模+成交额代理。）

**双层去重**（用户已选「双层」）：

- **第一层 · track 级折叠**：同一指数的多家基金（5 只中证 A500 → 留 1 只）。复用现有 `etf_peer_group = category:track`，保留流动性/规模/技术综合最优者，其余进 `peer_alternatives`，并记录 `peer_count`。
- **第二层 · cluster 级代表**：新增 `etf_cluster`（相关性簇），把高度相关的不同指数归为一簇，每簇保留 Top-1（可配 Top-N）。

  | cluster | 归入的 track |
  | --- | --- |
  | 大盘宽基 | 沪深300 / 上证50 / 中证A500 / MSCI中国A50 / 深证100 |
  | 中小盘宽基 | 中证500 / 中证1000 / 中证2000 |
  | 成长科创 | 创业板指 / 创业板50 / 科创50 |
  | 中国互联网科技 | 恒生科技 / 恒生互联网 / 中概互联网 |
  | 港股核心宽基 | 恒生指数 / 恒生中国企业 |
  | 海外宽基 | 纳斯达克100 / 标普500 / 日经225 / 德国DAX |
  | 红利策略 | 中证红利 / 恒生高股息 |
  | 行业-金融 | 证券 / 银行 |
  | 行业-科技硬件 | 半导体芯片 / 人工智能 / 光模块 |
  | 资源商品 | 黄金 / 有色金属 / 煤炭 / 原油 |
  | …（其余沿用 track） | |

**实现要点**：
- `enrich_etf_snapshot` 增列 `etf_cluster`（由 `ETF_CLUSTER_RULES: track → cluster` 映射，未命中则 `cluster = track`）。
- `consolidate_etf_candidates(df, group_col="etf_peer_group")` 参数化分组列：默认 track 级（向后兼容现有测试），传 `group_col="etf_cluster"` 即簇级。双层 = 先 track 再 cluster。
- **报告 section 6 接入去重**：`reporting.py` L431 的 `head(20)` 改为调用双层去重后的结果，并增加「簇 / 同组数（peer_count）」列，让同质化既被折叠又可见。

> 该模块边界清晰、与主链路解耦，可作为重构第一个落地项（见 §8 阶段一）。

### 5.4 数据源适配器接口（为美股 ETF 铺路）

定义协议，让每个 (market, asset_class) 实现统一签名：
```python
class SpotSource(Protocol):
    def fetch_spot(self) -> tuple[Securities, Snapshots]: ...
class HistorySource(Protocol):
    def fetch_history(self, symbol, start, end) -> DailyPrices: ...
```
注册表 `SOURCES[(market, asset_class)] = adapter`。`pipeline.sync_spot` 改为遍历注册表，新增美股 ETF = 注册一个 adapter。

### 5.5 拆分 pipeline 与收敛 CLI

- `pipeline.py` 拆为 `ingest.py`（同步落库）/ `enrich.py`（技术+基本面+ETF 富集）/ `screen.py`（打分+精选）/ `backtest.py`。
- CLI 命令瘦身：保留 `update-all` 作为编排入口，子命令只调用对应服务函数。

### 5.6 规则数据化

`ETF_RULES / ETF_TRACK_RULES / ETF_CLUSTER_RULES / 热门主题` 外置为 `config/*.yaml` 或 CSV，代码加载。便于在不改代码的情况下随市场扩张维护关键词。（可作为后期项，非阻塞。）

### 5.7 移除冗余 `scoring.py`

`screening_scores` 的简单分已被 expert 模型覆盖。确认无下游依赖后下线，减少一套口径。

---

## 6. 美股与美股 ETF 扩展

| 能力 | 现状 | 需要做 |
| --- | --- | --- |
| 美股股票现货 | ✅ `fetch_us_spot` | 纳入 universe 注册表 |
| 美股股票历史 | ✅ `fetch_us_history` | 纳入 `sync_history` 多市场遍历 |
| 美股基本面 | ✅ SEC EDGAR (`fundamentals._us_metric_row`) | 复用 |
| **美股 ETF 现货** | ❌ 无 | 新增 adapter（Nasdaq Trader ETF 列表 / Stooq / yfinance 免费源） |
| **美股 ETF 历史** | 可复用 `fetch_us_history` | adapter 注册即可 |
| 美股 ETF 分类/簇 | ❌ | 扩展 `ETF_RULES/CLUSTER`：SPY/QQQ/IWM/纳指/标普/罗素 等英文关键词 |

要点：美股 ETF 接入后，技术引擎与去重逻辑**零改动复用**——这正是统一技术 + 资产类别抽象的回报。分类与簇规则需要补英文关键词（标普 500、纳指 100、罗素 2000、行业 SPDR 等）。

---

## 7. 数据模型变更

- `technical_indicators`：无需改 schema，ETF 行自然写入。
- `market_snapshots` / `securities`：已有 `asset_type`，无需改。
- ETF 富集新增列（落到导出/报告，不一定持久化）：`etf_cluster`、`peer_count`、`cluster_count`、`etf_track`、`technical_score`（来自 join）。
- 若需持久化 ETF 评分，可新增 `etf_scores` 表（可选，首版用即时计算）。

向后兼容：所有变更为**增列**，不删字段，旧库可直接升级。

---

## 8. 渐进式迁移路径

| 阶段 | 内容 | 风险 | 可独立上线 |
| --- | --- | --- | --- |
| **一** | ETF 双层去重（§5.3）+ 报告 section 6 接入去重 | 低，纯增量 | ✅ 立即见效 |
| **二** | 统一技术引擎：ETF 进 `daily_prices` / `technical_indicators`，ETF 评分接入真实技术分（§5.1） | 中，需补 ETF 日线源 | ✅ |
| **三** | `universe.py` 抽象，收敛 `asset_type` 过滤（§5.2） | 中，触及多模块 | ✅ |
| **四** | 数据源适配器接口 + 美股 ETF 接入（§5.4、§6） | 中 | ✅ |
| **五** | 拆分 pipeline / 收敛 CLI / 规则数据化 / 下线 scoring.py（§5.5-5.7） | 较高，结构性 | 分步 |

建议顺序即上表。**阶段一、二直接回应用户当前两个诉求**（去同质化 + 技术指标一致），优先做。

---

## 9. 风险与权衡

- **ETF 日线源稳定性**：A/HK ETF 历史接口（`fund_etf_hist_em`）可能限流，需沿用现有"分批 + 多接口回退"策略。
- **cluster 划分主观性**：相关性簇是经验划分，建议后续用日线收益相关系数做实证校验（>0.9 自动归簇），先用人工表起步。
- **重构期双链路并存**：阶段三/五期间新旧逻辑共存，需测试覆盖防回归（现有 `tests/test_etf_model.py` 等需扩充 cluster 用例）。
- **下线 scoring.py**：需先确认 `screening_scores` 无 UI/报告/回测依赖。

---

## 10. 里程碑与工作量（粗估）

| 阶段 | 估算 | 关键产出 |
| --- | --- | --- |
| 一 | 0.5 天 | `etf_cluster` + 参数化 `consolidate` + 报告接入 + 测试 |
| 二 | 1 天 | ETF 日线抓取 + 技术分 join + 删除伪动量 |
| 三 | 1 天 | `universe.py` + 过滤收敛 + 回归测试 |
| 四 | 1–2 天 | adapter 协议 + 美股 ETF 源 + 英文分类簇 |
| 五 | 2–3 天 | pipeline 拆分 + CLI 瘦身 + 规则外置 |

---

## 11. 决策记录

| # | 决策点 | 结论 | 备注 |
| --- | --- | --- | --- |
| 1 | cluster 划分方式 | ✅ **先人工表起步，后续用收益相关系数（>0.9）自动验证** | 同型思路也用于潜力股历史胜率：先人工口径，后自动校准 |
| 2 | 报告/看板 ETF 呈现 | ✅ **两表并存**：①完整工具池规模（分类计数）②双层去重精选 | 见 §12 前端布局 |
| 3 | 美股 ETF 现货免费源 | ⏳ 待定（Nasdaq Trader 列表 + Stooq 历史 / yfinance） | 阶段四再定 |
| 4 | `scoring.py` 是否下线 | ⏳ 待定（确认无 UI/报告/回测依赖后下线） | 阶段五处理 |

> 关联：潜力股扫描详见 [`potential-stock-scanner.md`](./potential-stock-scanner.md)。

---

## 12. 前端（Streamlit 看板）布局优化

现状（`ui/streamlit_app.py`，1024 行）：单页 7 个 tab —
`概览 / 精选 / 股票池 / ETF / 基本面 / 覆盖 / 标签`，侧边栏过滤（市场/类型/板块/风险/分数/决策）。

随重构与潜力扫描落地，按以下方向优化：

### 12.1 tab 重组

| tab | 变化 | 内容 |
| --- | --- | --- |
| 概览 | 调整 | 市场温度 + 各市场覆盖 + 决策分布；置顶 A/HK/US 切换 |
| 股票精选 | 保留 | expert/refine 候选（现有） |
| **潜力扫描** | 🆕 新增 | 潜力候选表 + 支柱子分雷达 + **情景卡**（触发/目标/止损/时间止损/RR）+ 历史胜率 |
| ETF 工具池 | 改造 | **两表并存**：①分类规模总览 ②双层去重精选（含 `簇` / `同组数 peer_count` / `peer_alternatives` 列）；可展开看簇内成员 |
| 基本面 / 覆盖 / 标签 | 保留 | 现有 |

### 12.2 关键交互

- **市场切换一等化**：A/HK/US 作为全局切换（含 US ETF），各 tab 响应；为美股扩展铺路。
- **资产类别视图分离**：股票看"潜力/精选"，ETF 看"工具池去重"，技术指标列两者口径一致（统一技术引擎的直接体现）。
- **情景卡组件**：潜力 tab 中点选个股 → 右栏渲染情景卡（pivot 价位、目标、止损、时间止损日、风险回报比、历史胜率分布）。
- **去重可见性**：ETF 精选表保留 `peer_count` 与簇标注，做到"同质化既被折叠、又可追溯"（呼应决策 #2）。

### 12.3 工程

- 看板数据读取走与报告同一套服务函数（避免 UI 重复实现去重/打分逻辑）。
- 排在对应后端阶段之后：ETF 两表（随阶段一）、潜力 tab（随潜力扫描落地）、市场切换（随阶段四美股）。
