from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "ah_screener.duckdb"


@dataclass(frozen=True)
class Settings:
    db_path: Path
    min_a_amount: float = 20_000_000
    min_hk_amount: float = 5_000_000
    min_us_amount: float = 3_000_000


def get_settings() -> Settings:
    db_path = Path(os.getenv("AH_SCREENER_DB", DEFAULT_DB_PATH))
    return Settings(db_path=db_path)
