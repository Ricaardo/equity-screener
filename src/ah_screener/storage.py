from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS securities (
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    asset_type VARCHAR DEFAULT 'stock',
    board VARCHAR,
    exchange VARCHAR,
    currency VARCHAR,
    status VARCHAR,
    is_st BOOLEAN DEFAULT false,
    is_hk_connect BOOLEAN DEFAULT false,
    metadata_source VARCHAR,
    metadata_confidence VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (market, symbol)
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR DEFAULT 'stock',
    board VARCHAR,
    trade_date DATE NOT NULL,
    name VARCHAR,
    last_price DOUBLE,
    pct_change DOUBLE,
    volume DOUBLE,
    amount DOUBLE,
    turnover_rate DOUBLE,
    pe_ttm DOUBLE,
    pb DOUBLE,
    market_cap DOUBLE,
    source VARCHAR NOT NULL,
    updated_at TIMESTAMP,
    PRIMARY KEY (market, symbol, trade_date, source)
);

CREATE TABLE IF NOT EXISTS daily_prices (
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    amount DOUBLE,
    adj_type VARCHAR,
    source VARCHAR NOT NULL,
    updated_at TIMESTAMP,
    PRIMARY KEY (market, symbol, trade_date, adj_type, source)
);

CREATE TABLE IF NOT EXISTS hot_theme_definitions (
    snapshot_date DATE NOT NULL,
    theme_name VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    weight DOUBLE,
    keywords VARCHAR,
    rationale VARCHAR,
    source VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (snapshot_date, theme_name, market)
);

CREATE TABLE IF NOT EXISTS technical_indicators (
    snapshot_date DATE NOT NULL,
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    close DOUBLE,
    ma20 DOUBLE,
    ma60 DOUBLE,
    ma120 DOUBLE,
    return_20d DOUBLE,
    return_60d DOUBLE,
    pct_from_120d_high DOUBLE,
    rsi14 DOUBLE,
    volatility_20d DOUBLE,
    trend_score DOUBLE,
    momentum_score DOUBLE,
    technical_score DOUBLE,
    technical_signal VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (snapshot_date, market, symbol)
);

CREATE TABLE IF NOT EXISTS company_tags (
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    tag_type VARCHAR NOT NULL,
    tag_name VARCHAR NOT NULL,
    evidence_level VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    updated_at TIMESTAMP,
    PRIMARY KEY (market, symbol, tag_type, tag_name, source)
);

CREATE TABLE IF NOT EXISTS company_identity_mappings (
    canonical_id VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    listing_type VARCHAR,
    source VARCHAR NOT NULL,
    confidence VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (market, symbol, source)
);

CREATE TABLE IF NOT EXISTS company_documents (
    document_id VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    document_type VARCHAR,
    report_date DATE,
    title VARCHAR,
    source_url VARCHAR,
    local_path VARCHAR,
    file_sha256 VARCHAR,
    source VARCHAR NOT NULL,
    updated_at TIMESTAMP,
    PRIMARY KEY (document_id)
);

CREATE TABLE IF NOT EXISTS document_extractions (
    document_id VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    extract_type VARCHAR NOT NULL,
    extract_key VARCHAR NOT NULL,
    extract_value VARCHAR,
    evidence_text VARCHAR,
    evidence_level VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    updated_at TIMESTAMP,
    PRIMARY KEY (document_id, extract_type, extract_key, source)
);

CREATE TABLE IF NOT EXISTS financial_statement_items (
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    statement_type VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    report_type VARCHAR,
    item_code VARCHAR NOT NULL,
    item_name VARCHAR,
    amount DOUBLE,
    currency VARCHAR,
    source VARCHAR NOT NULL,
    updated_at TIMESTAMP,
    PRIMARY KEY (market, symbol, statement_type, report_date, item_code, source)
);

CREATE TABLE IF NOT EXISTS financial_metrics (
    snapshot_date DATE NOT NULL,
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    report_date DATE NOT NULL,
    report_type VARCHAR,
    revenue DOUBLE,
    revenue_yoy DOUBLE,
    gross_profit DOUBLE,
    parent_net_profit DOUBLE,
    net_profit_yoy DOUBLE,
    deducted_net_profit DOUBLE,
    operating_cashflow DOUBLE,
    total_assets DOUBLE,
    total_liabilities DOUBLE,
    total_equity DOUBLE,
    roe DOUBLE,
    roa DOUBLE,
    gross_margin DOUBLE,
    net_margin DOUBLE,
    debt_asset_ratio DOUBLE,
    current_ratio DOUBLE,
    cashflow_to_profit DOUBLE,
    ocf_to_revenue DOUBLE,
    rd_expense DOUBLE,
    rd_expense_ratio DOUBLE,
    capex DOUBLE,
    capex_to_revenue DOUBLE,
    capex_to_operating_cashflow DOUBLE,
    innovation_efficiency_score DOUBLE,
    revenue_cagr_3y DOUBLE,
    net_profit_cagr_3y DOUBLE,
    roe_avg_3y DOUBLE,
    roe_stability_score DOUBLE,
    margin_stability_score DOUBLE,
    fundamental_trend_score DOUBLE,
    quality_score DOUBLE,
    growth_score DOUBLE,
    balance_score DOUBLE,
    cashflow_score DOUBLE,
    fundamental_score DOUBLE,
    warnings VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (snapshot_date, market, symbol)
);

CREATE TABLE IF NOT EXISTS screening_scores (
    snapshot_date DATE NOT NULL,
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    quality_score DOUBLE,
    growth_score DOUBLE,
    valuation_score DOUBLE,
    liquidity_score DOUBLE,
    theme_score DOUBLE,
    risk_score DOUBLE,
    total_score DOUBLE,
    decision VARCHAR,
    reasons VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (snapshot_date, market, symbol)
);

CREATE TABLE IF NOT EXISTS expert_screening_results (
    snapshot_date DATE NOT NULL,
    strategy VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    canonical_id VARCHAR,
    expert_score DOUBLE,
    master_score DOUBLE,
    china_master_score DOUBLE,
    fundamental_score DOUBLE,
    detailed_industry VARCHAR,
    industry_peer_group VARCHAR,
    peer_score DOUBLE,
    industry_fit_score DOUBLE,
    valuation_percentile DOUBLE,
    theme_score DOUBLE,
    technical_score DOUBLE,
    liquidity_score DOUBLE,
    valuation_score DOUBLE,
    risk_score DOUBLE,
    decision VARCHAR,
    theme_matches VARCHAR,
    reasons VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (snapshot_date, strategy, market, symbol)
);

CREATE TABLE IF NOT EXISTS industry_valuation_stats (
    snapshot_date DATE NOT NULL,
    market VARCHAR NOT NULL,
    detailed_industry VARCHAR NOT NULL,
    securities INTEGER,
    pe_median DOUBLE,
    pb_median DOUBLE,
    valuation_percentile_median DOUBLE,
    valuation_percentile_top_quartile DOUBLE,
    source VARCHAR NOT NULL,
    updated_at TIMESTAMP,
    PRIMARY KEY (snapshot_date, market, detailed_industry, source)
);

CREATE TABLE IF NOT EXISTS refined_candidates (
    snapshot_date DATE NOT NULL,
    strategy VARCHAR NOT NULL,
    bucket VARCHAR NOT NULL,
    rank_in_bucket INTEGER NOT NULL,
    peer_group VARCHAR,
    style_bucket VARCHAR,
    market VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    canonical_id VARCHAR,
    expert_score DOUBLE,
    fundamental_score DOUBLE,
    technical_score DOUBLE,
    detailed_industry VARCHAR,
    industry_peer_group VARCHAR,
    peer_score DOUBLE,
    industry_fit_score DOUBLE,
    valuation_percentile DOUBLE,
    theme_matches VARCHAR,
    reasons VARCHAR,
    selection_note VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (snapshot_date, strategy, bucket, rank_in_bucket)
);
"""

MIGRATION_SQL = """
ALTER TABLE securities ADD COLUMN IF NOT EXISTS asset_type VARCHAR DEFAULT 'stock';
ALTER TABLE securities ADD COLUMN IF NOT EXISTS board VARCHAR;
ALTER TABLE securities ADD COLUMN IF NOT EXISTS is_st BOOLEAN DEFAULT false;
ALTER TABLE securities ADD COLUMN IF NOT EXISTS is_hk_connect BOOLEAN DEFAULT false;
ALTER TABLE securities ADD COLUMN IF NOT EXISTS metadata_source VARCHAR;
ALTER TABLE securities ADD COLUMN IF NOT EXISTS metadata_confidence VARCHAR;
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS asset_type VARCHAR DEFAULT 'stock';
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS board VARCHAR;
ALTER TABLE expert_screening_results ADD COLUMN IF NOT EXISTS china_master_score DOUBLE;
ALTER TABLE expert_screening_results ADD COLUMN IF NOT EXISTS fundamental_score DOUBLE;
ALTER TABLE expert_screening_results ADD COLUMN IF NOT EXISTS industry_peer_group VARCHAR;
ALTER TABLE expert_screening_results ADD COLUMN IF NOT EXISTS peer_score DOUBLE;
ALTER TABLE expert_screening_results ADD COLUMN IF NOT EXISTS industry_fit_score DOUBLE;
ALTER TABLE expert_screening_results ADD COLUMN IF NOT EXISTS canonical_id VARCHAR;
ALTER TABLE expert_screening_results ADD COLUMN IF NOT EXISTS detailed_industry VARCHAR;
ALTER TABLE expert_screening_results ADD COLUMN IF NOT EXISTS valuation_percentile DOUBLE;
ALTER TABLE refined_candidates ADD COLUMN IF NOT EXISTS peer_group VARCHAR;
ALTER TABLE refined_candidates ADD COLUMN IF NOT EXISTS style_bucket VARCHAR;
ALTER TABLE refined_candidates ADD COLUMN IF NOT EXISTS selection_note VARCHAR;
ALTER TABLE refined_candidates ADD COLUMN IF NOT EXISTS industry_peer_group VARCHAR;
ALTER TABLE refined_candidates ADD COLUMN IF NOT EXISTS peer_score DOUBLE;
ALTER TABLE refined_candidates ADD COLUMN IF NOT EXISTS industry_fit_score DOUBLE;
ALTER TABLE refined_candidates ADD COLUMN IF NOT EXISTS canonical_id VARCHAR;
ALTER TABLE refined_candidates ADD COLUMN IF NOT EXISTS detailed_industry VARCHAR;
ALTER TABLE refined_candidates ADD COLUMN IF NOT EXISTS valuation_percentile DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS revenue_cagr_3y DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS net_profit_cagr_3y DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS roe_avg_3y DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS roe_stability_score DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS margin_stability_score DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS fundamental_trend_score DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS rd_expense DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS rd_expense_ratio DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS capex DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS capex_to_revenue DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS capex_to_operating_cashflow DOUBLE;
ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS innovation_efficiency_score DOUBLE;
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.db_path), read_only=read_only)

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(SCHEMA_SQL)
            conn.execute(MIGRATION_SQL)

    def upsert_dataframe(self, table: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0

        with self.connect() as conn:
            conn.execute(SCHEMA_SQL)
            conn.execute(MIGRATION_SQL)
            conn.register("incoming_df", df)
            columns = list(df.columns)
            column_sql = ", ".join(columns)
            select_sql = ", ".join(f"incoming_df.{column}" for column in columns)
            conn.execute(f"INSERT OR REPLACE INTO {table} ({column_sql}) SELECT {select_sql} FROM incoming_df")
            conn.unregister("incoming_df")
        return len(df)

    def query_df(self, sql: str, parameters: object | None = None) -> pd.DataFrame:
        if not self.db_path.exists():
            self.init_db()

        with self.connect(read_only=True) as conn:
            if parameters is None:
                return conn.execute(sql).fetch_df()
            return conn.execute(sql, parameters).fetch_df()

    def execute(self, sql: str, parameters: object | None = None) -> None:
        with self.connect() as conn:
            conn.execute(SCHEMA_SQL)
            conn.execute(MIGRATION_SQL)
            if parameters is None:
                conn.execute(sql)
            else:
                conn.execute(sql, parameters)
