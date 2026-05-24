from __future__ import annotations

from pathlib import Path
from unittest import TestCase

import pandas as pd

from ah_screener.config import Settings
from ah_screener.expert_model import run_expert_model
from ah_screener.scoring import _risk_penalty


_SETTINGS = Settings(db_path=Path("test.duckdb"))


def _snap(market: str, symbol: str, name: str, price: float, amount: float) -> dict[str, object]:
    return {
        "market": market,
        "symbol": symbol,
        "trade_date": "2026-05-20",
        "name": name,
        "board": "主板" if market == "A" else market,
        "last_price": price,
        "amount": amount,
        "pe_ttm": 15,
        "pb": 2,
        "market_cap": 50_000_000_000,
    }


class RiskGateTest(TestCase):
    """P2-1: lifecycle overlap, HK/US penny and distress-name rules."""

    def test_lifecycle_overlap_is_hard_flagged(self) -> None:
        row = pd.Series(_snap("A", "600519", "贵州茅台", 1600, 5_000_000_000))
        clean, _ = _risk_penalty(row, _SETTINGS)
        flagged, reasons = _risk_penalty(
            row, _SETTINGS, delisted_keys=frozenset({("A", "600519")})
        )
        self.assertEqual(clean, 0.0)
        self.assertGreaterEqual(flagged, 100.0)
        self.assertTrue(any("生命周期" in r for r in reasons))

    def test_hk_penny_and_us_penny_penalized(self) -> None:
        hk_penny, hk_reasons = _risk_penalty(
            pd.Series(_snap("HK", "08000", "仙股", 0.2, 50_000_000)), _SETTINGS
        )
        us_penny, us_reasons = _risk_penalty(
            pd.Series(_snap("US", "ABCD", "PennyCo", 0.4, 50_000_000)), _SETTINGS
        )
        self.assertGreater(hk_penny, 0.0)
        self.assertGreater(us_penny, 0.0)
        self.assertTrue(any("仙股" in r for r in hk_reasons))
        self.assertTrue(any("退市风险" in r for r in us_reasons))

    def test_distress_name_penalized_in_any_market(self) -> None:
        penalty, reasons = _risk_penalty(
            pd.Series(_snap("HK", "01234", "某某清盘重整", 5.0, 50_000_000)), _SETTINGS
        )
        self.assertGreater(penalty, 0.0)
        self.assertTrue(any("清盘" in r or "风险词" in r for r in reasons))


class MissingDataDiscountTest(TestCase):
    """P2-2: missing data is uncertainty, not neutrality."""

    def _run(self, technicals: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.Series:
        snap = pd.DataFrame([_snap("A", "600519", "贵州茅台", 1600, 5_000_000_000)])
        results, _ = run_expert_model(
            snapshots=snap,
            tags=pd.DataFrame(),
            technicals=technicals,
            fundamentals=fundamentals,
            settings=_SETTINGS,
        )
        return results.iloc[0]

    def test_missing_both_penalized_more_than_partial(self) -> None:
        tech = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "600519",
                    "snapshot_date": "2026-05-20",
                    "technical_score": 60.0,
                    "technical_signal": "ok",
                    "rsi14": 55.0,
                    "return_20d": 0.05,
                }
            ]
        )
        fund = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "600519",
                    "snapshot_date": "2026-05-20",
                    "fundamental_score": 70.0,
                }
            ]
        )
        complete = self._run(tech, fund)
        missing_both = self._run(pd.DataFrame(), pd.DataFrame())
        self.assertGreater(
            float(missing_both["risk_score"]), float(complete["risk_score"])
        )
        # An unevaluable name must not outrank a data-complete one.
        self.assertGreater(float(complete["expert_score"]), float(missing_both["expert_score"]))


class ExpertRiskTest(TestCase):
    def test_document_risk_tags_increase_expert_risk_score(self) -> None:
        snapshots = pd.DataFrame(
            [
                {
                    "market": "HK",
                    "symbol": "00700",
                    "trade_date": "2026-05-15",
                    "name": "TENCENT",
                    "board": "HK Main",
                    "last_price": 400,
                    "amount": 100_000_000,
                    "pe_ttm": 20,
                    "pb": 4,
                    "market_cap": 4_000_000_000_000,
                }
            ]
        )
        tags = pd.DataFrame(
            [
                {
                    "market": "HK",
                    "symbol": "00700",
                    "tag_type": "risk",
                    "tag_name": "异常审计意见",
                }
            ]
        )

        results, _ = run_expert_model(
            snapshots=snapshots,
            tags=tags,
            technicals=pd.DataFrame(),
            fundamentals=pd.DataFrame(),
            settings=Settings(db_path=Path("test.duckdb")),
        )

        self.assertGreaterEqual(float(results.iloc[0]["risk_score"]), 30.0)
        self.assertIn("审计意见异常", str(results.iloc[0]["reasons"]))
