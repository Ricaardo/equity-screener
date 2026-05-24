from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.potential import (
    _price_features,
    _setup_scores,
    scan_potential_candidates,
    sweep_potential_thresholds,
    validate_potential_signals,
    walk_forward_potential_thresholds,
)


def _prices_from_closes(symbol: str, closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=len(closes))
    return pd.DataFrame(
        {
            "market": "A",
            "symbol": symbol,
            "trade_date": dates,
            "open": closes,
            "high": [c * 1.05 for c in closes],
            "low": [c * 0.95 for c in closes],
            "close": closes,
            "volume": 1_000_000.0,
            "amount": 50_000_000.0,
            "adj_type": "qfq",
            "source": "test",
        }
    )


def _make_prices(symbol: str, start: float, drift: float, periods: int = 190) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=periods)
    closes = [start + i * drift for i in range(periods)]
    return pd.DataFrame(
        {
            "market": "A",
            "symbol": symbol,
            "trade_date": dates,
            "open": closes,
            "high": [c * 1.05 for c in closes],
            "low": [c * 0.95 for c in closes],
            "close": closes,
            "volume": 1_000_000.0,
            "amount": 50_000_000.0,
            "adj_type": "qfq",
            "source": "test",
        }
    )


class PotentialScannerTest(TestCase):
    def test_validate_price_only_signals_returns_bias_note(self) -> None:
        prices = pd.concat(
            [_make_prices("AAA", 10, 0.02, 210), _make_prices("BBB", 12, -0.01, 210)],
            ignore_index=True,
        )
        validation = validate_potential_signals(prices)
        self.assertIn("signal", validation.columns)
        if not validation.empty:
            self.assertIn("survivorship", validation.iloc[0]["bias_note"])

    def test_setup_score_does_not_use_future_data(self) -> None:
        # The setup definition at date T must not change when data AFTER T changes
        # (guards against look-ahead, master-plan R1).
        base = [10 + i * 0.02 for i in range(200)]
        spiked = base[:160] + [c * 5 for c in base[160:]]  # huge spike only after index 160
        f_base = _setup_scores(_price_features(_prices_from_closes("AAA", base)))
        f_spiked = _setup_scores(_price_features(_prices_from_closes("AAA", spiked)))
        anchor = pd.bdate_range("2025-01-01", periods=200)[150]
        b = f_base[f_base["trade_date"].eq(anchor)]["base_setup"].iloc[0]
        s = f_spiked[f_spiked["trade_date"].eq(anchor)]["base_setup"].iloc[0]
        self.assertAlmostEqual(float(b), float(s), places=6)

    def test_fundamental_turn_pillar_lifts_score(self) -> None:
        from ah_screener.potential import _fundamental_turn_scores

        fund = pd.DataFrame(
            [
                {"market": "A", "symbol": "AAA", "snapshot_date": "2026-05-23",
                 "fundamental_trend_score": 90.0, "growth_score": 90.0},
                {"market": "A", "symbol": "BBB", "snapshot_date": "2026-05-23",
                 "fundamental_trend_score": 10.0, "growth_score": 10.0},
            ]
        )
        turn = _fundamental_turn_scores(fund)
        self.assertAlmostEqual(turn[("A", "AAA")], 90.0, places=1)
        self.assertAlmostEqual(turn[("A", "BBB")], 10.0, places=1)
        # missing fundamentals -> empty map (caller defaults to neutral 50)
        self.assertEqual(_fundamental_turn_scores(pd.DataFrame()), {})

    def test_weight_profiles_are_market_specific(self) -> None:
        from ah_screener.potential import WEIGHT_PROFILES

        for mkt in ("A", "HK", "US"):
            self.assertAlmostEqual(sum(WEIGHT_PROFILES[mkt].values()), 1.0, places=6)
        self.assertGreater(WEIGHT_PROFILES["US"]["fundamental"], WEIGHT_PROFILES["A"]["fundamental"])

    def test_threshold_sweep_returns_grid_with_stats(self) -> None:
        prices = pd.concat(
            [_make_prices(f"S{i}", 10 + i, 0.02 + i * 0.001, 210) for i in range(6)],
            ignore_index=True,
        )
        sweep = sweep_potential_thresholds(prices, rank_cuts=(50.0, 70.0), ret_caps=(0.35,))
        if not sweep.empty:
            self.assertIn("rs_rank_cut", sweep.columns)
            self.assertIn("median_excess_40d", sweep.columns)
            self.assertTrue((sweep["rs_rank_cut"].isin([50.0, 70.0])).all())

    def test_walk_forward_thresholds_returns_evidence_columns(self) -> None:
        prices = pd.concat(
            [_make_prices(f"S{i}", 10 + i, 0.015 + i * 0.001, 260) for i in range(8)],
            ignore_index=True,
        )
        result = walk_forward_potential_thresholds(prices, folds=2, min_train_samples=1)
        self.assertIn("test_median_excess_40d", result.columns)
        self.assertIn("bias_note", result.columns)
        if not result.empty:
            self.assertIn("walk-forward", result.iloc[0]["bias_note"])

    def test_scan_potential_candidates_respects_stock_asset_type(self) -> None:
        prices = pd.concat(
            [_make_prices("AAA", 10, 0.02, 210), _make_prices("ETF1", 10, 0.02, 210)],
            ignore_index=True,
        )
        snapshots = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "AAA",
                    "trade_date": pd.Timestamp("2025-10-01"),
                    "name": "测试股票",
                    "asset_type": "stock",
                    "amount": 100_000_000,
                },
                {
                    "market": "A",
                    "symbol": "ETF1",
                    "trade_date": pd.Timestamp("2025-10-01"),
                    "name": "测试ETF",
                    "asset_type": "etf",
                    "amount": 100_000_000,
                },
            ]
        )
        out = scan_potential_candidates(prices, snapshots, top=10)
        self.assertNotIn("ETF1", set(out.get("symbol", [])))
        expected = {
            "potential_score",
            "technical_setup_score",
            "relative_strength_score",
            "pivot_price",
            "target_price",
            "stop_price",
            "scenario_json",
        }
        self.assertTrue(expected.issubset(out.columns) or out.empty)
