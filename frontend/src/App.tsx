import {
  Activity,
  CalendarClock,
  CheckCircle2,
  CircleAlert,
  ClipboardCheck,
  Clock,
  Filter,
  Flame,
  LineChart,
  RefreshCcw,
  Target,
  TriangleAlert,
  WalletCards,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { fetchLatestReport, fetchUsPremarket } from "./api";
import type {
  Candidate,
  EtfLeader,
  EtfUseCase,
  HotTheme,
  Market,
  PotentialCandidate,
  ScreeningReport,
  UsCandidate,
  UsPremarketReport
} from "./types";

const marketLabels: Record<Market, string> = {
  A: "A股",
  HK: "港股",
  US: "美股"
};

const markets: Market[] = ["A", "HK", "US"];

const reasonLabels: Record<string, string> = {
  non_stock_asset: "非股票",
  price_missing: "价格缺失",
  amount_missing: "成交额缺失",
  low_amount: "成交额不足",
  market_cap_missing: "市值缺失",
  low_market_cap: "市值偏小",
  distress_name: "风险名称",
  a_st_or_delisting_name: "ST/退市",
  a_excluded_board: "非推荐板块",
  hk_penny: "港股仙股",
  hk_non_connect_illiquid: "非港股通低流动性",
  us_low_price: "美股低价",
  us_shell_structure: "SPAC/权证/unit"
};

type Recommendation = {
  id: string;
  market: Market;
  symbol: string;
  name: string;
  actionLabel: string;
  stance: string;
  score?: number | null;
  amount?: number | null;
  marketCap?: number | null;
  tags: string[];
  reasons: string[];
  risks: string[];
  checks: string[];
  invalidIf?: string;
};

type ActionItem = {
  id: string;
  kind: "priority" | "watch" | "risk" | "tool";
  label: string;
  title: string;
  meta?: string;
  score?: number | null;
};

function fmtScore(value?: number | null): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "--";
}

function fmtNum(value?: number | null, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "--";
}

function fmtAmount(value?: number | null, market?: Market): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  if (market === "US") {
    if (Math.abs(value) >= 1_000_000_000_000) return `$${(value / 1_000_000_000_000).toFixed(2)}T`;
    if (Math.abs(value) >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
    if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
    return `$${value.toFixed(0)}`;
  }
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(1)}万`;
  return value.toFixed(0);
}

// US security names ship with " - Common Stock" / "Class A Common Stock" tails.
function cleanName(name: string): string {
  return name
    .replace(/\s*[-–]?\s*(Class\s+[A-Z]\s+)?Common Stock\s*$/i, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function listText(items?: string[], fallback = "暂无"): string {
  return items && items.length > 0 ? items.slice(0, 3).join("；") : fallback;
}

function byScoreDesc<T>(field: keyof T) {
  return (a: T, b: T) => Number(b[field] ?? 0) - Number(a[field] ?? 0);
}

function reasonLabel(reason: string): string {
  return reasonLabels[reason] || reason;
}

function summarizeReasons(summary?: Record<string, number>): string {
  const entries = Object.entries(summary || {}).filter(([, value]) => value > 0);
  if (entries.length === 0) return "推荐池未触发额外过滤";
  return entries
    .slice(0, 4)
    .map(([key, value]) => `${reasonLabel(key)} ${value}`)
    .join(" / ");
}

// Data freshness powers the trust signal in the top bar. A daily pipeline run
// should refresh within ~a day; older than that means a run was likely missed.
const STALE_HOURS = 28;
const VERY_STALE_HOURS = 52;

type Freshness = { text: string; tone: "fresh" | "warn" | "stale"; exact: string };

function freshnessFrom(generatedAt?: string): Freshness {
  const then = generatedAt ? new Date(generatedAt).getTime() : NaN;
  if (!Number.isFinite(then)) {
    return { text: "更新时间未知", tone: "warn", exact: "无生成时间" };
  }
  const exact = new Date(then).toLocaleString("zh-CN", { hour12: false });
  const minutes = Math.max(0, Math.round((Date.now() - then) / 60_000));
  const hours = minutes / 60;
  let text: string;
  if (minutes < 1) text = "刚刚更新";
  else if (minutes < 60) text = `${minutes} 分钟前更新`;
  else if (hours < 24) text = `${Math.round(hours)} 小时前更新`;
  else text = `${Math.floor(hours / 24)} 天前更新`;
  const tone: Freshness["tone"] = hours <= STALE_HOURS ? "fresh" : hours <= VERY_STALE_HOURS ? "warn" : "stale";
  return { text, tone, exact: `生成于 ${exact}` };
}

function actionForCandidate(candidate: Candidate, index: number): string {
  const score = Number(candidate.expert_score ?? 0);
  if (index === 0 || score >= 74) return "优先验证";
  if (score >= 66) return "重点观察";
  if (Number(candidate.technical_score ?? 0) < 55) return "等待技术确认";
  return "观察候选";
}

function actionForUsCandidate(candidate: UsCandidate, index: number): string {
  if (candidate.decision === "core_candidate" || index < 3) return "盘前优先验证";
  if (Number(candidate.technical_score ?? 0) >= 65) return "趋势观察";
  return "等待确认";
}

function recommendationFromCandidate(candidate: Candidate, index: number): Recommendation {
  const tags = [
    candidate.style_bucket,
    candidate.detailed_industry,
    ...(candidate.theme_matches || [])
  ].filter(Boolean) as string[];
  return {
    id: `${candidate.market}-${candidate.symbol}`,
    market: candidate.market,
    symbol: candidate.symbol,
    name: cleanName(candidate.name),
    actionLabel: actionForCandidate(candidate, index),
    stance: candidate.selection_note || candidate.bucket || "提炼候选",
    score: candidate.expert_score,
    amount: candidate.amount,
    marketCap: candidate.market_cap,
    tags: tags.slice(0, 4),
    reasons: candidate.why_selected?.slice(0, 3) || ["进入推荐级候选池"],
    risks: candidate.key_risks?.slice(0, 2) || ["需结合仓位和交易计划核验"],
    checks: candidate.verify_before_action?.slice(0, 3) || ["确认量价和基本面数据未明显恶化"],
    invalidIf: candidate.invalid_if
  };
}

function recommendationFromUsCandidate(candidate: UsCandidate, index: number): Recommendation {
  const reasons = (candidate.reasons_list || [])
    .filter((item) => !item.startsWith("filtered:") && !item.startsWith("recommendation_filtered:"))
    .slice(0, 3);
  const risks = [
    typeof candidate.short_ratio === "number" && candidate.short_ratio >= 0.5
      ? `做空比 ${fmtNum(candidate.short_ratio)}，盘前波动可能放大`
      : "",
    candidate.peg != null && candidate.peg > 2.5 ? `PEG ${fmtNum(candidate.peg)}，估值弹性需核验` : ""
  ].filter(Boolean);
  return {
    id: `US-${candidate.symbol}`,
    market: "US",
    symbol: candidate.symbol,
    name: cleanName(candidate.name),
    actionLabel: actionForUsCandidate(candidate, index),
    stance: candidate.decision || "recommendable",
    score: candidate.expert_score,
    amount: candidate.amount,
    marketCap: candidate.market_cap,
    tags: (candidate.concept_boards || []).slice(0, 4),
    reasons: reasons.length > 0 ? reasons : ["通过美股推荐池市值、流动性和低价股过滤"],
    risks: risks.length > 0 ? risks : ["盘前方向需等待成交额和指数环境确认"],
    checks: ["确认盘前成交额延续", "检查财报窗口和隔夜消息", "观察主题热度是否扩散"],
    invalidIf: "指数环境转弱或盘前放量下跌，先撤回到观察池。"
  };
}

function actionItemsFromRecommendations(items: Recommendation[]): ActionItem[] {
  return items.slice(0, 6).map((item, index) => ({
    id: item.id,
    kind: index < 3 ? "priority" : "watch",
    label: item.actionLabel,
    title: `${item.market} ${item.symbol} ${item.name}`,
    meta: item.reasons[0],
    score: item.score
  }));
}

function DataFreshness({
  market,
  dataDate,
  generatedAt,
  warning
}: {
  market: Market;
  dataDate: string;
  generatedAt?: string;
  warning?: string | null;
}) {
  const fresh = freshnessFrom(generatedAt);
  return (
    <div className={`freshness freshness--${fresh.tone}`} title={fresh.exact}>
      <span className="freshness__data">
        <Clock size={13} />
        {marketLabels[market]}数据 {dataDate}
      </span>
      <b>{fresh.text}</b>
      {warning ? (
        <em className="freshness__warn">
          <TriangleAlert size={12} />
          {warning}
        </em>
      ) : null}
    </div>
  );
}

function App() {
  const [report, setReport] = useState<ScreeningReport | null>(null);
  const [us, setUs] = useState<UsPremarketReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeMarket, setActiveMarket] = useState<Market>("A");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [nextReport, nextUs] = await Promise.all([fetchLatestReport(), fetchUsPremarket()]);
      setReport(nextReport);
      setUs(nextUs);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (loading) {
    return (
      <main className="page page--center">
        <div className="loading-card">
          <Activity className="spin" size={24} />
          <span>正在读取最新报告</span>
        </div>
      </main>
    );
  }

  if (error || !report) {
    return (
      <main className="page page--center">
        <div className="loading-card loading-card--error">
          <CircleAlert size={24} />
          <span>{error || "无法读取报告"}</span>
          <button className="button" onClick={() => void load()}>
            重新读取
          </button>
        </div>
      </main>
    );
  }

  const counts: Record<Market, number> = {
    A: report.counts.refined_by_market.A ?? 0,
    HK: report.counts.refined_by_market.HK ?? 0,
    US: us?.top_candidates.length ?? 0
  };

  const marketDate = (market: Market): string =>
    market === "US"
      ? us?.report_date ?? "—"
      : report.data_freshness.find((f) => f.market === market)?.latest_date ?? report.report_date;

  const activeGeneratedAt = activeMarket === "US" ? us?.generated_at : report.generated_at;
  const freshnessWarning = activeMarket === "US" ? null : report.data_freshness_warning;

  return (
    <main className="page">
      <header className="topbar">
        <div className="topbar__brand">
          <div className="eyebrow">A/H/US daily brief</div>
          <strong>今日建议</strong>
          <DataFreshness
            market={activeMarket}
            dataDate={marketDate(activeMarket)}
            generatedAt={activeGeneratedAt}
            warning={freshnessWarning}
          />
        </div>
        <nav className="market-tabs" aria-label="市场">
          {markets.map((market) => (
            <button
              key={market}
              aria-pressed={activeMarket === market}
              className={activeMarket === market ? "is-active" : ""}
              onClick={() => setActiveMarket(market)}
            >
              {marketLabels[market]}
              <b>{counts[market]}</b>
            </button>
          ))}
        </nav>
        <button className="button button--ghost topbar__refresh" onClick={() => void load()}>
          <RefreshCcw size={16} />
          重新读取
        </button>
      </header>

      {activeMarket === "US" ? (
        <UsMarketView us={us} />
      ) : (
        <AhMarketView report={report} market={activeMarket} />
      )}

      <footer className="page-foot">{report.disclaimer}</footer>
    </main>
  );
}

function AhMarketView({ report, market }: { report: ScreeningReport; market: Market }) {
  const stocks = useMemo(
    () =>
      report.refined_candidates
        .filter((item) => item.market === market)
        .sort(byScoreDesc<Candidate>("expert_score")),
    [report, market]
  );
  const priority = useMemo(() => {
    const fromBrief = report.daily_brief.priority_candidates
      .filter((item) => item.market === market)
      .sort(byScoreDesc<Candidate>("expert_score"));
    return (fromBrief.length > 0 ? fromBrief : stocks).slice(0, 6);
  }, [market, report.daily_brief.priority_candidates, stocks]);
  const recommendations = useMemo(
    () => priority.map((item, index) => recommendationFromCandidate(item, index)),
    [priority]
  );
  const actionItems = actionItemsFromRecommendations(recommendations);
  const potential = report.daily_brief.potential_setups.filter((item) => item.market === market);
  const stockGroups = useMemo(() => groupByKeep(stocks, (c) => c.bucket || "未匹配主题"), [stocks]);

  const useCaseMeta = useMemo(() => {
    const map = new Map<string, EtfUseCase>();
    report.etf_use_cases.forEach((uc) => map.set(uc.key, uc));
    return map;
  }, [report]);

  const etfGroups = useMemo(() => {
    const marketEtfs = report.etf_leaders.filter((item) => item.market === market);
    return groupByKeep(marketEtfs, (e) => e.use_case || "other_tools");
  }, [report, market]);

  return (
    <div className="content">
      <DecisionHero
        title={`${marketLabels[market]}今日判断`}
        headline={report.daily_brief.headline}
        focus={report.daily_brief.focus}
        stats={[
          ["建议候选", String(recommendations.length)],
          ["候选池", String(stocks.length)],
          ["ETF 工具", String(report.etf_leaders.filter((e) => e.market === market).length)]
        ]}
        filterSummary={summarizeReasons(report.daily_brief.data_health.investability_summary)}
      />

      <ActionQueue items={actionItems} />

      <section className="block">
        <BlockTitle icon={Target} title="建议卡" meta={`${recommendations.length} 只 · 先核验再行动`} />
        {recommendations.length === 0 ? (
          <EmptyState title="暂无建议候选" description="推荐级市值、流动性和风险过滤后暂无标的。" />
        ) : (
          <div className="recommendation-grid">
            {recommendations.map((item) => (
              <RecommendationCard item={item} key={item.id} />
            ))}
          </div>
        )}
      </section>

      {potential.length > 0 ? (
        <section className="block">
          <BlockTitle icon={ClipboardCheck} title="等待触发" meta={`${potential.length} 只 · 不进入当前建议`} />
          <div className="watch-list">
            {potential.slice(0, 5).map((item) => (
              <PotentialRow item={item} key={`${item.market}-${item.symbol}`} />
            ))}
          </div>
        </section>
      ) : null}

      <section className="block">
        <BlockTitle icon={WalletCards} title="ETF 工具箱" meta="用于替代个股或表达方向" />
        {etfGroups.length === 0 ? (
          <EmptyState title="该市场暂无 ETF 工具" description="ETF 池当前未覆盖此市场。" />
        ) : (
          etfGroups.map(([key, items]) => {
            const meta = useCaseMeta.get(key);
            return (
              <div className="group" key={key}>
                <h3 className="group__title">
                  {meta?.title || key}
                  <span>{items.length}</span>
                  {meta?.description ? <em>{meta.description}</em> : null}
                </h3>
                <div className="tool-grid">
                  {items.map((e) => (
                    <EtfCard etf={e} key={`${e.symbol}-${key}`} />
                  ))}
                </div>
              </div>
            );
          })
        )}
      </section>

      <details className="evidence-block">
        <summary>候选池明细 / 证据链</summary>
        <section className="block evidence-block__body">
          {stockGroups.map(([bucket, items]) => (
            <div className="group" key={bucket}>
              <h3 className="group__title">
                {bucket}
                <span>{items.length}</span>
              </h3>
              <div className="card-grid">
                {items.map((c) => (
                  <CandidateCard candidate={c} key={`${c.symbol}-${c.rank_in_bucket ?? ""}`} />
                ))}
              </div>
            </div>
          ))}
        </section>
      </details>
    </div>
  );
}

function UsMarketView({ us }: { us: UsPremarketReport | null }) {
  if (!us) {
    return (
      <div className="content">
        <EmptyState title="未读取到美股盘前报告" description="请先运行美股筛选管线生成 us-premarket-latest.json。" />
      </div>
    );
  }

  const candidates = [...us.top_candidates].sort(byScoreDesc<UsCandidate>("expert_score"));
  const recommendations = candidates
    .slice(0, 8)
    .map((candidate, index) => recommendationFromUsCandidate(candidate, index));
  const themes = [...us.hot_themes].sort(byScoreDesc<HotTheme>("momentum_score"));

  return (
    <div className="content">
      <DecisionHero
        title="美股盘前判断"
        headline={us.macro_context.summary || "等待盘前量价确认。"}
        focus={`市场分 ${fmtNum(us.macro_context.market_score, 0)} · ${us.macro_context.regime || "neutral"}`}
        stats={[
          ["推荐候选", String(us.counts.recommendable ?? candidates.length)],
          ["基础通过", String(us.counts.candidates ?? 0)],
          ["已过滤", String(us.counts.filtered ?? 0)]
        ]}
        filterSummary={summarizeReasons(us.recommendation_filtered_summary)}
      />

      <ActionQueue items={actionItemsFromRecommendations(recommendations)} />

      {themes.length > 0 ? (
        <section className="block">
          <BlockTitle icon={Flame} title="热门主题" meta={`${themes.length} 个 · 只作方向背景`} />
          <div className="theme-grid">
            {themes.map((t) => (
              <ThemeCard theme={t} key={t.board} />
            ))}
          </div>
        </section>
      ) : null}

      <section className="block">
        <BlockTitle icon={LineChart} title="建议卡" meta={`${recommendations.length} 只 · 已去除小盘/壳股/低流动性`} />
        {recommendations.length === 0 ? (
          <EmptyState title="暂无美股建议候选" description="推荐级市值、成交额和低价股过滤后无入选标的。" />
        ) : (
          <div className="recommendation-grid">
            {recommendations.map((item) => (
              <RecommendationCard item={item} key={item.id} />
            ))}
          </div>
        )}
      </section>

      {(us.squeeze_watch.length > 0 || us.earnings_soon.length > 0) && (
        <section className="block secondary-grid">
          {us.squeeze_watch.length > 0 ? (
            <div className="mini-panel">
              <BlockTitle icon={Zap} title="轧空观察" meta={`${us.squeeze_watch.length} 只`} />
              <ul className="mini-list">
                {us.squeeze_watch.map((s) => (
                  <li key={s.symbol}>
                    <strong>{s.symbol}</strong>
                    <span>做空比 {fmtNum(s.short_ratio)} · RS {fmtScore(s.rs_score)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {us.earnings_soon.length > 0 ? (
            <div className="mini-panel">
              <BlockTitle icon={CalendarClock} title="临近财报" meta={`${us.earnings_soon.length} 只`} />
              <ul className="mini-list">
                {us.earnings_soon.map((e) => (
                  <li key={e.symbol}>
                    <strong>{e.symbol}</strong>
                    <span>
                      {e.earnings_date}
                      {typeof e.in_days === "number" ? ` · ${e.in_days} 天后` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      )}
    </div>
  );
}

function DecisionHero({
  title,
  headline,
  focus,
  stats,
  filterSummary
}: {
  title: string;
  headline: string;
  focus: string;
  stats: Array<[string, string]>;
  filterSummary: string;
}) {
  return (
    <section className="decision-hero">
      <div className="decision-hero__main">
        <div className="eyebrow">decision board</div>
        <h1>{title}</h1>
        <p>{headline}</p>
        <span>{focus}</span>
      </div>
      <div className="decision-hero__side">
        <div className="stat-strip">
          {stats.map(([label, value]) => (
            <div className="stat" key={label}>
              <strong>{value}</strong>
              <span>{label}</span>
            </div>
          ))}
        </div>
        <p className="filter-note">
          <Filter size={15} />
          {filterSummary}
        </p>
      </div>
    </section>
  );
}

function ActionQueue({ items }: { items: ActionItem[] }) {
  return (
    <section className="block">
      <BlockTitle icon={CheckCircle2} title="行动队列" meta="从上到下处理" />
      {items.length === 0 ? (
        <EmptyState title="暂无行动项" description="当前市场没有通过推荐门槛的候选。" />
      ) : (
        <div className="action-list">
          {items.map((item, index) => (
            <article className={`action-row action-row--${item.kind}`} key={item.id}>
              <span className="action-row__head">
                <i className="action-row__rank">{index + 1}</i>
                {item.label}
              </span>
              <strong>{item.title}</strong>
              <p>{item.meta || "等待进一步核验"}</p>
              <b>{fmtScore(item.score)}</b>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function RecommendationCard({ item }: { item: Recommendation }) {
  return (
    <article className="recommendation-card">
      <header className="recommendation-card__head">
        <div>
          <span>{item.actionLabel}</span>
          <strong>{item.name}</strong>
          <em>
            {marketLabels[item.market]} {item.symbol} · {item.stance}
          </em>
        </div>
        <b>{fmtScore(item.score)}</b>
      </header>

      <div className="chips">
        {item.tags.slice(0, 3).map((tag) => (
          <span className="chip--accent" key={tag}>
            {tag}
          </span>
        ))}
        <span>市值 {fmtAmount(item.marketCap, item.market)}</span>
        <span>成交额 {fmtAmount(item.amount, item.market)}</span>
      </div>

      <SignalLine icon="因" text={item.reasons[0]} />
      <SignalLine icon="核" text={item.checks[0]} />
      <SignalLine icon="险" text={item.risks[0]} tone="risk" />

      <details className="card-more">
        <summary>完整证据</summary>
        <div className="card-more__body">
          <Mini label="为什么进入建议" lines={item.reasons} />
          <Mini label="买前核验" lines={item.checks} />
          <Mini label="主要风险" lines={item.risks} tone="risk" />
          {item.invalidIf ? (
            <p className="card-line card-line--risk">
              <i>失</i>
              {item.invalidIf}
            </p>
          ) : null}
        </div>
      </details>
    </article>
  );
}

function CandidateCard({ candidate }: { candidate: Candidate }) {
  return (
    <article className="card card--stock">
      <header className="card-head">
        <div>
          <strong>{cleanName(candidate.name)}</strong>
          <span>
            {marketLabels[candidate.market]} {candidate.symbol} · {candidate.style_bucket || candidate.detailed_industry || "候选"}
          </span>
        </div>
        <b>{fmtScore(candidate.expert_score)}</b>
      </header>

      <div className="chips">
        <span>{candidate.trading_system}</span>
        <span>基本面 {fmtScore(candidate.fundamental_score)}</span>
        <span>技术 {fmtScore(candidate.technical_score)}</span>
        <span>市值 {fmtAmount(candidate.market_cap, candidate.market)}</span>
      </div>

      <SignalLine icon="选" text={candidate.why_selected?.[0] || "—"} />
      <SignalLine icon="险" text={candidate.key_risks?.[0] || "—"} tone="risk" />

      <details className="card-more">
        <summary>明细 / 证据链</summary>
        <div className="card-more__body">
          <Mini label="入选理由" lines={candidate.why_selected} />
          <Mini label="主要风险" lines={candidate.key_risks} tone="risk" />
          <Mini label="买前核验" lines={candidate.verify_before_action} />
          {candidate.invalid_if ? (
            <p className="card-line card-line--risk">
              <i>失</i>
              {candidate.invalid_if}
            </p>
          ) : null}
          {candidate.reasons && candidate.reasons.length > 0 ? (
            <Mini label="打分依据" lines={candidate.reasons.slice(0, 14)} />
          ) : null}
        </div>
      </details>
    </article>
  );
}

function PotentialRow({ item }: { item: PotentialCandidate }) {
  return (
    <article className="watch-row">
      <div>
        <strong>{cleanName(item.name)}</strong>
        <span>
          {marketLabels[item.market]} {item.symbol} · {item.setup_note || item.bias_note || "等待触发"}
        </span>
      </div>
      <p>
        触发 {fmtNum(item.pivot_price)} · 目标 {fmtNum(item.target_price)} · 止损 {fmtNum(item.stop_price)}
      </p>
      <b>{fmtScore(item.potential_score)}</b>
    </article>
  );
}

function EtfCard({ etf }: { etf: EtfLeader }) {
  return (
    <article className="card card--etf">
      <header className="card-head">
        <div>
          <strong>{etf.name}</strong>
          <span>
            {marketLabels[etf.market]} {etf.symbol} · {etf.etf_cluster || etf.etf_category}
          </span>
        </div>
        <b>{fmtScore(etf.etf_score)}</b>
      </header>

      <div className="chips">
        <span>{etf.trading_system}</span>
        <span>{etf.etf_track || etf.etf_category}</span>
        <span>同组 {etf.peer_count ?? 1}</span>
        <span>成交额 {fmtAmount(etf.amount, etf.market)}</span>
      </div>

      <SignalLine icon="用" text={etf.why_selected?.[0] || "—"} />
      <SignalLine icon="替" text={listText(etf.alternatives)} tone="muted" />
      {etf.caution ? <SignalLine icon="注" text={etf.caution} tone="risk" /> : null}
    </article>
  );
}

function ThemeCard({ theme }: { theme: HotTheme }) {
  return (
    <article className="theme-card">
      <header>
        <strong>{theme.board}</strong>
        <b>{fmtScore(theme.momentum_score)}</b>
      </header>
      <div className="chips">
        <span>成员 {theme.members ?? "--"}</span>
        <span>领涨占比 {typeof theme.leaders_pct === "number" ? `${theme.leaders_pct.toFixed(0)}%` : "--"}</span>
      </div>
    </article>
  );
}

function SignalLine({ icon, text, tone }: { icon: string; text: string; tone?: "risk" | "muted" }) {
  return (
    <p className={`card-line ${tone === "risk" ? "card-line--risk" : ""} ${tone === "muted" ? "card-line--muted" : ""}`}>
      <i>{icon}</i>
      {text}
    </p>
  );
}

function BlockTitle({ title, meta, icon: Icon }: { title: string; meta?: string; icon: typeof Activity }) {
  return (
    <div className="block-title">
      <h2>
        <Icon size={18} />
        {title}
      </h2>
      {meta ? <span>{meta}</span> : null}
    </div>
  );
}

function Mini({ label, lines, tone }: { label: string; lines?: string[]; tone?: "risk" }) {
  const content = lines && lines.length > 0 ? lines : ["暂无"];
  return (
    <section className={`mini-section ${tone === "risk" ? "mini-section--risk" : ""}`}>
      <span>{label}</span>
      <ul>
        {content.map((line, index) => (
          <li key={`${index}-${line}`}>{line}</li>
        ))}
      </ul>
    </section>
  );
}

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <section className="empty-state">
      <CircleAlert size={22} />
      <strong>{title}</strong>
      <p>{description}</p>
    </section>
  );
}

function groupByKeep<T>(items: T[], keyOf: (item: T) => string): Array<[string, T[]]> {
  const map = new Map<string, T[]>();
  for (const item of items) {
    const key = keyOf(item);
    const bucket = map.get(key);
    if (bucket) bucket.push(item);
    else map.set(key, [item]);
  }
  return Array.from(map.entries());
}

export default App;
