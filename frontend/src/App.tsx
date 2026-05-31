import {
  Activity,
  BarChart3,
  BriefcaseBusiness,
  CircleAlert,
  ExternalLink,
  FileText,
  LineChart,
  RefreshCcw,
  Search,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  WalletCards
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { fetchAppendix, fetchLatestReport } from "./api";
import type {
  Candidate,
  CandidateChange,
  EtfLeader,
  EtfUseCase,
  Market,
  PotentialCandidate,
  ScreeningReport,
  TopAction
} from "./types";

type View = "brief" | "stocks" | "etf" | "potential" | "appendix";

const marketLabels: Record<Market, string> = {
  A: "A股",
  HK: "港股",
  US: "美股"
};

const views: Array<{ key: View; label: string; icon: typeof Activity }> = [
  { key: "brief", label: "今日摘要", icon: Activity },
  { key: "stocks", label: "优先研究", icon: BriefcaseBusiness },
  { key: "etf", label: "ETF工具箱", icon: WalletCards },
  { key: "potential", label: "潜力情景", icon: TrendingUp },
  { key: "appendix", label: "证据附录", icon: FileText }
];

function fmtScore(value?: number | null): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "--";
}

function fmtPrice(value?: number | null): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "--";
}

function fmtAmount(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(1)}万`;
  return value.toFixed(0);
}

function fmtDateTime(value: string): string {
  return value.replace("T", " ").slice(0, 19);
}

function strategyLabel(value: string): string {
  if (value.includes("china_masters")) return "大师框架 + 基本面 + 技术 v2";
  return value;
}

function unique<T>(items: T[]): T[] {
  return Array.from(new Set(items));
}

function listText(items?: string[], fallback = "暂无"): string {
  return items && items.length > 0 ? items.slice(0, 3).join("；") : fallback;
}

function byScoreDesc<T>(field: keyof T) {
  return (a: T, b: T) => Number(b[field] ?? 0) - Number(a[field] ?? 0);
}

function App() {
  const [report, setReport] = useState<ScreeningReport | null>(null);
  const [appendix, setAppendix] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeView, setActiveView] = useState<View>("brief");
  const [activeMarket, setActiveMarket] = useState<Market>("A");
  const [minScore, setMinScore] = useState(55);
  const [query, setQuery] = useState("");
  const [selectedEtfUseCases, setSelectedEtfUseCases] = useState<string[]>([]);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [nextReport, nextAppendix] = await Promise.all([fetchLatestReport(), fetchAppendix()]);
      setReport(nextReport);
      setAppendix(nextAppendix);
      setSelectedEtfUseCases(nextReport.etf_use_cases.filter((item) => item.count > 0).map((item) => item.title));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const filteredCandidates = useMemo(() => {
    if (!report) return [];
    const normalizedQuery = query.trim().toLowerCase();
    return report.refined_candidates
      .filter((item) => item.market === activeMarket)
      .filter((item) => (item.expert_score ?? 0) >= minScore)
      .filter((item) => {
        if (!normalizedQuery) return true;
        return [item.symbol, item.name, item.bucket, item.style_bucket, item.detailed_industry]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(normalizedQuery);
      })
      .sort(byScoreDesc<Candidate>("expert_score"));
  }, [activeMarket, minScore, query, report]);

  const filteredPotential = useMemo(() => {
    if (!report) return [];
    return report.potential_candidates
      .filter((item) => item.market === activeMarket)
      .filter((item) => {
        const normalizedQuery = query.trim().toLowerCase();
        return !normalizedQuery || `${item.symbol} ${item.name}`.toLowerCase().includes(normalizedQuery);
      })
      .sort(byScoreDesc<PotentialCandidate>("potential_score"));
  }, [activeMarket, query, report]);

  const etfUseCases = useMemo(() => {
    if (!report) return [];
    return report.etf_use_cases.filter((item) => {
      if (item.count === 0) return false;
      return selectedEtfUseCases.length === 0 || selectedEtfUseCases.includes(item.title);
    });
  }, [report, selectedEtfUseCases]);

  if (loading) {
    return (
      <main className="app-shell app-shell--loading">
        <div className="loading-card">
          <Activity className="spin" size={24} />
          <span>正在读取最新报告</span>
        </div>
      </main>
    );
  }

  if (error || !report) {
    return (
      <main className="app-shell app-shell--loading">
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

  return (
    <main className="app-shell">
      <aside className="sidebar" aria-label="筛选条件">
        <div className="sidebar__brand">
          <div className="eyebrow">A/H/US</div>
          <strong>Daily Brief</strong>
          <span>{report.report_date}</span>
        </div>

        <button className="button button--full" onClick={() => void load()}>
          <RefreshCcw size={16} />
          重新读取报告
        </button>

        <label className="field">
          <span>
            <Search size={15} />
            搜索
          </span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="代码、名称、主题" />
        </label>

        <label className="field">
          <span>专家分下限 {minScore}</span>
          <input min={0} max={100} value={minScore} type="range" onChange={(event) => setMinScore(Number(event.target.value))} />
        </label>

        <section className="filter-group">
          <div className="filter-group__title">ETF 用途</div>
          <div className="stacked-toggles">
            {report.etf_use_cases
              .filter((item) => item.count > 0)
              .map((item) => (
                <button
                  key={item.key}
                  aria-pressed={selectedEtfUseCases.includes(item.title)}
                  className={selectedEtfUseCases.includes(item.title) ? "is-active" : ""}
                  onClick={() =>
                    setSelectedEtfUseCases((current) =>
                      current.includes(item.title) ? current.filter((title) => title !== item.title) : [...current, item.title]
                    )
                  }
                >
                  <span>{item.title}</span>
                  <b>{item.count}</b>
                </button>
              ))}
          </div>
        </section>

        <p className="sidebar__disclaimer">{report.disclaimer}</p>
      </aside>

      <section className="workspace">
        <nav className="market-tabs" aria-label="市场页面">
          {(Object.keys(marketLabels) as Market[]).map((market) => (
            <button
              key={market}
              aria-pressed={activeMarket === market}
              className={activeMarket === market ? "is-active" : ""}
              onClick={() => setActiveMarket(market)}
            >
              {marketLabels[market]}
              <b>{report.counts.refined_by_market?.[market] ?? 0}</b>
            </button>
          ))}
        </nav>

        <Hero report={report} market={activeMarket} />
        <KpiStrip report={report} />

        <nav className="view-tabs" aria-label="主要视图" role="tablist">
          {views.map((view) => {
            const Icon = view.icon;
            return (
              <button
                key={view.key}
                aria-selected={activeView === view.key}
                className={activeView === view.key ? "is-active" : ""}
                role="tab"
                onClick={() => setActiveView(view.key)}
              >
                <Icon size={16} />
                {view.label}
              </button>
            );
          })}
        </nav>

        {activeView === "brief" && (
          <BriefView report={report} candidates={filteredCandidates} onShowStocks={() => setActiveView("stocks")} />
        )}
        {activeView === "stocks" && <StocksView candidates={filteredCandidates} />}
        {activeView === "etf" && <EtfView useCases={etfUseCases} allEtfs={report.etf_leaders} />}
        {activeView === "potential" && <PotentialView candidates={filteredPotential} />}
        {activeView === "appendix" && <AppendixView report={report} appendix={appendix} />}
      </section>
    </main>
  );
}

function Hero({ report, market }: { report: ScreeningReport; market: Market }) {
  return (
    <header className="hero">
      <section className="hero__main">
        <div className="eyebrow">Research OS · {marketLabels[market]}</div>
        <h1>{marketLabels[market]}每日筛选摘要</h1>
        <p>{report.daily_brief.headline}</p>
      </section>
      <section className="hero__meta">
        <dl>
          <div>
            <dt>报告日期</dt>
            <dd>{report.report_date}</dd>
          </div>
          <div>
            <dt>生成时间</dt>
            <dd>{fmtDateTime(report.generated_at)}</dd>
          </div>
          <div>
            <dt>策略</dt>
            <dd title={report.strategy}>{strategyLabel(report.strategy)}</dd>
          </div>
          <div>
            <dt>附录</dt>
            <dd title={report.appendix_report}>完整证据附录</dd>
          </div>
        </dl>
      </section>
    </header>
  );
}

function KpiStrip({ report }: { report: ScreeningReport }) {
  const byMarket = report.counts.refined_by_market;
  const items = [
    ["提炼候选", report.counts.refined_candidates],
    ["核心候选", report.counts.core_candidates],
    ["ETF 工具", report.counts.etf_leaders],
    ["潜力情景", report.counts.potential_candidates],
    ["A/HK/US", `${byMarket.A ?? 0}/${byMarket.HK ?? 0}/${byMarket.US ?? 0}`]
  ];

  return (
    <section className="kpi-strip" aria-label="关键计数">
      {items.map(([label, value]) => (
        <div className="kpi" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </section>
  );
}

function BriefView({ report, candidates, onShowStocks }: { report: ScreeningReport; candidates: Candidate[]; onShowStocks: () => void }) {
  return (
    <div className="view-grid">
      <section className="panel panel--wide">
        <PanelTitle title="今日结论" meta="摘要优先" icon={Sparkles} />
        <div className="brief-list">
          {[report.daily_brief.headline, report.daily_brief.focus].map((line) => (
            <p key={line}>
              <span />
              {line}
            </p>
          ))}
          <p>
            <span />
            数据日期：
            {report.data_freshness.map((item) => ` ${item.market} ${item.latest_date}`).join(" · ")}
          </p>
        </div>
      </section>

      <section className="panel">
        <PanelTitle title="数据健康" meta="覆盖和偏差口径" icon={ShieldCheck} />
        <div className="health-grid">
          {Object.entries(report.coverage_counts).map(([key, value]) => (
            <div key={key}>
              <span>{key}</span>
              <strong>{value.toLocaleString("zh-CN")}</strong>
            </div>
          ))}
        </div>
        {report.data_freshness_warning ? <p className="warning">{report.data_freshness_warning}</p> : <p className="ok">各市场快照日期一致。</p>}
      </section>

      <section className="panel panel--wide">
        <PanelTitle title="今日变化" meta="新增与大幅变化" icon={BarChart3} />
        <div className="action-grid">
          {report.top_actions.slice(0, 8).map((item, index) => (
            <ActionCard action={item} key={`${item.symbol}-${index}`} />
          ))}
        </div>
      </section>

      <section className="panel panel--wide">
        <PanelTitle title="优先研究预览" meta={`${candidates.length} 只`} icon={BriefcaseBusiness} />
        <div className="candidate-grid">
          {candidates.slice(0, 6).map((candidate) => (
            <CandidateCard candidate={candidate} compact key={`${candidate.market}-${candidate.symbol}-${candidate.bucket}`} />
          ))}
        </div>
        <button className="button button--ghost panel-action" onClick={onShowStocks}>
          查看完整股票候选
          <ExternalLink size={15} />
        </button>
      </section>
    </div>
  );
}

function StocksView({ candidates }: { candidates: Candidate[] }) {
  const buckets = unique(candidates.map((item) => item.bucket || "未分组"));

  if (candidates.length === 0) {
    return <EmptyState title="当前筛选条件下没有股票候选" description="降低专家分下限或清空搜索条件后再看。" />;
  }

  return (
    <div className="view-stack">
      {buckets.map((bucket) => {
        const items = candidates.filter((candidate) => (candidate.bucket || "未分组") === bucket);
        return (
          <section className="panel" key={bucket}>
            <PanelTitle title={bucket} meta={`${items.length} 只`} icon={LineChart} />
            <div className="candidate-grid">
              {items.map((candidate) => (
                <CandidateCard candidate={candidate} key={`${candidate.market}-${candidate.symbol}-${candidate.rank_in_bucket ?? ""}`} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function EtfView({ useCases, allEtfs }: { useCases: EtfUseCase[]; allEtfs: EtfLeader[] }) {
  if (useCases.length === 0) {
    return <EmptyState title="当前条件下没有 ETF 工具" description="打开左侧 ETF 用途过滤，或重新生成报告。" />;
  }

  return (
    <div className="view-stack">
      {useCases.map((useCase) => (
        <section className="panel" key={useCase.key}>
          <PanelTitle title={useCase.title} meta={useCase.description} icon={WalletCards} />
          <div className="etf-grid">
            {useCase.leaders.map((item) => (
              <EtfCard etf={item} key={`${item.market}-${item.symbol}-${useCase.key}`} />
            ))}
          </div>
        </section>
      ))}

      <section className="panel">
        <details className="details-table">
          <summary>完整 ETF 明细</summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>用途</th>
                  <th>市场</th>
                  <th>交易</th>
                  <th>代码</th>
                  <th>名称</th>
                  <th>簇</th>
                  <th>ETF分</th>
                  <th>同组</th>
                  <th>成交额</th>
                </tr>
              </thead>
              <tbody>
                {allEtfs.map((item) => (
                  <tr key={`${item.market}-${item.symbol}`}>
                    <td>{item.use_case}</td>
                    <td>{item.market}</td>
                    <td>{item.trading_system}</td>
                    <td>{item.symbol}</td>
                    <td>{item.name}</td>
                    <td>{item.etf_cluster}</td>
                    <td>{fmtScore(item.etf_score)}</td>
                    <td>{item.peer_count ?? 1}</td>
                    <td>{fmtAmount(item.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>
    </div>
  );
}

function PotentialView({ candidates }: { candidates: PotentialCandidate[] }) {
  if (candidates.length === 0) {
    return <EmptyState title="当前筛选条件下没有潜力情景" description="潜力扫描是 price-only 试运行，空结果是正常状态。" />;
  }

  return (
    <section className="panel">
      <PanelTitle title="潜力情景" meta="price-only，不作为交易指令" icon={TrendingUp} />
      <div className="potential-grid">
        {candidates.map((candidate) => (
          <PotentialCard candidate={candidate} key={`${candidate.market}-${candidate.symbol}`} />
        ))}
      </div>
    </section>
  );
}

function AppendixView({ report, appendix }: { report: ScreeningReport; appendix: string }) {
  return (
    <div className="view-stack">
      <section className="panel">
        <PanelTitle title="候选变化" meta={`${report.candidate_changes.length} 条`} icon={BarChart3} />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>变化</th>
                <th>主题桶</th>
                <th>市场</th>
                <th>代码</th>
                <th>名称</th>
                <th>最新分</th>
                <th>上期分</th>
                <th>分数变化</th>
              </tr>
            </thead>
            <tbody>
              {report.candidate_changes.map((item) => (
                <ChangeRow change={item} key={`${item.change}-${item.market}-${item.symbol}`} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel markdown-panel">
        <PanelTitle title="完整附录" meta={report.appendix_report} icon={FileText} />
        {appendix ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{appendix}</ReactMarkdown>
        ) : (
          <EmptyState title="未找到附录 Markdown" description="请先运行 `ah-screener report` 生成最新附录。" />
        )}
      </section>
    </div>
  );
}

function PanelTitle({ title, meta, icon: Icon }: { title: string; meta?: string; icon: typeof Activity }) {
  return (
    <div className="panel-title">
      <h2>
        <Icon size={18} />
        {title}
      </h2>
      {meta ? <span>{meta}</span> : null}
    </div>
  );
}

function ActionCard({ action }: { action: TopAction }) {
  return (
    <article className="action-card">
      <span className="label">{action.label}</span>
      <strong>
        {action.market} {action.symbol} {action.name}
      </strong>
      <small>
        分数 {fmtScore(action.score)}
        {typeof action.delta === "number" ? ` · 变化 ${fmtScore(action.delta)}` : ""}
      </small>
    </article>
  );
}

function CandidateCard({ candidate, compact = false }: { candidate: Candidate; compact?: boolean }) {
  return (
    <article className="candidate-card">
      <header className="card-head">
        <div>
          <strong>{candidate.name}</strong>
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

      <InfoBlock label="入选理由" lines={candidate.why_selected} limit={compact ? 2 : 4} />
      {!compact ? <InfoBlock label="主要风险" lines={candidate.key_risks} limit={3} tone="risk" /> : null}
      {!compact ? <InfoBlock label="买前核验" lines={candidate.verify_before_action} limit={3} /> : null}

      {!compact && (
        <details className="card-details">
          <summary>证据链与失效条件</summary>
          <p>{candidate.invalid_if}</p>
          <ul>
            {(candidate.reasons || []).slice(0, 14).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}

function EtfCard({ etf }: { etf: EtfLeader }) {
  return (
    <article className="etf-card">
      <header className="card-head">
        <div>
          <strong>{etf.name}</strong>
          <span>
            {etf.market} {etf.symbol} · {etf.etf_cluster || etf.etf_category}
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

      <InfoBlock label="为什么选它" lines={etf.why_selected} limit={3} />
      <section className="mini-section">
        <span>备选</span>
        <p>{listText(etf.alternatives)}</p>
      </section>
      <section className="mini-section mini-section--risk">
        <span>注意</span>
        <p>{etf.caution}</p>
      </section>
    </article>
  );
}

function PotentialCard({ candidate }: { candidate: PotentialCandidate }) {
  return (
    <article className="potential-card">
      <header className="card-head">
        <div>
          <strong>{candidate.name}</strong>
          <span>
            {marketLabels[candidate.market]} {candidate.symbol}
          </span>
        </div>
        <b>{fmtScore(candidate.potential_score)}</b>
      </header>
      <div className="chips">
        <span>{candidate.trading_system}</span>
        <span>筑底 {fmtScore(candidate.technical_setup_score)}</span>
        <span>RS {fmtScore(candidate.relative_strength_score)}</span>
        <span>RR {fmtScore(candidate.rr_ratio)}</span>
      </div>
      <section className="scenario-grid">
        <div>
          <span>触发</span>
          <strong>{candidate.scenario?.trigger || fmtPrice(candidate.pivot_price)}</strong>
        </div>
        <div>
          <span>目标</span>
          <strong>{candidate.scenario?.target || fmtPrice(candidate.target_price)}</strong>
        </div>
        <div>
          <span>止损</span>
          <strong>{candidate.scenario?.stop || fmtPrice(candidate.stop_price)}</strong>
        </div>
      </section>
      <section className="mini-section mini-section--risk">
        <span>证伪</span>
        <p>{candidate.invalid_if}</p>
      </section>
    </article>
  );
}

function InfoBlock({ label, lines, limit, tone }: { label: string; lines?: string[]; limit: number; tone?: "risk" }) {
  const content = lines && lines.length > 0 ? lines.slice(0, limit) : ["暂无"];
  return (
    <section className={`mini-section ${tone === "risk" ? "mini-section--risk" : ""}`}>
      <span>{label}</span>
      <ul>
        {content.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </section>
  );
}

function ChangeRow({ change }: { change: CandidateChange }) {
  return (
    <tr>
      <td>{change.change}</td>
      <td>{change.bucket}</td>
      <td>{change.market}</td>
      <td>{change.symbol}</td>
      <td>{change.name}</td>
      <td>{fmtScore(change.latest_score)}</td>
      <td>{fmtScore(change.previous_score)}</td>
      <td>{fmtScore(change.score_delta)}</td>
    </tr>
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

export default App;
