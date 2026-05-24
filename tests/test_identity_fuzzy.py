from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.identity import default_identity_mappings, derive_fuzzy_identity_mappings


class FuzzyIdentityTest(TestCase):
    def test_links_same_name_across_markets(self) -> None:
        # Equal normalized-name key across two markets -> one fuzzy canonical link.
        securities = pd.DataFrame(
            [
                {"market": "A", "symbol": "601012", "name": "隆基绿能科技股份有限公司"},
                {"market": "HK", "symbol": "09012", "name": "隆基绿能科技"},
            ]
        )
        result = derive_fuzzy_identity_mappings(securities)
        self.assertFalse(result.empty)
        self.assertEqual(result["canonical_id"].nunique(), 1)
        self.assertEqual(set(result["market"]), {"A", "HK"})
        self.assertTrue((result["confidence"] == "fuzzy").all())

    def test_single_market_collision_does_not_link(self) -> None:
        securities = pd.DataFrame(
            [
                {"market": "A", "symbol": "000001", "name": "测试科技"},
                {"market": "A", "symbol": "000002", "name": "测试科技"},
            ]
        )
        self.assertTrue(derive_fuzzy_identity_mappings(securities).empty)

    def test_generic_stopword_names_excluded(self) -> None:
        # Names that reduce to a bare generic token ("china") must not anchor a link
        # between two genuinely different companies.
        securities = pd.DataFrame(
            [
                {"market": "HK", "symbol": "00001", "name": "China Holdings Limited"},
                {"market": "US", "symbol": "CHN", "name": "China Inc"},
            ]
        )
        self.assertTrue(derive_fuzzy_identity_mappings(securities).empty)

    def test_curated_symbols_are_skipped(self) -> None:
        curated = default_identity_mappings()
        # 比亚迪 is curated (A 002594 / HK 01211); fuzzy must not re-link those symbols.
        securities = pd.DataFrame(
            [
                {"market": "A", "symbol": "002594", "name": "比亚迪股份有限公司"},
                {"market": "HK", "symbol": "01211", "name": "比亚迪股份有限公司"},
            ]
        )
        result = derive_fuzzy_identity_mappings(securities, curated=curated)
        self.assertTrue(result.empty)

    def test_empty_input_is_safe(self) -> None:
        self.assertTrue(derive_fuzzy_identity_mappings(pd.DataFrame()).empty)
