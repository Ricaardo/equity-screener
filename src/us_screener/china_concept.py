"""China-concept (中概股) identification — a hard exclusion rule for the US screener.

Reliable signals, in confidence order:
  1. Curated seed list (``data/us_china_concept.seed.csv`` + builtin) — high.
  2. SEC ``submissions`` API domicile: state-of-incorporation OR business-address
     country described as China / Hong Kong — high.
  3. SEC state-of-incorporation described as a classic VIE shell (Cayman Islands /
     British Virgin Islands) for a US-listed operating company — medium.

The standard SEC ``company_tickers.json`` (``fetch_sec_company_tickers``) only
carries cik/ticker/title, so domicile must come from the per-CIK ``submissions``
endpoint. We resolve it lazily and bounded (top-N by liquidity) to respect SEC
rate limits; the seed list alone already catches the major ADRs offline.

Tags are written to ``company_tags`` as ``tag_type='risk', tag_name='china_concept'``
with ``evidence_level`` ∈ {high, medium}. ``exclude_china_concept`` drops them from
the universe *before* scoring (a hard cut, not a penalty).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from us_screener.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

SEED_CSV_PATH = PROJECT_ROOT / "data" / "us_china_concept.seed.csv"

# Minimal builtin fallback if the CSV is missing; the CSV is the source of truth.
_BUILTIN_SEED: frozenset[str] = frozenset(
    {"BABA", "JD", "PDD", "BIDU", "NIO", "LI", "XPEV", "BILI", "NTES", "TME"}
)

# SEC stateOrCountryDescription / stateOfIncorporationDescription values.
_HIGH_DOMICILE = {"china", "hong kong"}
_SHELL_INCORP = {"cayman islands", "british virgin islands"}

TAG_TYPE = "risk"
TAG_NAME = "china_concept"
TAG_SOURCE = "us_screener.china_concept"


def _norm(value: object) -> str:
    return str(value or "").strip().lower()


def load_seed(path: Path | None = None) -> set[str]:
    """Load the editable seed list (union with the builtin fallback)."""
    seed = set(_BUILTIN_SEED)
    csv_path = path or SEED_CSV_PATH
    try:
        frame = pd.read_csv(csv_path)
        if "symbol" in frame.columns:
            seed |= {
                str(s).strip().upper() for s in frame["symbol"].dropna() if str(s).strip()
            }
    except FileNotFoundError:
        logger.warning("china-concept seed CSV not found at %s; using builtin seed", csv_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to read china-concept seed CSV: %s", exc)
    return seed


def classify_symbol(
    symbol: str,
    *,
    incorp_desc: object = None,
    business_country_desc: object = None,
    seed: set[str] | None = None,
) -> tuple[bool, str, str]:
    """Classify one symbol. Returns ``(is_china_concept, evidence_level, reason)``."""
    seed = seed if seed is not None else load_seed()
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False, "none", ""
    if sym in seed:
        return True, "high", "策展种子表（已知中概 ADR）"
    inc = _norm(incorp_desc)
    biz = _norm(business_country_desc)
    if inc in _HIGH_DOMICILE or biz in _HIGH_DOMICILE:
        label = incorp_desc if inc in _HIGH_DOMICILE else business_country_desc
        return True, "high", f"SEC 注册地/经营地：{label}"
    if inc in _SHELL_INCORP:
        return True, "medium", f"VIE 空壳注册地：{incorp_desc}（美股上市经营公司）"
    return False, "none", ""


def fetch_sec_domicile(symbol: str, ticker_map: dict[str, dict[str, Any]] | None = None) -> dict[str, str | None]:
    """Resolve a symbol's SEC domicile via the per-CIK submissions endpoint.

    Returns ``{incorp_desc, business_country_desc, name}`` (values may be None).
    Network call; callers should bound how many symbols they resolve.
    """
    import requests

    from ah_screener.sources.us_client import (
        SEC_TICKERS_URL,  # noqa: F401 (kept for discoverability)
        _sec_headers,
        fetch_sec_company_tickers,
    )

    ticker_map = ticker_map if ticker_map is not None else fetch_sec_company_tickers()
    entry = ticker_map.get(str(symbol).strip().upper())
    if not entry:
        return {"incorp_desc": None, "business_country_desc": None, "name": None}
    cik = int(entry["cik_str"])
    resp = requests.get(
        f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
        headers=_sec_headers(),
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    business = (data.get("addresses") or {}).get("business") or {}
    return {
        "incorp_desc": data.get("stateOfIncorporationDescription"),
        "business_country_desc": business.get("stateOrCountryDescription"),
        "name": data.get("name"),
    }


def tag_china_concept(
    store,
    *,
    use_sec: bool = True,
    limit: int | None = 1500,
    seed: set[str] | None = None,
    domicile_fetcher: Callable[[str], dict[str, str | None]] | None = None,
) -> dict[str, int]:
    """Tag US China-concept stocks in ``company_tags``.

    Seed-list hits are tagged offline for the whole US universe. When ``use_sec``,
    the top-``limit`` US stocks by latest turnover are additionally resolved via SEC
    submissions (bounded for rate limits) to catch ADRs not on the seed list.
    """
    seed = seed if seed is not None else load_seed()
    securities = store.query_df(
        "SELECT market, symbol, name, asset_type FROM securities WHERE market = 'US'"
    )
    if securities.empty:
        return {"tagged": 0, "high": 0, "medium": 0, "sec_checked": 0}

    rows: list[dict[str, Any]] = []
    high = medium = 0

    def _add(symbol: str, name: object, level: str, reason: str) -> None:
        nonlocal high, medium
        rows.append(
            {
                "market": "US",
                "symbol": symbol,
                "tag_type": TAG_TYPE,
                "tag_name": TAG_NAME,
                "evidence_level": level,
                "source": TAG_SOURCE,
                "updated_at": pd.Timestamp.now(),
            }
        )
        if level == "high":
            high += 1
        else:
            medium += 1

    tagged_syms: set[str] = set()
    # 1) offline seed pass over the whole universe
    for sym in securities["symbol"].astype(str).str.upper():
        is_cn, level, reason = classify_symbol(sym, seed=seed)
        if is_cn:
            _add(sym, None, level, reason)
            tagged_syms.add(sym)

    # 2) bounded SEC submissions pass for the most liquid stocks not already tagged
    sec_checked = 0
    if use_sec:
        fetcher = domicile_fetcher or fetch_sec_domicile
        ranked = _liquid_us_stock_symbols(store, limit=limit)
        for sym in ranked:
            if sym in tagged_syms:
                continue
            try:
                dom = fetcher(sym)
            except Exception as exc:  # noqa: BLE001
                logger.debug("SEC domicile fetch failed for %s: %s", sym, exc)
                continue
            sec_checked += 1
            is_cn, level, reason = classify_symbol(
                sym,
                incorp_desc=dom.get("incorp_desc"),
                business_country_desc=dom.get("business_country_desc"),
                seed=seed,
            )
            if is_cn:
                _add(sym, dom.get("name"), level, reason)
                tagged_syms.add(sym)

    if rows:
        store.upsert_dataframe("company_tags", pd.DataFrame(rows))
    return {"tagged": len(rows), "high": high, "medium": medium, "sec_checked": sec_checked}


def _liquid_us_stock_symbols(store, limit: int | None) -> list[str]:
    snap = store.query_df(
        """
        SELECT symbol, amount, trade_date FROM market_snapshots
        WHERE market = 'US' AND COALESCE(asset_type,'stock') <> 'etf'
        """
    )
    if snap.empty:
        return []
    snap["trade_date"] = pd.to_datetime(snap["trade_date"], errors="coerce")
    latest = snap.sort_values("trade_date").drop_duplicates("symbol", keep="last")
    latest["amount_num"] = pd.to_numeric(latest["amount"], errors="coerce").fillna(0)
    ranked = latest.sort_values("amount_num", ascending=False)["symbol"].astype(str).str.upper()
    out = ranked.tolist()
    return out[:limit] if limit else out


def china_concept_symbols(store) -> set[str]:
    """Symbols currently tagged as China-concept."""
    tags = store.query_df(
        "SELECT symbol FROM company_tags WHERE market='US' AND tag_type=? AND tag_name=?",
        [TAG_TYPE, TAG_NAME],
    )
    if tags.empty:
        return set()
    return {str(s).strip().upper() for s in tags["symbol"]}


def exclude_china_concept(df: pd.DataFrame, store) -> pd.DataFrame:
    """Hard-drop China-concept US rows from a universe/candidate frame (not a penalty)."""
    if df.empty or "symbol" not in df.columns:
        return df
    banned = china_concept_symbols(store)
    if not banned:
        return df
    is_us = df["market"].astype(str).str.upper().eq("US") if "market" in df.columns else True
    sym_up = df["symbol"].astype(str).str.upper()
    drop_mask = is_us & sym_up.isin(banned)
    return df.loc[~drop_mask].copy()
