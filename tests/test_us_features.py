from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from ah_screener.storage import Store
from us_screener import concept_boards
from us_screener.cli import app
from us_screener.config import get_us_config
from us_screener.heat import compute_heat_scores
from us_screener.llm_opinion import generate_us_llm_opinion
from us_screener.macro import get_macro_context, score_macro_transmission
from us_screener.reporting_us import build_us_premarket_payload, generate_us_premarket_report
from us_screener.scheduler_us import install_us_launchd_schedule
from us_screener.scoring_us import run_us_screen


runner = CliRunner()


def _seed_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "us.duckdb")
    store.init_db()

    snapshots = pd.DataFrame(
        [
            {
                "market": "US",
                "symbol": "AAPL",
                "asset_type": "stock",
                "board": "NASDAQ",
                "trade_date": pd.Timestamp("2026-05-29"),
                "name": "Apple Inc",
                "last_price": 210.0,
                "pct_change": 1.2,
                "volume": 1000.0,
                "amount": 15_000_000.0,
                "turnover_rate": None,
                "pe_ttm": 28.0,
                "pb": 12.0,
                "market_cap": 3_100_000_000_000.0,
                "source": "test",
                "updated_at": pd.Timestamp("2026-05-29"),
            },
            {
                "market": "US",
                "symbol": "NVDA",
                "asset_type": "stock",
                "board": "NASDAQ",
                "trade_date": pd.Timestamp("2026-05-29"),
                "name": "NVIDIA Corporation",
                "last_price": 1150.0,
                "pct_change": 3.1,
                "volume": 1400.0,
                "amount": 22_000_000.0,
                "turnover_rate": None,
                "pe_ttm": 40.0,
                "pb": 20.0,
                "market_cap": 2_800_000_000_000.0,
                "source": "test",
                "updated_at": pd.Timestamp("2026-05-29"),
            },
            {
                "market": "US",
                "symbol": "BABA",
                "asset_type": "stock",
                "board": "NYSE",
                "trade_date": pd.Timestamp("2026-05-29"),
                "name": "Alibaba Group",
                "last_price": 85.0,
                "pct_change": -0.5,
                "volume": 800.0,
                "amount": 8_000_000.0,
                "turnover_rate": None,
                "pe_ttm": 12.0,
                "pb": 1.8,
                "market_cap": 220_000_000_000.0,
                "source": "test",
                "updated_at": pd.Timestamp("2026-05-29"),
            },
            {
                "market": "US",
                "symbol": "IONQ",
                "asset_type": "stock",
                "board": "NYSE",
                "trade_date": pd.Timestamp("2026-05-29"),
                "name": "IonQ Inc",
                "last_price": 11.0,
                "pct_change": 2.5,
                "volume": 1200.0,
                "amount": 4_500_000.0,
                "turnover_rate": None,
                "pe_ttm": None,
                "pb": 7.0,
                "market_cap": 2_800_000_000.0,
                "source": "test",
                "updated_at": pd.Timestamp("2026-05-29"),
            },
        ]
    )
    store.upsert_dataframe("market_snapshots", snapshots)

    securities = pd.DataFrame(
        [
            {"market": "US", "symbol": "AAPL", "name": "Apple Inc", "asset_type": "stock"},
            {"market": "US", "symbol": "NVDA", "name": "NVIDIA Corporation", "asset_type": "stock"},
            {"market": "US", "symbol": "BABA", "name": "Alibaba Group", "asset_type": "stock"},
            {"market": "US", "symbol": "IONQ", "name": "IonQ Inc", "asset_type": "stock"},
        ]
    )
    store.upsert_dataframe("securities", securities)

    daily_rows = []
    for symbol, start, drift, volume_base in [
        ("AAPL", 180.0, 1.0, 900.0),
        ("NVDA", 900.0, 4.0, 1000.0),
        ("BABA", 95.0, -0.2, 700.0),
        ("IONQ", 8.0, 0.08, 850.0),
        ("SPY", 500.0, 0.4, 1500.0),
        ("QQQ", 430.0, 0.7, 1450.0),
        ("IWM", 205.0, 0.15, 1300.0),
        ("TLT", 92.0, 0.05, 1200.0),
        ("XLK", 210.0, 0.5, 1100.0),
        ("XLE", 85.0, 0.2, 1000.0),
    ]:
        for idx in range(80):
            date = pd.Timestamp("2026-03-01") + pd.Timedelta(days=idx)
            close = start + drift * idx
            daily_rows.append(
                {
                    "market": "US",
                    "symbol": symbol,
                    "trade_date": date,
                    "open": close - 1,
                    "high": close + 2,
                    "low": close - 2,
                    "close": close,
                    "volume": volume_base + idx * 4,
                    "amount": (volume_base + idx * 4) * close,
                    "adj_type": "raw",
                    "source": "test",
                    "updated_at": pd.Timestamp("2026-05-29"),
                }
            )
    store.upsert_dataframe("daily_prices", pd.DataFrame(daily_rows))

    technical = pd.DataFrame(
        [
            {
                "snapshot_date": pd.Timestamp("2026-05-29"),
                "market": "US",
                "symbol": "AAPL",
                "name": "Apple Inc",
                "close": 210.0,
                "ma20": 200.0,
                "ma60": 190.0,
                "ma120": None,
                "return_20d": 0.08,
                "return_60d": 0.18,
                "pct_from_120d_high": -0.03,
                "rsi14": 61.0,
                "volatility_20d": 0.25,
                "trend_score": 80.0,
                "momentum_score": 70.0,
                "technical_score": 76.0,
                "technical_signal": "constructive",
                "updated_at": pd.Timestamp("2026-05-29"),
            },
            {
                "snapshot_date": pd.Timestamp("2026-05-29"),
                "market": "US",
                "symbol": "NVDA",
                "name": "NVIDIA Corporation",
                "close": 1150.0,
                "ma20": 1080.0,
                "ma60": 990.0,
                "ma120": None,
                "return_20d": 0.14,
                "return_60d": 0.33,
                "pct_from_120d_high": -0.01,
                "rsi14": 66.0,
                "volatility_20d": 0.32,
                "trend_score": 88.0,
                "momentum_score": 82.0,
                "technical_score": 85.0,
                "technical_signal": "strong_trend",
                "updated_at": pd.Timestamp("2026-05-29"),
            },
            {
                "snapshot_date": pd.Timestamp("2026-05-29"),
                "market": "US",
                "symbol": "IONQ",
                "name": "IonQ Inc",
                "close": 11.0,
                "ma20": 10.2,
                "ma60": 9.4,
                "ma120": None,
                "return_20d": 0.10,
                "return_60d": 0.20,
                "pct_from_120d_high": -0.05,
                "rsi14": 58.0,
                "volatility_20d": 0.40,
                "trend_score": 72.0,
                "momentum_score": 68.0,
                "technical_score": 70.0,
                "technical_signal": "constructive",
                "updated_at": pd.Timestamp("2026-05-29"),
            },
        ]
    )
    store.upsert_dataframe("technical_indicators", technical)

    financials = pd.DataFrame(
        [
            {
                "snapshot_date": pd.Timestamp("2026-05-29"),
                "market": "US",
                "symbol": "AAPL",
                "name": "Apple Inc",
                "report_date": pd.Timestamp("2026-03-31"),
                "report_type": "quarterly",
                "quality_score": 82.0,
                "growth_score": 72.0,
                "balance_score": 75.0,
                "cashflow_score": 80.0,
                "fundamental_score": 78.0,
                "updated_at": pd.Timestamp("2026-05-29"),
            },
            {
                "snapshot_date": pd.Timestamp("2026-05-29"),
                "market": "US",
                "symbol": "NVDA",
                "name": "NVIDIA Corporation",
                "report_date": pd.Timestamp("2026-03-31"),
                "report_type": "quarterly",
                "quality_score": 88.0,
                "growth_score": 92.0,
                "balance_score": 78.0,
                "cashflow_score": 84.0,
                "fundamental_score": 86.0,
                "updated_at": pd.Timestamp("2026-05-29"),
            },
            {
                "snapshot_date": pd.Timestamp("2026-05-29"),
                "market": "US",
                "symbol": "IONQ",
                "name": "IonQ Inc",
                "report_date": pd.Timestamp("2026-03-31"),
                "report_type": "quarterly",
                "quality_score": 50.0,
                "growth_score": 68.0,
                "balance_score": 58.0,
                "cashflow_score": 46.0,
                "fundamental_score": 56.0,
                "updated_at": pd.Timestamp("2026-05-29"),
            },
        ]
    )
    store.upsert_dataframe("financial_metrics", financials)

    concept_boards.tag_concept_boards(store, seed={"NVDA": "AI算力", "IONQ": "量子计算"})
    return store


def _tag_baba(store: Store) -> None:
    store.upsert_dataframe(
        "company_tags",
        pd.DataFrame(
            [
                {
                    "market": "US",
                    "symbol": "BABA",
                    "tag_type": "risk",
                    "tag_name": "china_concept",
                    "evidence_level": "high",
                    "source": "test",
                    "updated_at": pd.Timestamp("2026-05-29"),
                }
            ]
        ),
    )


def test_heat_scores_offline(tmp_path: Path):
    store = _seed_store(tmp_path)
    heat = compute_heat_scores(store)
    assert list(heat.columns) == ["market", "symbol", "heat_score", "heat_components"]
    assert {"AAPL", "NVDA", "IONQ", "BABA"} <= set(heat["symbol"])
    assert heat["heat_score"].between(0, 100).all()
    assert isinstance(heat.iloc[0]["heat_components"], dict)


def test_sec_latest_shares():
    from us_screener.sec_bulk_loader import _latest_shares

    cf = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2025-12-31", "val": 9.0e9},
        {"end": "2026-03-31", "val": 1.0e10},
        {"end": "2025-06-30", "val": 8.0e9},
    ]}}}}}
    assert _latest_shares(cf) == 1.0e10  # newest end wins


def test_sec_fill_snapshot_valuation(tmp_path: Path):
    from us_screener.sec_bulk_loader import _fill_snapshot_valuation

    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    store.upsert_dataframe(
        "market_snapshots",
        pd.DataFrame([{"market": "US", "symbol": "AAPL", "asset_type": "stock",
                       "trade_date": pd.Timestamp("2026-05-29"), "name": "Apple", "last_price": 200.0,
                       "amount": 1e9, "volume": 1e6, "pe_ttm": None, "pb": None, "market_cap": None,
                       "source": "test", "updated_at": pd.Timestamp("2026-05-29")}]),
    )
    metrics = pd.DataFrame([{"symbol": "AAPL", "total_equity": 1.0e11, "parent_net_profit": 1.0e11}])
    n = _fill_snapshot_valuation(store, {"AAPL": 1.0e10}, metrics)
    assert n == 1
    row = store.query_df("SELECT market_cap, pb, pe_ttm FROM market_snapshots WHERE symbol='AAPL'").iloc[0]
    assert float(row["market_cap"]) == 200.0 * 1.0e10
    assert float(row["pb"]) == (200.0 * 1.0e10) / 1.0e11
    assert float(row["pe_ttm"]) == (200.0 * 1.0e10) / 1.0e11


def test_sec_fill_prefers_existing_market_cap(tmp_path: Path):
    """An authoritative (Sina) market cap must not be overwritten by SEC shares x price
    (which is wrong for multi-class structures); PB derives from the authoritative cap."""
    from us_screener.sec_bulk_loader import _fill_snapshot_valuation

    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    store.upsert_dataframe(
        "market_snapshots",
        pd.DataFrame([{"market": "US", "symbol": "IBKR", "asset_type": "stock",
                       "trade_date": pd.Timestamp("2026-05-29"), "name": "IBKR", "last_price": 200.0,
                       "amount": 1e9, "volume": 1e6, "pe_ttm": 25.0, "pb": None, "market_cap": 2.0e11,
                       "source": "test", "updated_at": pd.Timestamp("2026-05-29")}]),
    )
    metrics = pd.DataFrame([{"symbol": "IBKR", "total_equity": 1.0e10, "parent_net_profit": 5.0e9}])
    # SEC reports only ~440M shares for IBKR -> shares*price would be a wrong 8.8e10
    _fill_snapshot_valuation(store, {"IBKR": 4.4e8}, metrics)
    row = store.query_df("SELECT market_cap, pb, pe_ttm FROM market_snapshots WHERE symbol='IBKR'").iloc[0]
    assert float(row["market_cap"]) == 2.0e11  # Sina cap preserved, not 8.8e10
    assert float(row["pb"]) == 2.0e11 / 1.0e10  # PB from authoritative cap
    assert float(row["pe_ttm"]) == 25.0  # Sina PE kept


def test_sec_bulk_loader_integration(tmp_path: Path, monkeypatch):
    import json
    import zipfile

    from ah_screener.sources import us_client
    from us_screener import sec_bulk_loader

    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    store.upsert_dataframe(
        "market_snapshots",
        pd.DataFrame([{"market": "US", "symbol": "AAPL", "asset_type": "stock",
                       "trade_date": pd.Timestamp("2026-05-29"), "name": "Apple", "last_price": 200.0,
                       "amount": 1e9, "volume": 1e6, "pe_ttm": None, "pb": None, "market_cap": None,
                       "source": "test", "updated_at": pd.Timestamp("2026-05-29")}]),
    )

    def _gaap(val):
        return {"units": {"USD": [{"end": "2025-12-31", "val": val, "form": "10-K", "fp": "FY", "filed": "2026-02-01"}]}}

    cf = {"cik": 1, "entityName": "Apple Inc.", "facts": {
        "us-gaap": {"StockholdersEquity": _gaap(1.0e11), "NetIncomeLoss": _gaap(1.0e11), "Revenues": _gaap(4.0e11)},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [{"end": "2026-01-01", "val": 1.0e10}]}}},
    }}
    zpath = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("CIK0000000001.json", json.dumps(cf))
    monkeypatch.setattr(
        us_client, "fetch_sec_company_tickers",
        lambda: {"AAPL": {"cik_str": 1, "ticker": "AAPL", "title": "Apple Inc"}},
    )
    out = sec_bulk_loader.load_companyfacts_zip(store, zpath)
    assert out["status"] == "ok" and out["shares"] == 1
    mcap = store.query_df("SELECT market_cap FROM market_snapshots WHERE symbol='AAPL'").iloc[0]["market_cap"]
    assert float(mcap) == 200.0 * 1.0e10  # shares x price, no API call


def test_peer_relative_valuation():
    from us_screener.scoring_us import _peer_relative_score

    values = pd.Series([10.0, 20.0, 30.0, 100.0, 200.0, 300.0, 5.0])
    groups = pd.Series(["tech", "tech", "tech", "bank", "bank", "bank", ""])
    out = _peer_relative_score(values, groups, min_group=3)
    # within each sector the cheapest multiple scores highest
    assert out.iloc[0] > out.iloc[2]  # tech: PE 10 beats 30
    assert out.iloc[3] > out.iloc[5]  # bank: PE 100 beats 300
    # cross-sector: the cheapest bank scores high despite being pricier than any tech
    assert out.iloc[3] > 50


def test_earnings_tag_and_map(tmp_path: Path, monkeypatch):
    from us_screener import earnings

    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    cal = pd.DataFrame(
        [
            {"symbol": "AAPL", "earnings_date": "2026-06-02", "when": "time-after-hours"},
            {"symbol": "MSFT", "earnings_date": "2026-06-05", "when": "time-pre-market"},
        ]
    )
    monkeypatch.setattr(earnings, "fetch_earnings_calendar", lambda days_ahead=10: cal)
    out = earnings.tag_earnings(store)
    assert out["status"] == "ok" and out["tagged"] == 2
    m = earnings.earnings_map(store)
    assert m["AAPL"]["date"] == "2026-06-02"
    assert m["MSFT"]["when"] == "time-pre-market"


def test_report_earnings_annotation(tmp_path: Path):
    from datetime import date, timedelta

    from us_screener import reporting_us

    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    soon_date = (date.today() + timedelta(days=3)).isoformat()
    far_date = (date.today() + timedelta(days=30)).isoformat()
    store.upsert_dataframe(
        "company_tags",
        pd.DataFrame(
            [
                {"market": "US", "symbol": "AAPL", "tag_type": "earnings_date", "tag_name": soon_date,
                 "evidence_level": "amc", "source": "nasdaq.earnings", "updated_at": pd.Timestamp.now()},
                {"market": "US", "symbol": "MSFT", "tag_type": "earnings_date", "tag_name": far_date,
                 "evidence_level": "bmo", "source": "nasdaq.earnings", "updated_at": pd.Timestamp.now()},
            ]
        ),
    )
    payload = {"top_candidates": [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "NVDA"}]}
    reporting_us._annotate_earnings(payload, store)
    aapl = next(c for c in payload["top_candidates"] if c["symbol"] == "AAPL")
    assert aapl["earnings_date"] == soon_date
    assert any(e["symbol"] == "AAPL" for e in payload["earnings_soon"])
    assert not any(e["symbol"] == "MSFT" for e in payload["earnings_soon"])  # 30d out


def test_macro_context_fallback(tmp_path: Path, monkeypatch):
    from us_screener import fred

    monkeypatch.setattr(fred, "get_fred_macro", lambda: {"status": "unavailable", "fred_score": None})
    store = Store(tmp_path / "empty.duckdb")
    store.init_db()
    ctx = get_macro_context(store)
    assert ctx["status"] == "fallback_neutral"
    scored = score_macro_transmission(pd.DataFrame([{"market": "US", "symbol": "AAPL"}]), store, ctx)
    assert scored.iloc[0]["macro_score"] == 50.0


def test_macro_context_uses_fred(tmp_path: Path, monkeypatch):
    from us_screener import fred

    monkeypatch.setattr(
        fred,
        "get_fred_macro",
        lambda: {"status": "ok", "fred_score": 72.0, "regime": "risk_on", "as_of": "2026-05-28",
                 "metrics": {}, "components": {"credit": 85.0, "vix": 72.0, "curve": 55.0}},
    )
    store = Store(tmp_path / "empty.duckdb")
    store.init_db()
    ctx = get_macro_context(store)
    # No ETF history, but FRED carries the macro signal -> not neutral fallback.
    assert ctx["status"] == "ok"
    assert ctx["market_score"] == 72.0
    assert ctx["regime"] == "bullish"
    assert ctx["fred"]["fred_score"] == 72.0


def test_fred_score_computation(monkeypatch):
    import pandas as pd

    from us_screener import fred

    fred.get_fred_macro.cache_clear()
    series = {
        "BAMLH0A0HYM2": pd.Series([2.7], index=[pd.Timestamp("2026-05-28")]),
        "VIXCLS": pd.Series([15.0], index=[pd.Timestamp("2026-05-28")]),
        "T10Y2Y": pd.Series([0.46], index=[pd.Timestamp("2026-05-28")]),
        "DGS10": pd.Series([4.45], index=[pd.Timestamp("2026-05-28")]),
    }
    monkeypatch.setattr(fred, "fetch_fred_series", lambda sid, **k: series.get(sid, pd.Series(dtype="float64")))
    out = fred.get_fred_macro()
    fred.get_fred_macro.cache_clear()
    assert out["status"] == "ok"
    assert out["regime"] == "risk_on"  # tight credit + low vix
    assert 60 <= out["fred_score"] <= 90


def test_fred_policy_signal(monkeypatch):
    from us_screener import fred

    idx = pd.date_range("2024-01-01", periods=20, freq="MS")
    cpi = pd.Series([300.0 + i for i in range(20)], index=idx)  # hot, rising
    monkeypatch.setattr(
        fred, "fetch_fred_series",
        lambda sid, **k: cpi if sid == fred.CPI_SERIES else pd.Series(dtype="float64"),
    )
    # 2Y > funds + hot CPI -> hawkish (higher-for-longer)
    hawk = fred._policy_signal({"DGS2": 3.99, "DFEDTARU": 3.75})
    assert hawk["stance"] == "hawkish"
    assert hawk["rate_path_2y_minus_funds"] == 0.24
    assert hawk["cpi_yoy"] is not None and hawk["cpi_yoy"] >= 3.0
    # 2Y well below funds -> dovish (cuts priced) even with hot CPI
    dove = fred._policy_signal({"DGS2": 3.25, "DFEDTARU": 3.75})
    assert dove["stance"] == "dovish"


def test_macro_policy_tilt_penalizes_growth(tmp_path: Path):
    from us_screener.macro import score_macro_transmission

    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    ctx = {"market_score": 60.0, "regime": "bullish", "component_scores": {},
           "policy": {"stance": "hawkish"}}
    cands = pd.DataFrame([
        {"market": "US", "symbol": "NVDA", "concept_boards": ["AI算力"]},
        {"market": "US", "symbol": "KO", "concept_boards": []},
    ])
    out = score_macro_transmission(cands, store, ctx)
    nvda = out[out["symbol"] == "NVDA"].iloc[0]["macro_score"]
    ko = out[out["symbol"] == "KO"].iloc[0]["macro_score"]
    assert nvda < ko  # hawkish penalizes the long-duration growth board


def test_fd_classification(tmp_path: Path, monkeypatch):
    import pandas as pd

    from us_screener import classification_fd

    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    store.upsert_dataframe(
        "securities",
        pd.DataFrame(
            [
                {"market": "US", "symbol": "NVDA", "name": "NVIDIA", "asset_type": "stock"},
                {"market": "US", "symbol": "JPM", "name": "JPMorgan", "asset_type": "stock"},
            ]
        ),
    )
    fd_frame = pd.DataFrame(
        {"sector": ["Technology", "Financials"], "industry": ["Semiconductors", "Banks"]},
        index=["NVDA", "JPM"],
    )
    monkeypatch.setattr(classification_fd, "load_fd_us_equities", lambda: fd_frame)
    out = classification_fd.tag_fd_classification(store)
    assert out["status"] == "ok"
    assert out["sector_tags"] == 2 and out["industry_tags"] == 2
    # Semiconductors -> AI算力 concept board derived from industry
    boards = store.query_df(
        "SELECT tag_name FROM company_tags WHERE symbol='NVDA' AND tag_type='concept_board'"
    )
    assert "AI算力" in set(boards["tag_name"])
    smap = classification_fd.sector_industry_map(store)
    assert smap["JPM"]["sector"] == "Financials"


def test_concept_board_keyword_fallback():
    boards = concept_boards.infer_concept_boards("XYZ", "Acme Quantum Systems")
    assert "量子计算" in boards


def test_scoring_excludes_china_concept(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("US_SCREENER_DB", str(tmp_path / "us.duckdb"))
    monkeypatch.setenv("AH_SCREENER_DB", str(tmp_path / "us.duckdb"))
    store = _seed_store(tmp_path)
    _tag_baba(store)
    result = run_us_screen(store=store, persist=True)
    scored = result["results"]
    baba = scored.loc[scored["symbol"] == "BABA"].iloc[0]
    nvda = scored.loc[scored["symbol"] == "NVDA"].iloc[0]
    # is_filtered must be a real bool dtype: an object column of Python bools
    # would make `~scored["is_filtered"]` do bitwise int negation (~True == -2)
    # and break reporting's `scored.loc[~scored["is_filtered"]]` (regression guard).
    assert scored["is_filtered"].dtype == bool
    assert (~scored["is_filtered"]).sum() == int((~scored["is_filtered"]).sum())
    assert bool(baba["is_filtered"]) is True
    assert "china_concept" in baba["filter_reasons"]
    assert bool(nvda["is_filtered"]) is False
    persisted = store.query_df(
        "SELECT symbol, decision FROM expert_screening_results WHERE strategy = 'us_premarket'"
    )
    assert set(persisted["symbol"]) >= {"AAPL", "NVDA", "BABA", "IONQ"}


def test_reporting_payload_and_files(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "us.duckdb"
    monkeypatch.setenv("US_SCREENER_DB", str(db_path))
    monkeypatch.setenv("AH_SCREENER_DB", str(db_path))
    monkeypatch.setenv("US_SCREENER_REPORTS", str(tmp_path / "reports"))
    store = _seed_store(tmp_path)
    _tag_baba(store)
    payload = build_us_premarket_payload(store)
    assert payload["report_type"] == "us-premarket"
    assert payload["counts"]["universe"] >= 4
    assert payload["top_candidates"]
    assert payload["llm_opinion"]["status"] == "skipped"
    path = generate_us_premarket_report(output_dir=tmp_path / "reports")
    assert path.exists()
    assert (tmp_path / "reports" / "us-premarket-latest.json").exists()
    written = json.loads((tmp_path / "reports" / "us-premarket-latest.json").read_text())
    assert written["report_type"] == "us-premarket"


def test_llm_skip_without_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("US_SCREENER_LLM_PROVIDER", raising=False)
    opinion = generate_us_llm_opinion({"top_candidates": []})
    assert opinion["status"] == "skipped"


def test_cli_info_and_report_json(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "us.duckdb"
    reports_dir = tmp_path / "reports"
    monkeypatch.setenv("US_SCREENER_DB", str(db_path))
    monkeypatch.setenv("AH_SCREENER_DB", str(db_path))
    monkeypatch.setenv("US_SCREENER_REPORTS", str(reports_dir))
    _seed_store(tmp_path)
    tagged_store = Store(db_path)
    tagged_store.init_db()
    _tag_baba(tagged_store)

    info = runner.invoke(app, ["info", "--json"])
    assert info.exit_code == 0
    info_payload = json.loads(info.stdout)
    assert info_payload["db_path"] == str(db_path)

    report = runner.invoke(app, ["report", "--json"])
    assert report.exit_code == 0
    report_payload = json.loads(report.stdout)
    assert report_payload["latest_json_path"].endswith("us-premarket-latest.json")


def test_exclude_china_concept_false_keeps_china_name(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("US_SCREENER_DB", str(tmp_path / "us.duckdb"))
    monkeypatch.setenv("AH_SCREENER_DB", str(tmp_path / "us.duckdb"))
    monkeypatch.setenv("US_SCREENER_EXCLUDE_CHINA", "0")
    store = _seed_store(tmp_path)
    _tag_baba(store)
    scored = run_us_screen(store=store, persist=False)["results"]
    baba = scored.loc[scored["symbol"] == "BABA"].iloc[0]
    # Flag is still surfaced truthfully, but with exclusion off it is not hard-cut.
    assert bool(baba["is_china_concept"]) is True
    assert "china_concept" not in (baba["filter_reasons"] or [])
    assert bool(baba["is_filtered"]) is False
    assert float(baba["expert_score"]) > 0.0


def test_empty_universe_report(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "empty.duckdb"
    monkeypatch.setenv("US_SCREENER_DB", str(db_path))
    monkeypatch.setenv("AH_SCREENER_DB", str(db_path))
    monkeypatch.setenv("US_SCREENER_REPORTS", str(tmp_path / "reports"))
    store = Store(db_path)
    store.init_db()
    payload = build_us_premarket_payload(store)
    assert payload["counts"]["universe"] == 0
    assert payload["counts"]["candidates"] == 0
    assert payload["top_candidates"] == []
    assert payload["llm_opinion"]["status"] == "skipped"
    path = generate_us_premarket_report(output_dir=tmp_path / "reports")
    assert path.exists()
    assert (tmp_path / "reports" / "us-premarket-latest.md").exists()


def test_api_key_never_emitted(tmp_path: Path, monkeypatch):
    sentinel = "sk-do-not-leak-DEADBEEF"
    monkeypatch.setenv("US_SCREENER_DB", str(tmp_path / "us.duckdb"))
    monkeypatch.setenv("AH_SCREENER_DB", str(tmp_path / "us.duckdb"))
    monkeypatch.setenv("US_SCREENER_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    # The key must be loaded into config (so we know it could leak) ...
    assert get_us_config().llm_api_key == sentinel
    # ... yet must never appear in any emitted surface.
    info = runner.invoke(app, ["info", "--json"])
    assert info.exit_code == 0
    assert sentinel not in info.stdout
    payload = json.loads(info.stdout)
    assert payload["llm_api_key_present"] is True
    assert sentinel not in json.dumps(payload)


def test_scheduler_uses_module_invocation(tmp_path: Path, monkeypatch):
    # No .venv under the throwaway repo dir, so the script must fall back to the
    # current interpreter via `python -m us_screener.cli`, never a hardcoded
    # .venv/bin/us-screener path (which may not exist in a base-env install).
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    script_path, plist_path = install_us_launchd_schedule(repo_dir=tmp_path, hour=20, minute=30)
    script_text = script_path.read_text()
    assert "-m us_screener.cli update" in script_text
    assert "-m us_screener.cli report" in script_text
    assert ".venv/bin/us-screener" not in script_text
    assert plist_path.exists()


def test_fetch_sina_quotes_parses(monkeypatch):
    import requests

    from us_screener import data_source

    fields = ["0"] * 36
    fields[0], fields[1], fields[2] = "Apple", "312.06", "-0.14"
    fields[3] = "2026-05-29 16:00:00"
    fields[5], fields[6], fields[7] = "311.77", "315.0", "309.53"
    fields[10] = "70026752"  # volume
    fields[12] = "4583336313360"  # market cap
    fields[14] = "39.35"  # PE
    fields[30] = "21847030402"  # amount
    line = 'var hq_str_gb_aapl="' + ",".join(fields) + '";'

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.encoding = "utf-8"

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(line))
    quotes = data_source.fetch_sina_quotes(["AAPL"], pause=0.0)
    assert len(quotes) == 1
    row = quotes.iloc[0]
    assert row["symbol"] == "AAPL"
    assert float(row["last_price"]) == 312.06
    assert float(row["market_cap"]) == 4583336313360.0
    assert float(row["pe_ttm"]) == 39.35
    assert float(row["amount"]) == 21847030402.0


def test_localize_universe_free(tmp_path: Path, monkeypatch):
    from ah_screener.sources import us_client
    from us_screener import data_source

    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    master = pd.DataFrame(
        [
            {
                "market": "US", "symbol": "AAPL", "name": "Apple Inc", "asset_type": "stock",
                "board": "US Tech", "exchange": "NASDAQ", "currency": "USD", "status": "listed",
                "is_st": False, "is_hk_connect": False, "metadata_source": "test",
                "metadata_confidence": "high", "updated_at": pd.Timestamp("2026-05-29"),
            },
            {
                "market": "US", "symbol": "SPY", "name": "SPDR S&P 500", "asset_type": "etf",
                "board": "US ETF", "exchange": "NYSE", "currency": "USD", "status": "listed",
                "is_st": False, "is_hk_connect": False, "metadata_source": "test",
                "metadata_confidence": "high", "updated_at": pd.Timestamp("2026-05-29"),
            },
        ]
    )
    quotes = pd.DataFrame(
        [
            {"symbol": "AAPL", "last_price": 312.0, "pct_change": -0.1, "open": 311.0,
             "high": 315.0, "low": 309.0, "volume": 7.0e7, "amount": 2.18e10,
             "market_cap": 4.58e12, "pe_ttm": 39.35, "trade_date": pd.Timestamp("2026-05-29")},
            {"symbol": "SPY", "last_price": 756.0, "pct_change": 0.2, "open": 755.0,
             "high": 757.0, "low": 754.0, "volume": 5.0e7, "amount": 3.78e10,
             "market_cap": None, "pe_ttm": None, "trade_date": pd.Timestamp("2026-05-29")},
        ]
    )
    monkeypatch.setattr(us_client, "fetch_us_security_master", lambda: master)
    monkeypatch.setattr(data_source, "fetch_sina_quotes", lambda symbols, **k: quotes)

    out = data_source.localize_us_universe_free(store, include_etf=True)
    assert out["securities"] == 2
    assert out["snapshots"] == 2
    snap = store.query_df(
        "SELECT symbol, last_price, market_cap, source FROM market_snapshots WHERE symbol = 'AAPL'"
    ).iloc[0]
    assert float(snap["last_price"]) == 312.0
    assert float(snap["market_cap"]) == 4.58e12
    assert snap["source"] == "sina.gb"

    # include_etf=False must drop the ETF from the localized universe.
    store2 = Store(tmp_path / "us2.duckdb")
    store2.init_db()
    out2 = data_source.localize_us_universe_free(store2, include_etf=False)
    assert out2["securities"] == 1


def _write_stooq_zip(zpath: Path) -> None:
    import zipfile

    header = "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>"
    aapl = header + "\nAAPL.US,D,20250102,000000,180,182,179,181,1000000,0\nAAPL.US,D,20240102,000000,150,151,149,150,900000,0\n"
    spy = header + "\nSPY.US,D,20250102,000000,470,471,469,470,2000000,0\n"
    toyota = header + "\n7203.JP,D,20250102,000000,2800,2850,2790,2830,500000,0\n"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("data/daily/us/nasdaq stocks/1/aapl.us.txt", aapl)
        z.writestr("data/daily/us/nyse etfs/s/spy.us.txt", spy)
        z.writestr("data/daily/jp/tse stocks/7/7203.jp.txt", toyota)


def test_stooq_loader_synthetic(tmp_path: Path):
    from us_screener.stooq_loader import load_stooq_us_zip

    zpath = tmp_path / "d_us_txt.zip"
    _write_stooq_zip(zpath)
    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    out = load_stooq_us_zip(store, zpath, since="2024-06-01", include_etf=True)
    assert out["status"] == "ok"
    df = store.query_df("SELECT symbol, close FROM daily_prices WHERE source = 'stooq.d' ORDER BY symbol")
    assert {"AAPL", "SPY"} <= set(df["symbol"])
    assert "7203" not in set(df["symbol"])  # US-only wrapper excludes JP
    # the 'since' filter drops AAPL's 2024-01 bar, keeping only the 2025 one
    assert len(df[df["symbol"] == "AAPL"]) == 1
    assert float(df[df["symbol"] == "AAPL"].iloc[0]["close"]) == 181.0

    # include_etf=False must drop the ETF (path contains 'etfs')
    store2 = Store(tmp_path / "us2.duckdb")
    store2.init_db()
    load_stooq_us_zip(store2, zpath, since="2024-06-01", include_etf=False)
    syms2 = set(store2.query_df("SELECT DISTINCT symbol FROM daily_prices WHERE source='stooq.d'")["symbol"])
    assert "SPY" not in syms2 and "AAPL" in syms2


def test_stooq_path_market(tmp_path: Path):
    import zipfile

    from us_screener.stooq_loader import load_stooq_zip

    header = "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>"
    aave = header + "\nAAVE.V,D,20250102,000000,300,310,295,305,1000,0\n"
    zpath = tmp_path / "d_world_txt.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("data/daily/world/cryptocurrencies/a/aave.v.txt", aave)
    store = Store(tmp_path / "g.duckdb")
    store.init_db()
    out = load_stooq_zip(store, zpath, since="2024-06-01", path_market_map={"cryptocurrencies": "CRYPTO"})
    assert out["status"] == "ok"
    row = store.query_df("SELECT market, symbol FROM daily_prices").iloc[0]
    assert row["market"] == "CRYPTO" and row["symbol"] == "AAVE"


def test_global_screen_market(tmp_path: Path):
    from us_screener.global_screener import screen_market

    store = Store(tmp_path / "g.duckdb")
    store.init_db()
    rows = []
    start = pd.Timestamp("2025-06-01")
    for sym, drift in [("UPUP", 1.0), ("DOWN", -0.5)]:
        for i in range(130):
            close = 100.0 + drift * i
            rows.append({"market": "HK", "symbol": sym, "trade_date": start + pd.Timedelta(days=i),
                         "open": close, "high": close + 1, "low": close - 1, "close": max(close, 1.0),
                         "volume": 1000.0 + i, "amount": (1000.0 + i) * max(close, 1.0),
                         "adj_type": "stooq_adj", "source": "stooq.d", "updated_at": pd.Timestamp.now()})
    store.upsert_dataframe("daily_prices", pd.DataFrame(rows))
    out = screen_market(store, "HK", top=10, max_stale_days=0)
    assert out["market"] == "HK"
    assert out["universe"] >= 1
    syms = [c["symbol"] for c in out["candidates"]]
    # the uptrend name should rank ahead of the downtrend one
    assert syms and syms[0] == "UPUP"
    assert all("composite_score" in c and "technical_score" in c for c in out["candidates"])


def test_stooq_loader_multimarket(tmp_path: Path):
    from us_screener.stooq_loader import load_stooq_zip

    zpath = tmp_path / "d_world_txt.zip"
    _write_stooq_zip(zpath)
    store = Store(tmp_path / "world.duckdb")
    store.init_db()
    # load all markets; JP ticker -> market 'JP' derived from suffix
    out = load_stooq_zip(store, zpath, since="2024-06-01", delete_zip=True)
    assert out["status"] == "ok"
    assert out["symbols_by_market"].get("US", 0) >= 2
    assert out["symbols_by_market"].get("JP", 0) == 1
    jp = store.query_df("SELECT market, symbol FROM daily_prices WHERE symbol = '7203'").iloc[0]
    assert jp["market"] == "JP"
    assert not zpath.exists()  # delete_zip removed the archive


def test_consolidate_single_source_per_symbol(tmp_path: Path):
    from us_screener.stooq_loader import consolidate_history_sources

    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    rows = []
    base = {"market": "US", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
            "amount": 1.0, "updated_at": pd.Timestamp("2026-05-29")}
    # AAPL present in stooq + alpaca + akshare; ZZZZ only in alpaca (no stooq base)
    rows.append({**base, "symbol": "AAPL", "trade_date": pd.Timestamp("2026-05-20"), "adj_type": "stooq_adj", "source": "stooq.d"})
    rows.append({**base, "symbol": "AAPL", "trade_date": pd.Timestamp("2026-05-29"), "adj_type": "adjusted", "source": "alpaca.iex"})
    rows.append({**base, "symbol": "AAPL", "trade_date": pd.Timestamp("2026-05-18"), "adj_type": "raw", "source": "akshare.stock_us_daily"})
    rows.append({**base, "symbol": "ZZZZ", "trade_date": pd.Timestamp("2026-05-29"), "adj_type": "adjusted", "source": "alpaca.iex"})
    store.upsert_dataframe("daily_prices", pd.DataFrame(rows))

    out = consolidate_history_sources(store)
    assert out["status"] == "ok"
    df = store.query_df("SELECT symbol, source FROM daily_prices WHERE market='US'")
    aapl_src = set(df[df["symbol"] == "AAPL"]["source"])
    assert aapl_src == {"stooq.d"}  # stooq wins; alpaca+akshare dropped for AAPL
    zzzz_src = set(df[df["symbol"] == "ZZZZ"]["source"])
    assert zzzz_src == {"alpaca.iex"}  # kept — no stooq base for ZZZZ
    assert "akshare.stock_us_daily" not in set(df["source"])


def test_stooq_loader_rejects_sql_injection(tmp_path: Path):
    import pytest

    from us_screener.stooq_loader import load_stooq_zip

    zpath = tmp_path / "d.zip"
    _write_stooq_zip(zpath)
    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    with pytest.raises(ValueError):
        load_stooq_zip(store, zpath, since="2024'; DROP TABLE daily_prices;--")
    with pytest.raises(ValueError):
        load_stooq_zip(store, zpath, since="2024-01-01", markets=["US'); DROP TABLE daily_prices;--"])
    with pytest.raises(ValueError):
        load_stooq_zip(store, zpath, since="2024-01-01", market_map={"US": "x'); DROP--"})


def test_alpaca_history_skips_without_creds(tmp_path: Path, monkeypatch):
    from us_screener import data_source

    for var in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    store = Store(tmp_path / "us.duckdb")
    store.init_db()
    out = data_source.localize_us_history_alpaca(store, ["AAPL"], lookback_days=30)
    assert out["status"] == "skipped"
    assert out["rows"] == 0


def test_localize_history_free_parallel(tmp_path: Path, monkeypatch):
    from ah_screener.sources import us_client
    from us_screener import data_source

    store = Store(tmp_path / "us.duckdb")
    store.init_db()

    def _fake_hist(symbol, start_date, end_date, adjust=""):
        return pd.DataFrame(
            [
                {
                    "market": "US", "symbol": symbol, "trade_date": pd.Timestamp("2026-05-29"),
                    "open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5, "volume": 100.0,
                    "amount": 150.0, "adj_type": "raw", "source": "test",
                    "updated_at": pd.Timestamp("2026-05-29"),
                }
            ]
        )

    monkeypatch.setattr(us_client, "fetch_us_history", _fake_hist)
    out = data_source.localize_us_history_free(store, ["AAPL", "NVDA", "MSFT"], lookback_days=90)
    assert out["symbols_ok"] == 3
    assert out["symbols_failed"] == 0
    rows = int(store.query_df("SELECT COUNT(*) c FROM daily_prices WHERE market = 'US'").iloc[0]["c"])
    assert rows == 3


def test_valuation_enrich_updates_snapshots(tmp_path: Path, monkeypatch):
    from us_screener import valuation_enrich

    store = _seed_store(tmp_path)
    # BABA seeded with pe/pb/market_cap already; AAPL too. Null them to test fill.
    store.execute(
        "UPDATE market_snapshots SET market_cap = NULL, pe_ttm = NULL, pb = NULL WHERE market = 'US'"
    )
    monkeypatch.setattr(
        valuation_enrich,
        "_fetch_one",
        lambda symbol: {"market_cap": 1.0e12, "pe_ttm": 25.0, "pb": 8.0},
    )
    out = valuation_enrich.enrich_us_valuation(store, limit=50)
    assert out["status"] == "ok"
    assert out["updated"] >= 1
    assert out["rate_limited"] is False
    filled = store.query_df(
        "SELECT market_cap, pe_ttm, pb FROM market_snapshots WHERE symbol = 'AAPL' AND market = 'US'"
    )
    assert float(filled.iloc[0]["market_cap"]) == 1.0e12
    assert float(filled.iloc[0]["pe_ttm"]) == 25.0


def test_valuation_enrich_rate_limit_stops_early(tmp_path: Path, monkeypatch):
    from us_screener import valuation_enrich

    store = _seed_store(tmp_path)
    store.execute("UPDATE market_snapshots SET market_cap = NULL WHERE market = 'US'")

    class _RateLimit(Exception):
        pass

    _RateLimit.__name__ = "YFRateLimitError"

    def _boom(symbol):
        raise _RateLimit("Too Many Requests. Rate limited.")

    monkeypatch.setattr(valuation_enrich, "_fetch_one", _boom)
    out = valuation_enrich.enrich_us_valuation(store, limit=50)
    assert out["status"] == "ok"
    assert out["updated"] == 0
    assert out["rate_limited"] is True


def test_sec_derive_valuation(tmp_path: Path, monkeypatch):
    from ah_screener.sources import us_client
    from us_screener import valuation_enrich

    store = _seed_store(tmp_path)
    store.execute(
        "UPDATE market_snapshots SET market_cap = NULL, pe_ttm = NULL, pb = NULL WHERE market = 'US'"
    )
    # Give AAPL real equity / net income so PB and PE get derived too.
    store.execute(
        "UPDATE financial_metrics SET total_equity = 1.0e10, parent_net_profit = 1.0e10 "
        "WHERE symbol = 'AAPL' AND market = 'US'"
    )
    monkeypatch.setattr(
        us_client,
        "fetch_sec_company_tickers",
        lambda: {sym: {"cik_str": idx + 1} for idx, sym in enumerate(["AAPL", "NVDA", "BABA", "IONQ"])},
    )
    monkeypatch.setattr(valuation_enrich, "_sec_shares_outstanding", lambda cik: 1.0e9)

    out = valuation_enrich.derive_us_valuation_sec(store, limit=50, pause=0.0)
    assert out["status"] == "ok"
    assert out["updated"] >= 1
    aapl = store.query_df(
        "SELECT market_cap, pe_ttm, pb FROM market_snapshots WHERE symbol = 'AAPL' AND market = 'US'"
    ).iloc[0]
    assert float(aapl["market_cap"]) == 210.0 * 1.0e9  # last_price x shares
    assert float(aapl["pb"]) == (210.0 * 1.0e9) / 1.0e10
    assert float(aapl["pe_ttm"]) == (210.0 * 1.0e9) / 1.0e10


def test_enrich_all_prefers_sec_then_yfinance(tmp_path: Path, monkeypatch):
    from ah_screener.sources import us_client
    from us_screener import valuation_enrich

    store = _seed_store(tmp_path)
    store.execute("UPDATE market_snapshots SET market_cap = NULL, pe_ttm = NULL, pb = NULL WHERE market = 'US'")
    monkeypatch.setattr(
        us_client,
        "fetch_sec_company_tickers",
        lambda: {"AAPL": {"cik_str": 1}},  # only AAPL has a CIK
    )
    monkeypatch.setattr(valuation_enrich, "_sec_shares_outstanding", lambda cik: 2.0e9)
    # yfinance tops up the names SEC could not fill.
    monkeypatch.setattr(
        valuation_enrich, "_fetch_one", lambda symbol: {"market_cap": 5.0e9, "pe_ttm": 18.0, "pb": 3.0}
    )
    out = valuation_enrich.enrich_us_valuation_all(store, sec_limit=50, yf_limit=50)
    assert out["status"] == "ok"
    assert out["sec"]["updated"] >= 1
    assert out["yfinance"]["updated"] >= 1
    assert out["updated"] == out["sec"]["updated"] + out["yfinance"]["updated"]


def test_valuation_enrich_empty_store(tmp_path: Path):
    from us_screener import valuation_enrich

    store = Store(tmp_path / "empty.duckdb")
    store.init_db()
    out = valuation_enrich.enrich_us_valuation(store, limit=10)
    assert out["status"] == "empty"
    assert out["updated"] == 0


def test_mcp_server_build_or_skip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("US_SCREENER_DB", str(tmp_path / "us.duckdb"))
    monkeypatch.setenv("AH_SCREENER_DB", str(tmp_path / "us.duckdb"))
    from us_screener.mcp_server import create_mcp_server

    try:
        server = create_mcp_server()
    except RuntimeError as exc:
        assert "mcp" in str(exc).lower()
        pytest.skip("mcp extra not installed")
    else:
        assert hasattr(server, "run")
