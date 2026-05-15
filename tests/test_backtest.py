from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import pandas as pd

from ah_screener import pipeline
from ah_screener.expert_model import STRATEGY_NAME
from ah_screener.storage import Store


class BacktestTest(TestCase):
    def test_outputs_benchmark_and_excess_columns(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "test.duckdb")
            store.init_db()
            store.upsert_dataframe(
                "refined_candidates",
                pd.DataFrame(
                    [
                        {
                            "snapshot_date": "2026-01-01",
                            "strategy": STRATEGY_NAME,
                            "bucket": "科技",
                            "rank_in_bucket": 1,
                            "market": "A",
                            "symbol": "000001",
                            "name": "测试股份",
                            "expert_score": 80,
                            "fundamental_score": 75,
                            "technical_score": 70,
                            "industry_peer_group": "科技",
                            "peer_score": 70,
                            "industry_fit_score": 70,
                        },
                        {
                            "snapshot_date": "2026-04-01",
                            "strategy": STRATEGY_NAME,
                            "bucket": "科技",
                            "rank_in_bucket": 1,
                            "market": "A",
                            "symbol": "000001",
                            "name": "测试股份",
                            "expert_score": 80,
                            "fundamental_score": 75,
                            "technical_score": 70,
                            "industry_peer_group": "科技",
                            "peer_score": 70,
                            "industry_fit_score": 70,
                        },
                    ]
                ),
            )
            price_rows = []
            for symbol, closes, adj_type in [
                ("000001", [10.0, 11.0, 12.0], "qfq"),
                ("000300", [100.0, 102.0, 104.0], "benchmark"),
            ]:
                for trade_date, close in zip(
                    ["2026-01-01", "2026-04-01", "2026-05-01"], closes
                ):
                    price_rows.append(
                        {
                            "market": "A",
                            "symbol": symbol,
                            "trade_date": trade_date,
                            "open": close,
                            "high": close,
                            "low": close,
                            "close": close,
                            "volume": 1,
                            "amount": 1,
                            "adj_type": adj_type,
                            "source": "test",
                        }
                    )
            store.upsert_dataframe("daily_prices", pd.DataFrame(price_rows))
            original_get_store = pipeline.get_store
            pipeline.get_store = lambda: store
            try:
                result = pipeline.backtest_refined_candidates(
                    rebalance="snapshot",
                    fee_bps=0,
                    slippage_bps=0,
                    benchmark="A:000300",
                )
            finally:
                pipeline.get_store = original_get_store

        self.assertEqual(list(result["benchmark"]), ["A:000300", "A:000300"])
        self.assertEqual(result["period_return"].round(6).tolist(), [0.1, 0.090909])
        self.assertEqual(result["benchmark_return"].round(6).tolist(), [0.02, 0.019608])
        self.assertEqual(result["excess_return"].round(6).tolist(), [0.08, 0.071301])
        self.assertEqual(result["benchmark_equity"].round(0).tolist(), [1_020_000.0, 1_040_000.0])

    def test_backfills_historical_refined_snapshots_from_real_prices(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "test.duckdb")
            store.init_db()
            store.upsert_dataframe(
                "expert_screening_results",
                pd.DataFrame(
                    [
                        {
                            "snapshot_date": "2026-05-01",
                            "strategy": STRATEGY_NAME,
                            "market": "A",
                            "symbol": "000001",
                            "name": "测试股份",
                            "expert_score": 80,
                            "master_score": 80,
                            "china_master_score": 80,
                            "fundamental_score": 75,
                            "detailed_industry": "测试行业",
                            "industry_peer_group": "测试行业",
                            "peer_score": 70,
                            "industry_fit_score": 70,
                            "valuation_percentile": 60,
                            "theme_score": 70,
                            "technical_score": 70,
                            "liquidity_score": 70,
                            "valuation_score": 60,
                            "risk_score": 10,
                            "decision": "core_candidate",
                            "theme_matches": "[\"AI算力硬件\"]",
                            "reasons": "[]",
                        }
                    ]
                ),
            )
            price_rows = []
            for trade_date, close in [
                ("2026-01-02", 10.0),
                ("2026-02-02", 11.0),
                ("2026-03-02", 12.0),
                ("2026-04-02", 13.0),
                ("2026-05-01", 14.0),
            ]:
                price_rows.append(
                    {
                        "market": "A",
                        "symbol": "000001",
                        "trade_date": trade_date,
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "volume": 1,
                        "amount": 1,
                        "adj_type": "qfq",
                        "source": "test",
                    }
                )
            store.upsert_dataframe("daily_prices", pd.DataFrame(price_rows))
            original_get_store = pipeline.get_store
            pipeline.get_store = lambda: store
            try:
                inserted = pipeline.backfill_refined_candidate_snapshots(
                    min_snapshots=2,
                    rebalance="monthly",
                )
                refined = store.query_df("SELECT * FROM refined_candidates")
            finally:
                pipeline.get_store = original_get_store

        self.assertGreater(inserted, 0)
        self.assertGreaterEqual(refined["snapshot_date"].nunique(), 1)
