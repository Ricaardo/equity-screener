from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import pandas as pd

from ah_screener import pipeline
from ah_screener.storage import Store


def _sec(market: str) -> pd.DataFrame:
    return pd.DataFrame([{"market": market, "symbol": "X1", "name": f"{market} name"}])


def _snap(market: str) -> pd.DataFrame:
    return pd.DataFrame(
        [{"market": market, "symbol": "X1", "trade_date": "2026-05-23", "source": "test", "name": "n"}]
    )


class SyncSpotResilienceTest(TestCase):
    def test_sync_spot_all_tolerates_single_market_failure(self) -> None:
        empty = (pd.DataFrame(), pd.DataFrame())
        originals = (
            pipeline.fetch_spot,
            pipeline.fetch_a_etf_spot,
            pipeline.fetch_hk_etf_spot,
            pipeline.get_store,
        )
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.duckdb")
            store.init_db()

            def fake_fetch_spot(market):
                if market == "HK":
                    raise RuntimeError("transient akshare HK spot failure")
                return _sec(market), _snap(market)

            pipeline.fetch_spot = fake_fetch_spot
            pipeline.fetch_a_etf_spot = lambda: empty
            pipeline.fetch_hk_etf_spot = lambda: empty
            pipeline.get_store = lambda: store
            try:
                result = pipeline.sync_spot("all")
            finally:
                (
                    pipeline.fetch_spot,
                    pipeline.fetch_a_etf_spot,
                    pipeline.fetch_hk_etf_spot,
                    pipeline.get_store,
                ) = originals

        # HK failed but is recorded, not raised; A and US still ingested.
        self.assertEqual(result.get("HK_failed"), 1)
        self.assertIn("transient", result.get("HK_error", ""))
        self.assertEqual(result.get("A_snapshots"), 1)
        self.assertEqual(result.get("US_snapshots"), 1)

    def test_sync_fundamentals_carries_forward_fresh(self) -> None:
        originals = (pipeline.fetch_fundamentals, pipeline.get_store)
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.duckdb")
            store.init_db()
            # Current spot at a new date; a fresh prior fundamentals row at an older date.
            store.upsert_dataframe(
                "market_snapshots",
                pd.DataFrame([{"market": "A", "symbol": "600000", "trade_date": "2026-05-23",
                               "asset_type": "stock", "amount": 1e9, "source": "t", "name": "n"}]),
            )
            store.upsert_dataframe(
                "financial_metrics",
                pd.DataFrame([{"market": "A", "symbol": "600000", "snapshot_date": "2026-02-01",
                               "report_date": "2025-12-31", "name": "n", "roe": 12.0,
                               "updated_at": pd.Timestamp.now()}]),
            )

            def boom(*a, **k):
                raise AssertionError("fetch_fundamentals must not be called for a fresh name")

            pipeline.fetch_fundamentals = boom
            pipeline.get_store = lambda: store
            try:
                result = pipeline.sync_fundamentals("A", top=10)
                rows = store.query_df(
                    "SELECT * FROM financial_metrics WHERE snapshot_date = DATE '2026-05-23'"
                )
            finally:
                pipeline.fetch_fundamentals, pipeline.get_store = originals

        self.assertEqual(result.get("A_fundamentals_carried"), 1)
        self.assertEqual(result.get("A_fundamentals_fetched"), 0)
        self.assertEqual(len(rows), 1)  # carried forward to the new snapshot_date
        self.assertEqual(float(rows.iloc[0]["roe"]), 12.0)

    def test_sync_history_skips_names_already_current(self) -> None:
        originals = (pipeline.fetch_history, pipeline.get_store)
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.duckdb")
            store.init_db()
            store.upsert_dataframe(
                "market_snapshots",
                pd.DataFrame([{"market": "A", "symbol": "600000", "trade_date": "2026-05-23",
                               "asset_type": "stock", "amount": 1e9, "source": "t", "name": "n"}]),
            )
            store.upsert_dataframe(
                "daily_prices",
                pd.DataFrame([{"market": "A", "symbol": "600000", "trade_date": "2026-05-23",
                               "close": 10.0, "adj_type": "qfq", "source": "t"}]),
            )

            def boom(*a, **k):
                raise AssertionError("fetch_history must not be called for a current name")

            pipeline.fetch_history = boom
            pipeline.get_store = lambda: store
            try:
                result = pipeline.sync_history("A", top=10, include_etf=False)
            finally:
                pipeline.fetch_history, pipeline.get_store = originals

        self.assertEqual(result.get("A_history_skipped"), 1)
        self.assertEqual(result.get("A_history_rows"), 0)

    def test_run_full_update_continues_when_a_step_fails(self) -> None:
        # A flaky step (e.g. board tags) must not abort the refresh — later steps still run.
        names = [
            "sync_spot", "sync_a_tags", "sync_curated_theme_tags", "sync_identity_mappings",
            "sync_history", "sync_benchmarks", "run_technical_indicators", "sync_fundamentals",
            "run_expert_scores", "compute_industry_valuation_stats", "run_potential_scan",
            "generate_report",
        ]
        originals = {n: getattr(pipeline, n) for n in names}
        try:
            for n in names:
                setattr(pipeline, n, lambda *a, **k: {})

            def boom(*a, **k):
                raise RuntimeError("akshare board tags down")

            pipeline.sync_a_tags = boom
            result = pipeline.run_full_update(include_fundamentals=True, include_report=True)
        finally:
            for n, fn in originals.items():
                setattr(pipeline, n, fn)

        self.assertIn("failed", result["a_industry_tags"])
        self.assertIn("report", result)  # downstream step still ran despite the tags failure
        self.assertIn("expert_scores", result)

    def test_sync_spot_single_market_still_raises(self) -> None:
        originals = (pipeline.fetch_spot, pipeline.get_store)
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.duckdb")
            store.init_db()

            def boom(market):
                raise RuntimeError("boom")

            pipeline.fetch_spot = boom
            pipeline.get_store = lambda: store
            try:
                with self.assertRaises(RuntimeError):
                    pipeline.sync_spot("HK")
            finally:
                pipeline.fetch_spot, pipeline.get_store = originals


class SyncTagsIncrementalTest(TestCase):
    def test_sync_a_tags_skips_when_fresh(self) -> None:
        originals = (pipeline.fetch_a_board_tags, pipeline.get_store)
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.duckdb")
            store.init_db()
            store.upsert_dataframe(
                "company_tags",
                pd.DataFrame([{"market": "A", "symbol": "600000", "tag_type": "industry",
                               "tag_name": "银行", "evidence_level": "A", "source": "test",
                               "updated_at": pd.Timestamp.now()}]),
            )

            def boom(*a, **k):
                raise AssertionError("fetch_a_board_tags must not be called when fresh")

            pipeline.fetch_a_board_tags = boom
            pipeline.get_store = lambda: store
            try:
                fresh = pipeline.sync_a_tags("industry", None)
            finally:
                pipeline.fetch_a_board_tags, pipeline.get_store = originals
        self.assertEqual(fresh, 0)
