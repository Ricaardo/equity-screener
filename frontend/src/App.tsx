import {
  Activity,
  CalendarClock,
  CircleAlert,
  Flame,
  Gauge,
  LineChart,
  RefreshCcw,
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
  ScreeningReport,
  UsCandidate,
  UsMacroContext,
  UsPremarketReport
} from "./types";

const marketLabels: Record<Market, string> = {
  A: "A股",
  HK: "港股",
  US: "美股"
};

const markets: Market[] = ["A", "HK", "US"];

function fmtScore(value?: number | null): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "--";
}

function fmtNum(value?: number | null, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "--";
}

function fmtAmount(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(1)}万`;
  return value.toFixed(0);
}

// US security names ship with " - Common Stock" / "Class A Common Stock" tails — strip for display.
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
    market === "US" ? us?.report_date ?? "—" : report.report_date;

  return (
    <main className="page">
      <header className="topbar">
        <div className="topbar__brand">
          <div className="eyebrow">A/H/US 每日筛选</div>
          <strong>今日结果</strong>
          <span>{marketLabels[activeMarket]} · {marketDate(activeMarket)}</span>
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

/* ---------------- A股 / 港股 ---------------- */

function AhMarketView({ report, market }: { report: ScreeningReport; market: Market }) {
  const stocks = useMemo(
    () =>
      report.refined_candidates
        .filter((item) => item.market === market)
        .sort(byScoreDesc<Candidate>("expert_score")),
    [report, market]
  );

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
      <p className="conclusion">{report.daily_brief.headline}</p>

      <section className="block">
        <BlockTitle icon={LineChart} title="股票" meta={`${stocks.length} 只 · 按主题`} />
        {stocks.length === 0 ? (
          <EmptyState title="该市场暂无股票候选" description="重新生成报告后再看。" />
        ) : (
          stockGroups.map(([bucket, items]) => (
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
          ))
        )}
      </section>

      <section className="block">
        <BlockTitle icon={WalletCards} title="ETF" meta={`${report.etf_leaders.filter((e) => e.market === market).length} 只 · 按用途`} />
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
                <div className="card-grid">
                  {items.map((e) => (
                    <EtfCard etf={e} key={`${e.symbol}-${key}`} />
                  ))}
                </div>
              </div>
            );
          })
        )}
      </section>
    </div>
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
        {(candidate.theme_matches || []).slice(0, 2).map((theme) => (
          <span className="chip--accent" key={theme}>
            {theme}
          </span>
        ))}
      </div>

      <p className="card-line">
        <i>选</i>
        {candidate.why_selected?.[0] || "—"}
      </p>
      <p className="card-line card-line--risk">
        <i>险</i>
        {candidate.key_risks?.[0] || "—"}
      </p>

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
        <span>成交额 {fmtAmount(etf.amount)}</span>
      </div>

      <p className="card-line">
        <i>选</i>
        {etf.why_selected?.[0] || "—"}
      </p>
      <p className="card-line card-line--muted">
        <i>替</i>
        {listText(etf.alternatives)}
      </p>
      {etf.caution ? (
        <p className="card-line card-line--risk">
          <i>注</i>
          {etf.caution}
        </p>
      ) : null}
    </article>
  );
}

/* ---------------- 美股 ---------------- */

function UsMarketView({ us }: { us: UsPremarketReport | null }) {
  if (!us) {
    return (
      <div className="content">
        <EmptyState title="未读取到美股盘前报告" description="请先运行美股筛选管线生成 us-premarket-latest.json。" />
      </div>
    );
  }

  const candidates = [...us.top_candidates].sort(byScoreDesc<UsCandidate>("expert_score"));
  const themes = [...us.hot_themes].sort(byScoreDesc<HotTheme>("momentum_score"));

  return (
    <div className="content">
      <UsMacroStrip macro={us.macro_context} />

      {themes.length > 0 ? (
        <section className="block">
          <BlockTitle icon={Flame} title="热门主题" meta={`${themes.length} 个 · 市场在买什么`} />
          <div className="theme-grid">
            {themes.map((t) => (
              <ThemeCard theme={t} key={t.board} />
            ))}
          </div>
        </section>
      ) : null}

      <section className="block">
        <BlockTitle icon={LineChart} title="核心候选" meta={`${candidates.length} 只`} />
        {candidates.length === 0 ? (
          <EmptyState title="暂无核心候选" description="管线过滤后无入选标的。" />
        ) : (
          <div className="card-grid">
            {candidates.map((c) => (
              <UsCandidateCard candidate={c} key={c.symbol} />
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

function UsMacroStrip({ macro }: { macro: UsMacroContext }) {
  const regime = macro.regime || "—";
  const regimeTone = regime === "bullish" || regime === "bearish" ? regime : "neutral";
  return (
    <section className="macro-strip">
      <div className="macro-strip__score">
        <Gauge size={18} />
        <div>
          <span>市场分</span>
          <strong>{fmtNum(macro.market_score, 0)}</strong>
        </div>
        <em className={`regime regime--${regimeTone}`}>{regime}</em>
      </div>
      <p>{macro.summary || macro.policy?.summary || "—"}</p>
    </section>
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

function UsCandidateCard({ candidate }: { candidate: UsCandidate }) {
  return (
    <article className="card card--stock">
      <header className="card-head">
        <div>
          <strong>{cleanName(candidate.name)}</strong>
          <span>
            {marketLabels.US} {candidate.symbol}
            {candidate.concept_boards && candidate.concept_boards.length > 0 ? ` · ${candidate.concept_boards[0]}` : ""}
          </span>
        </div>
        <b>{fmtScore(candidate.expert_score)}</b>
      </header>

      <div className="chips">
        <span>基本面 {fmtScore(candidate.fundamental_score_final)}</span>
        <span>技术 {fmtScore(candidate.technical_score)}</span>
        <span>估值 {fmtScore(candidate.valuation_score)}</span>
        <span className="chip--accent">热度 {fmtScore(candidate.heat_score)}</span>
        <span>RS {fmtScore(candidate.rs_score)}</span>
      </div>

      <div className="kv-row">
        <span>PE {fmtNum(candidate.pe_ttm)}</span>
        <span>PEG {fmtNum(candidate.peg)}</span>
        <span>市值 {fmtAmount(candidate.market_cap)}</span>
        <span>做空比 {fmtNum(candidate.short_ratio)}</span>
      </div>

      {candidate.reasons_list && candidate.reasons_list.length > 0 ? (
        <details className="card-more">
          <summary>打分依据</summary>
          <div className="card-more__body">
            <Mini label="reasons" lines={candidate.reasons_list} />
          </div>
        </details>
      ) : null}
    </article>
  );
}

/* ---------------- 通用小组件 ---------------- */

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

// Group items by a key, preserving first-seen order of keys (and item order within).
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
