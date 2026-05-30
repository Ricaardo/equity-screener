"""Offline unit tests for the us_screener data layer (P0)."""

from __future__ import annotations

import importlib

import pytest

from us_screener import config as uscfg
from us_screener import pipeline_us


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "US_SCREENER_DB",
        "AH_SCREENER_DB",
        "US_SCREENER_LLM_PROVIDER",
        "US_SCREENER_LLM_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "US_SCREENER_EXCLUDE_CHINA",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_us_db_path_default_and_override(monkeypatch, tmp_path):
    assert uscfg.us_db_path() == uscfg.DEFAULT_US_DB_PATH
    custom = tmp_path / "x.duckdb"
    monkeypatch.setenv("US_SCREENER_DB", str(custom))
    assert uscfg.us_db_path() == custom


def test_use_us_database_routes_shared_store(monkeypatch, tmp_path):
    custom = tmp_path / "nested" / "us.duckdb"
    monkeypatch.setenv("US_SCREENER_DB", str(custom))
    returned = uscfg.use_us_database()
    assert returned == custom
    # the env var ah_screener.config reads must now point at the US database
    import os

    assert os.environ["AH_SCREENER_DB"] == str(custom)
    assert custom.parent.exists()  # parent dir created


def test_llm_provider_autodetect(monkeypatch):
    assert uscfg.get_us_config().llm_provider == "none"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cfg = uscfg.get_us_config()
    assert cfg.llm_provider == "anthropic"
    assert cfg.llm_api_key == "sk-test"
    assert cfg.llm_model  # has a default model
    monkeypatch.setenv("US_SCREENER_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    cfg2 = uscfg.get_us_config()
    assert cfg2.llm_provider == "openai"
    assert cfg2.llm_api_key == "sk-oai"


def test_exclude_china_concept_flag(monkeypatch):
    assert uscfg.get_us_config().exclude_china_concept is True
    monkeypatch.setenv("US_SCREENER_EXCLUDE_CHINA", "0")
    assert uscfg.get_us_config().exclude_china_concept is False


def test_step_records_success_and_failure():
    result: dict = {}
    pipeline_us._step(result, "ok", lambda: {"rows": 3})
    pipeline_us._step(result, "boom", lambda: (_ for _ in ()).throw(ValueError("nope")))
    assert result["ok"] == {"rows": 3}
    assert "error" in result["boom"] and "nope" in result["boom"]["error"]


def test_backfill_universe_paginates_until_empty():
    """_backfill_universe must page until a batch yields zero securities."""

    class FakeAh:
        def __init__(self):
            self.calls = []

        def sync_us_spot_batch(self, *, offset, limit, include_etf):
            self.calls.append(offset)
            # two full batches, then empty
            n = limit if offset < 2 * limit else 0
            return {"US_securities": n, "US_snapshots": n}

    fake = FakeAh()
    out = pipeline_us._backfill_universe(fake, batch_limit=100, include_etf=True, max_symbols=10000)
    assert out == {"securities": 200, "snapshots": 200, "batches": 2}
    assert fake.calls == [0, 100, 200]  # stopped at first empty batch


def test_package_importable():
    mod = importlib.import_module("us_screener")
    assert mod.__version__
