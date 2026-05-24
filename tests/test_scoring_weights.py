from __future__ import annotations

from pathlib import Path
from unittest import TestCase

import pandas as pd

from ah_screener.config import Settings
from ah_screener.expert_model import (
    HOT_THEMES,
    THEME_BY_NAME,
    THEME_PRIORITY,
    _style_bucket,
    run_expert_model,
)


def _baseline_results() -> pd.DataFrame:
    snap = pd.DataFrame(
        [
            {
                "market": "A",
                "symbol": "600519",
                "trade_date": "2026-05-20",
                "name": "贵州茅台",
                "board": "主板",
                "last_price": 1600,
                "amount": 5_000_000_000,
                "pe_ttm": 30,
                "pb": 9,
                "market_cap": 2_000_000_000_000,
            },
            {
                "market": "A",
                "symbol": "000001",
                "trade_date": "2026-05-20",
                "name": "平安银行",
                "board": "主板",
                "last_price": 11,
                "amount": 2_000_000_000,
                "pe_ttm": 5,
                "pb": 0.6,
                "market_cap": 200_000_000_000,
            },
            {
                "market": "HK",
                "symbol": "00700",
                "trade_date": "2026-05-20",
                "name": "TENCENT",
                "board": "港股通",
                "last_price": 400,
                "amount": 8_000_000_000,
                "pe_ttm": 20,
                "pb": 4,
                "market_cap": 4_000_000_000_000,
            },
        ]
    )
    tags = pd.DataFrame(
        [
            {"market": "A", "symbol": "600519", "tag_type": "concept", "tag_name": "高股息"},
            {
                "market": "HK",
                "symbol": "00700",
                "tag_type": "concept",
                "tag_name": "AI 互联网 平台 算力",
            },
        ]
    )
    results, _ = run_expert_model(
        snapshots=snap,
        tags=tags,
        technicals=pd.DataFrame(),
        fundamentals=pd.DataFrame(),
        settings=Settings(db_path=Path("x.duckdb")),
    )
    return results.set_index("symbol")


class CompositeCharacterizationTest(TestCase):
    """Locks the expert_score composition so the weights refactor cannot drift numbers.

    Inputs deliberately omit technicals/fundamentals, so ``risk_score`` reflects the
    missing-data penalty in force when these values were captured. If the missing-data
    discount changes (P2-2), update the affected rows here intentionally.
    """

    # Re-locked after P2-2 raised the missing-data discount (these rows have no
    # technicals/fundamentals, so risk_score = 12 + 14 + 8 = 34).
    EXPECTED = {
        "600519": {"expert": 18.0076, "master": 52.1947, "china": 52.6677, "risk": 34.0},
        "000001": {"expert": 19.5032, "master": 56.3613, "china": 58.1877, "risk": 34.0},
        "00700": {"expert": 25.5344, "master": 65.3530, "china": 59.2277, "risk": 34.0},
    }

    def test_composite_scores_are_stable(self) -> None:
        results = _baseline_results()
        for symbol, expected in self.EXPECTED.items():
            row = results.loc[symbol]
            self.assertAlmostEqual(float(row["expert_score"]), expected["expert"], places=3)
            self.assertAlmostEqual(float(row["master_score"]), expected["master"], places=3)
            self.assertAlmostEqual(
                float(row["china_master_score"]), expected["china"], places=3
            )
            self.assertAlmostEqual(float(row["risk_score"]), expected["risk"], places=3)


class ThemeMetadataTest(TestCase):
    """P2-6: theme priority/style live on the HotTheme registry, not scattered."""

    def test_priority_is_derived_and_complete(self) -> None:
        self.assertEqual(set(THEME_PRIORITY), {t.name for t in HOT_THEMES})
        self.assertEqual(len(THEME_PRIORITY), len(HOT_THEMES))

    def test_style_bucket_reads_from_registry(self) -> None:
        import pandas as pd

        row = pd.Series({"valuation_score": 50.0, "fundamental_score": 50.0})
        self.assertEqual(_style_bucket(row, ["高股息央国企防御"]), "红利防御")
        self.assertEqual(
            _style_bucket(row, ["高股息央国企防御"]),
            THEME_BY_NAME["高股息央国企防御"].style_bucket,
        )
        # 科技成长 gets the valuation override only above the threshold.
        hot = pd.Series({"valuation_score": 80.0, "fundamental_score": 50.0})
        self.assertEqual(_style_bucket(hot, ["AI算力硬件"]), "科技成长偏估值")
        self.assertEqual(_style_bucket(row, ["AI算力硬件"]), "科技成长")
