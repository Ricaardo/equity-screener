"""Offline unit tests for China-concept identification + hard exclusion (P1)."""

from __future__ import annotations

import pandas as pd
import pytest

from us_screener import china_concept as cc


@pytest.fixture
def seed():
    return cc.load_seed()


def test_seed_loads_known_adrs(seed):
    assert {"BABA", "NIO", "PDD", "BIDU"} <= seed


@pytest.mark.parametrize(
    "symbol,kwargs,expected",
    [
        ("AAPL", {}, (False, "none")),
        ("BABA", {}, (True, "high")),  # seed
        ("ZZZZ", {"incorp_desc": "Cayman Islands"}, (True, "medium")),  # VIE shell, not seed
        ("YYYY", {"business_country_desc": "China"}, (True, "high")),
        ("KKKK", {"incorp_desc": "Hong Kong"}, (True, "high")),
        ("WWWW", {"incorp_desc": "Delaware"}, (False, "none")),
        ("", {}, (False, "none")),
    ],
)
def test_classify_symbol(symbol, kwargs, expected, seed):
    is_cn, level, _ = cc.classify_symbol(symbol, seed=seed, **kwargs)
    assert (is_cn, level) == expected


class _FakeStore:
    """Minimal store double backed by in-memory frames."""

    def __init__(self, securities: pd.DataFrame, snapshots: pd.DataFrame):
        self._securities = securities
        self._snapshots = snapshots
        self.tags = pd.DataFrame()

    def query_df(self, sql: str, params=None):
        if "FROM securities" in sql:
            return self._securities.copy()
        if "FROM market_snapshots" in sql:
            return self._snapshots.copy()
        if "FROM company_tags" in sql:
            return self.tags.copy()
        return pd.DataFrame()

    def upsert_dataframe(self, table: str, df: pd.DataFrame) -> int:
        if table == "company_tags":
            self.tags = pd.concat([self.tags, df], ignore_index=True)
        return len(df)


def _store():
    # XYZQ is deliberately NOT on the seed list, so it can only be caught via SEC.
    securities = pd.DataFrame(
        {
            "market": ["US"] * 4,
            "symbol": ["AAPL", "BABA", "XYZQ", "MSFT"],
            "name": ["Apple", "Alibaba", "Xyzq", "Microsoft"],
            "asset_type": ["stock"] * 4,
        }
    )
    snapshots = pd.DataFrame(
        {
            "market": ["US"] * 4,
            "symbol": ["AAPL", "BABA", "XYZQ", "MSFT"],
            "asset_type": ["stock"] * 4,
            "amount": [9e9, 1e9, 5e7, 8e9],
            "trade_date": pd.Timestamp("2026-05-29"),
        }
    )
    return _FakeStore(securities, snapshots)


def test_tag_via_seed_only_offline():
    store = _store()
    res = cc.tag_china_concept(store, use_sec=False)
    assert res["tagged"] == 1 and res["high"] == 1  # BABA via seed
    assert cc.china_concept_symbols(store) == {"BABA"}


def test_tag_via_sec_catches_non_seed_shell():
    store = _store()

    def fake_domicile(sym):
        return {
            "XYZQ": {"incorp_desc": "Cayman Islands", "business_country_desc": None, "name": "Xyzq"},
        }.get(sym, {"incorp_desc": "Delaware", "business_country_desc": None, "name": sym})

    res = cc.tag_china_concept(store, use_sec=True, limit=10, domicile_fetcher=fake_domicile)
    tagged = cc.china_concept_symbols(store)
    assert "BABA" in tagged  # seed
    assert "XYZQ" in tagged  # SEC shell (medium), not on seed list
    assert "AAPL" not in tagged and "MSFT" not in tagged
    assert res["sec_checked"] >= 1


def test_exclude_is_hard_cut():
    store = _store()
    cc.tag_china_concept(store, use_sec=False)  # tags BABA
    uni = pd.DataFrame({"market": ["US"] * 4, "symbol": ["AAPL", "BABA", "XYZQ", "MSFT"]})
    kept = cc.exclude_china_concept(uni, store)
    assert set(kept["symbol"]) == {"AAPL", "XYZQ", "MSFT"}  # BABA dropped


def test_exclude_noop_when_no_tags():
    store = _store()
    uni = pd.DataFrame({"market": ["US", "US"], "symbol": ["AAPL", "MSFT"]})
    kept = cc.exclude_china_concept(uni, store)
    assert set(kept["symbol"]) == {"AAPL", "MSFT"}
