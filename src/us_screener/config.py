"""US screener configuration + independent-database routing.

The reused ``ah_screener`` core opens its store via ``ah_screener.db.get_store()``,
which reads ``AH_SCREENER_DB`` at call time (see ``ah_screener/config.py``).
``use_us_database()`` sets that env var to the independent US DuckDB *before* any
store is opened, so every reused pipeline/storage write lands in the US database
without modifying shared code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# src/us_screener/config.py -> repo root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_US_DB_PATH = PROJECT_ROOT / "data" / "us_screener.duckdb"
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "reports" / "us-premarket"

US_DB_ENV = "US_SCREENER_DB"
SHARED_DB_ENV = "AH_SCREENER_DB"  # the env var ah_screener.config.get_settings() reads

_TRUE = {"1", "true", "yes", "on"}


def us_db_path() -> Path:
    return Path(os.getenv(US_DB_ENV, DEFAULT_US_DB_PATH))


def use_us_database() -> Path:
    """Route the shared ah_screener store to the independent US DuckDB.

    Idempotent. Call this at the start of every us_screener entrypoint (CLI
    command, pipeline run, MCP server startup) before importing/using any
    ah_screener pipeline or storage function.
    """
    path = us_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ[SHARED_DB_ENV] = str(path)
    return path


@dataclass(frozen=True)
class USConfig:
    db_path: Path
    reports_dir: Path
    # universe / hard filters
    min_us_amount: float
    min_market_cap: float
    exclude_china_concept: bool
    # LLM opinion (optional — graceful skip when api key is absent)
    llm_provider: str  # "anthropic" | "openai" | "none"
    llm_model: str
    llm_api_key: str | None
    # pre-market schedule (Beijing-time evening ≈ US pre-market next morning ET)
    schedule_hour: int
    schedule_minute: int


def _resolve_llm() -> tuple[str, str, str | None]:
    provider = os.getenv("US_SCREENER_LLM_PROVIDER", "").strip().lower()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY") or None
    openai_key = os.getenv("OPENAI_API_KEY") or None
    if not provider:
        if anthropic_key:
            provider = "anthropic"
        elif openai_key:
            provider = "openai"
        else:
            provider = "none"
    if provider == "anthropic":
        return provider, os.getenv("US_SCREENER_LLM_MODEL", "claude-opus-4-8"), anthropic_key
    if provider == "openai":
        return provider, os.getenv("US_SCREENER_LLM_MODEL", "gpt-4o"), openai_key
    return "none", "", None


def get_us_config() -> USConfig:
    provider, model, key = _resolve_llm()
    return USConfig(
        db_path=us_db_path(),
        reports_dir=Path(os.getenv("US_SCREENER_REPORTS", DEFAULT_REPORTS_DIR)),
        min_us_amount=float(os.getenv("US_SCREENER_MIN_AMOUNT", "3000000")),
        min_market_cap=float(os.getenv("US_SCREENER_MIN_MKTCAP", "300000000")),
        exclude_china_concept=os.getenv("US_SCREENER_EXCLUDE_CHINA", "1").strip().lower() in _TRUE,
        llm_provider=provider,
        llm_model=model,
        llm_api_key=key,
        schedule_hour=int(os.getenv("US_SCREENER_SCHED_HOUR", "20")),
        schedule_minute=int(os.getenv("US_SCREENER_SCHED_MIN", "30")),
    )
