from __future__ import annotations

import json
from unittest import TestCase

import pandas as pd

from ah_screener.expert_model import STRATEGY_NAME, refine_candidates


def _candidate(
    market: str,
    symbol: str,
    name: str,
    score: float,
    theme: str,
    style_value: float = 40,
) -> dict[str, object]:
    return {
        "snapshot_date": "2026-05-14",
        "strategy": STRATEGY_NAME,
        "market": market,
        "symbol": symbol,
        "name": name,
        "canonical_id": None,
        "expert_score": score,
        "fundamental_score": 75,
        "technical_score": 70,
        "detailed_industry": "测试行业",
        "valuation_percentile": 70,
        "liquidity_score": 70,
        "valuation_score": style_value,
        "peer_score": 70,
        "industry_fit_score": 70,
        "industry_peer_group": "测试行业",
        "decision": "core_candidate",
        "theme_matches": json.dumps([theme], ensure_ascii=False),
        "reasons": "[]",
    }


class RefineCandidatesTest(TestCase):
    def test_deduplicates_dual_listings_and_caps_bucket(self) -> None:
        results = pd.DataFrame(
            [
                _candidate("A", "002594", "比亚迪", 82, "汽车智能化与出海"),
                _candidate("HK", "01211", "比亚迪股份", 88, "汽车智能化与出海"),
                _candidate("A", "000001", "测试银行", 75, "高股息央国企防御"),
                _candidate("A", "000002", "测试地产", 74, "高股息央国企防御"),
                _candidate("A", "000003", "测试资源", 73, "高股息央国企防御"),
            ]
        )

        refined = refine_candidates(results, max_per_bucket=2, max_per_style=1)

        byd = refined[refined["peer_group"].eq("比亚迪AH")]
        high_dividend = refined[refined["bucket"].eq("高股息央国企防御")]

        self.assertEqual(len(byd), 1)
        self.assertEqual(byd.iloc[0]["market"], "HK")
        self.assertEqual(len(high_dividend), 2)
        self.assertTrue(refined["selection_note"].str.contains("A/H或同名主体只留最高分").all())

    def test_deduplicates_by_canonical_id_across_hk_and_us(self) -> None:
        results = pd.DataFrame(
            [
                {
                    **_candidate("HK", "09988", "阿里巴巴-W", 78, "港股AI互联网平台"),
                    "canonical_id": "阿里巴巴",
                },
                {
                    **_candidate("US", "BABA", "Alibaba", 82, "港股AI互联网平台"),
                    "canonical_id": "阿里巴巴",
                },
            ]
        )

        refined = refine_candidates(results, max_per_bucket=3)

        self.assertEqual(len(refined), 1)
        self.assertEqual(refined.iloc[0]["market"], "US")
        self.assertEqual(refined.iloc[0]["peer_group"], "阿里巴巴")
