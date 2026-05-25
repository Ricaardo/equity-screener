# TODO 跟踪

更新时间：2026-05-24

本文件记录当前项目后续事项。已完成的能力不再作为待办推进，除非后续数据验证发现回归。

## 已完成

- 数据源方案：A/H/US 股票和 ETF 行情、主数据、历史、基准和 A 股板块优先走本地 Futu OpenD，不可用或不覆盖时回退免费/公开源。
- 本地 DuckDB 存储：行情快照、日线、标签、三表财务指标、技术指标、专家筛选结果和同类提炼结果。
- A 股市场细分：主板、创业板、科创板、北交所、ST/退市风险、ETF。
- 港股细分：港股通标识、普通港股、风险状态。
- 专家模型：结合中国投资大师框架、行业适配、热门主题、基本面、技术指标、流动性和风险约束。
- 三表基本面：ROE、现金流、负债、成长、稳定性、研发费用率、资本开支效率等指标已接入。
- 同类去重提炼：按主题桶、风格桶、行业组和 A/H 同主体提炼最优候选。
- ETF 工具池：宽基、行业、主题、跨境、债券、商品、货币分类和工具评分。
- React 看板：摘要优先的只读日报，包含今日摘要、优先研究、ETF工具箱、潜力情景、证据附录；Streamlit 保留为备用入口。
- 定时更新：`ah-screener update-all` 和 `ah-screener install-schedule` 已可用。
- 回测增强：支持 snapshot/monthly/quarterly 调仓、行业分散约束、手续费、滑点和 A/H 免费指数基准对比。
- 严格/回放回测区分：`refined_candidates` 记录 `snapshot_source` 和 `is_replay`，`backtest` 默认排除历史回放快照；`--include-replay` 只用于诊断。
- 回测证据偏差审计：新增 `potential-walk-forward`，RS 阈值样本外验证与 in-sample sweep 分离。
- 日期化 active universe：新增 `security_universe_snapshots`，从后续 spot 同步开始记录每天证券池、资产类型、板块和状态。
- 主题权重去评分化：外部背景和主题匹配只保留为上下文、解释和分桶，不再直接推高专家综合分。
- 部署文档：新增 [deployment.md](deployment.md)，记录 macOS LaunchAgent、依赖、验证、回测口径和回滚方式。
- GitHub 数据发布：DuckDB 数据库已通过 GitHub Release 上传。

## P0：回测确认

- [x] 等第二个及以上 `refined_candidates` 快照生成后，跑真实库回测并确认输出表。
  - 结果：新增 `backfill-refined-snapshots`，可基于已存真实日线生成历史回放候选快照；历史回放链路可输出诊断回测。
  - 命令：`ah-screener backtest --rebalance quarterly --industry-neutral --fee-bps 5 --slippage-bps 10 --benchmark A:000300`
  - 严格口径：默认只使用定时任务/手动自然生成的候选快照；历史回放需显式 `--include-replay`。
  - 当前验证：自然快照只有 2026-05-23 和 2026-05-24，价格历史最新到 2026-05-22，严格口径暂无可回测收益行。历史回放诊断最终权益约 2,810,621，但不作为 edge 证明。
- [x] 确认自动刷新日志和 DuckDB 写锁情况。
  - 结果：LaunchAgent 已运行；`logs/` 中未见 DuckDB 写锁异常，主要为 AkShare 进度条输出。

## P1：研究质量增强

- [x] 扩展更长历史样本。
  - 结果：A/H 股票和常用 A/H 基准指数已回填到约 3 年窗口，起始日期为 2023-05-15。
  - 约束：后续继续使用免费数据源，遇到接口限流时分批同步。
- [x] 扩展港股主题标签的可验证来源。
  - 目标：减少港股只依赖名称和少量策展标签造成的主题覆盖不足。
  - 结果：新增 `sync-hkex-documents` 和 `ingest-document`，支持从 HKEXnews 自动搜索/下载 PDF，也支持本地 PDF 或文本，抽取业务结构、研发投入、客户集中度、审计意见、风险提示，并把主题证据写入 `company_tags`。
- [x] 扩展行业估值分位和细分行业口径。
  - 目标：同类公司比较更贴近 A/H 行业结构，例如半导体、创新药、互联网平台、高股息央国企。
  - 结果：专家模型新增 `detailed_industry` 和 `valuation_percentile`，新增 `industry_valuation_stats` 统计表与 `industry-valuation-stats` 命令，并提供 `import-industry-map` 读取可编辑 CSV 行业映射。
- [x] 增强年报/公告 PDF 解析。
  - 目标：从官方公告中提取业务结构、研发投入、客户集中度、审计意见和风险提示。
  - 结果：新增 `company_documents`、`document_extractions` 和 PDF/TXT/MD 解析流程，PDF 优先使用 `pdfplumber`，备用 `pypdf`；额外识别频繁合股/供股/配股、延迟刊发财报和异常审计意见，并写入风险标签。

## P2：市场扩展

- [x] 接入美股市场。
  - 免费数据源：Futu/OpenD（本机优先）、SEC EDGAR、Nasdaq Trader 和 AKShare。
  - 结果：新增 `market = US`，证券目录和行情优先使用 Futu/OpenD、失败回退 Nasdaq Trader/AKShare，美股基本面使用 SEC EDGAR Company Facts，支持美元财报字段和美股 ETF 识别；`sync-us-batch` 可按证券目录分页同步。
- [x] 增加跨市场同主体映射。
  - 目标：A/H/US 多地上市公司只保留更优交易标的，或在报告中合并展示。
  - 结果：新增 `company_identity_mappings` 表和 `sync-identity-mappings` 命令，`refined_candidates` 优先按 `canonical_id` 去重。

## P3：工程自动化

- [x] 将数据库 Release 上传脚本化。
  - 命令：`scripts/upload_release_db.sh data-YYYY-MM-DD`
- [x] 增加最小化测试套件。
  - 覆盖：基准回测、同类去重、ETF 分类、基本面评分边界。
- [x] 增加 UI 截图回归。
  - 命令：`scripts/check_ui_screenshots.sh http://127.0.0.1:5173`
  - 目标：防止顶部留白、HTML 转义、关键页面空白等视觉问题回归。

## 当前使用提醒

- 筛选结果是研究辅助，不构成投资建议。
- 严格点时真实回测仍依赖未来定时任务自然积累；`backfill-refined-snapshots` 是基于真实日线的历史回放，用于先验证模型和回测链路。严格口径使用默认 `backtest`；诊断历史回放时才显式传 `--include-replay`。
- `potential-sweep` 是 in-sample 参数敏感度；RS 阈值证据以 `potential-walk-forward` 样本外结果为准。
- 免费数据源存在限流、字段变化和短期不可用风险，定时任务失败时优先查看 `logs/`。

## 后续 backlog（2026-05-24，PR #1/#2 合并后）

P0 是本机/浏览器操作（需 Futu OpenD + 真实浏览器），不在仓库代码范围；其余为代码项，逐条推进。

### P0 · 本机运营验证（用户侧，非代码）
- [x] 本机同日全市场 `update-all --top 150`（OpenD 起着）——已完成全量流程；本轮修复 HK 现货 Futu fallback 与 HK ETF 重复快照去重，A 股现货遇 AKShare 抖动后单独重试成功。
- [x] 浏览器目视看板（结论卡 / ETF 两视图 / 潜力 tab / 情景卡）——已用 `browser-use` 截图验证，输出到 `reports/ui-screenshots/`。

### P1 · 高价值
- [x] HK ETF 经 Futu OpenD 取现货 universe（`get_stock_basicinfo(HK, ETF)` + 快照），OpenD 不可用时回退 AKShare；字段映射已用本机 OpenD 实测确认。
- [x] 潜力 tab 渲染情景卡（selectbox 选标的 → 展开 scenario_json：触发/目标/止损/时间止损/RR/历史胜率）。
- [x] 多市场同日守卫：`market_date_health` 在报告 §3.1 列各市场最新日期，分歧 >3 天告警。

### P2 · 完整性/质量
- [x] CI：GitHub Actions 跑 ruff + pytest。
- [x] SEC ticker 表缓存（`@lru_cache`）。companyfacts 为每标的一次性，缓存收益低+内存风险，未做。
- [x] 回测证据口径修正：默认排除 replay，新增 `potential-walk-forward`，报告输出偏差控制和快照来源。
- [x] 日期化 active universe：`sync-spot`/US spot 写入 `security_universe_snapshots`，为后续自然验证提供当日证券池基线。
- [x] 外部背景/主题去权重化：报告外部背景明确不计入评分；专家模型中 `theme_score` 仅作为参考字段和分桶依据。
- [x] A 股退市生命周期：`sync-delisted-universe` 接入上交所/深交所退市记录并写入 `security_lifecycle_events`。
- [x] HK/US 退市/摘牌历史 universe：`sync-delisted-universe` 已聚合 A 股、HKEX 官方摘牌名单和 Alpha Vantage US delisted CSV（`AH_SCREENER_ALPHA_VANTAGE_KEY` 存在时启用；无 key 时不中断刷新）。
- [x] 潜力"题材早期"支柱：作为明确产品决策归档为中性占位；没有题材内 peer 动量和前瞻 edge 验证前不进入生产评分，避免半成品噪音。
- [x] point-in-time 财务（防前视 R1）：已提供 `point_in_time.py` 的 as-of 财务打分索引与测试；历史验证继续优先使用 price-only，基本面进入验证时必须走该索引。

### P3 · 锦上添花
- [x] `sync_a_tags` 增量（板块成员 <max_age_days 跳过）。
- [x] 看板数据新鲜度角标（hero 下方各市场最新快照 + 分歧告警）。
- [x] 回测用多市场新数据重跑：归为运营动作；代码侧已默认隔离 replay，等待自然快照积累后用固定参数 forward validation。
- [x] 规则 YAML 热加载：明确不做；规则已外置 JSON，改后重启即可，继续加热加载收益低且增加状态复杂度。

### 完成小结（2026-05-24 本轮）
已实现 A/H/US OpenD 优先数据源、HK ETF Futu、A 股北交所现货补齐、A 股行业/概念 OpenD 标签、HK 现货 Futu fallback、情景卡、同日守卫、CI、sync_a_tags 增量、新鲜度角标、部署文档、默认自然快照回测、RS walk-forward、日期化 active universe、主题去权重化、A/HK/US lifecycle 入库、本机 UI 截图验证。
当前没有未勾选代码 TODO；剩余是每日自然快照继续积累和外部免费数据源可用性监控。
