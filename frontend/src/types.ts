export type Market = "A" | "HK" | "US";

export interface Candidate {
  bucket?: string;
  rank_in_bucket?: number;
  style_bucket?: string;
  market: Market;
  trading_system: string;
  symbol: string;
  name: string;
  expert_score?: number;
  master_score?: number;
  china_master_score?: number;
  fundamental_score?: number;
  technical_score?: number;
  detailed_industry?: string;
  industry_peer_group?: string;
  peer_score?: number;
  industry_fit_score?: number;
  valuation_percentile?: number;
  decision?: string;
  theme_matches?: string[];
  reasons?: string[];
  selection_note?: string;
  why_selected?: string[];
  key_risks?: string[];
  verify_before_action?: string[];
  invalid_if?: string;
}

export interface PotentialCandidate {
  market: Market;
  trading_system: string;
  symbol: string;
  name: string;
  potential_score?: number;
  technical_setup_score?: number;
  relative_strength_score?: number;
  fundamental_turn_score?: number;
  theme_early_score?: number;
  pivot_price?: number;
  target_price?: number;
  stop_price?: number;
  rr_ratio?: number;
  time_stop_days?: number;
  hist_win_rate?: number;
  bias_note?: string;
  setup_note?: string;
  scenario?: {
    trigger?: string | null;
    target?: string | null;
    stop?: string | null;
    time_stop_days?: number | null;
  };
  invalid_if?: string;
}

export interface EtfLeader {
  market: Market;
  trading_system: string;
  symbol: string;
  name: string;
  etf_category?: string;
  etf_cluster?: string;
  etf_track?: string;
  etf_score?: number;
  etf_recommendation?: string;
  peer_count?: number;
  peer_alternatives?: string;
  pct_change?: number;
  amount?: number;
  use_case?: string;
  why_selected?: string[];
  alternatives?: string[];
  caution?: string;
}

export interface EtfUseCase {
  key: string;
  title: string;
  description: string;
  leaders: EtfLeader[];
  count: number;
}

export interface CandidateChange {
  change: string;
  bucket?: string;
  market: Market;
  symbol: string;
  name: string;
  latest_score?: number | null;
  previous_score?: number | null;
  score_delta?: number | null;
}

export interface TopAction {
  type: string;
  label: string;
  market?: Market;
  symbol?: string;
  name?: string;
  score?: number | null;
  delta?: number | null;
  action?: string;
}

export interface DailyBrief {
  headline: string;
  focus: string;
  priority_candidates: Candidate[];
  potential_setups: PotentialCandidate[];
  etf_use_cases: EtfUseCase[];
  top_changes: CandidateChange[];
  data_health: {
    coverage_counts: Record<string, number>;
    freshness: Array<{ market: Market; latest_date: string }>;
    warning?: string | null;
  };
  portfolio_notes: string[];
  reader_contract: string;
}

export interface ScreeningReport {
  schema_version: string;
  report_type: string;
  generated_at: string;
  report_date: string;
  strategy: string;
  database: string;
  disclaimer: string;
  markdown_report: string;
  appendix_report: string;
  conclusion: string[];
  bias_notes: string[];
  external_context: Array<{ name: string; url: string; note: string }>;
  data_freshness: Array<{ market: Market; latest_date: string }>;
  data_freshness_warning?: string | null;
  coverage_counts: Record<string, number>;
  decision_distribution: Array<{ decision: string; count: number }>;
  counts: {
    refined_candidates: number;
    core_candidates: number;
    potential_candidates: number;
    etf_leaders: number;
    refined_by_market: Partial<Record<Market, number>>;
  };
  daily_brief: DailyBrief;
  top_actions: TopAction[];
  etf_use_cases: EtfUseCase[];
  refined_candidates: Candidate[];
  core_candidates: Candidate[];
  potential_candidates: PotentialCandidate[];
  etf_leaders: EtfLeader[];
  candidate_changes: CandidateChange[];
}
