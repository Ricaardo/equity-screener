from __future__ import annotations

import re
from datetime import datetime

import pandas as pd


DEFAULT_IDENTITY_LINKS: tuple[tuple[str, tuple[tuple[str, str, str, str], ...]], ...] = (
    (
        "比亚迪",
        (
            ("A", "002594", "比亚迪", "ordinary"),
            ("HK", "01211", "比亚迪股份", "h_share"),
        ),
    ),
    (
        "中芯国际",
        (
            ("A", "688981", "中芯国际", "ordinary"),
            ("HK", "00981", "中芯国际", "h_share"),
        ),
    ),
    (
        "阿里巴巴",
        (
            ("HK", "09988", "阿里巴巴-W", "ordinary"),
            ("US", "BABA", "Alibaba Group Holding Limited", "adr"),
        ),
    ),
    (
        "京东集团",
        (
            ("HK", "09618", "京东集团-SW", "ordinary"),
            ("US", "JD", "JD.com, Inc.", "adr"),
        ),
    ),
    (
        "百度",
        (
            ("HK", "09888", "百度集团-SW", "ordinary"),
            ("US", "BIDU", "Baidu, Inc.", "adr"),
        ),
    ),
    (
        "网易",
        (
            ("HK", "09999", "网易-S", "ordinary"),
            ("US", "NTES", "NetEase, Inc.", "adr"),
        ),
    ),
    (
        "哔哩哔哩",
        (
            ("HK", "09626", "哔哩哔哩-W", "ordinary"),
            ("US", "BILI", "Bilibili Inc.", "adr"),
        ),
    ),
    (
        "小鹏汽车",
        (
            ("HK", "09868", "小鹏汽车-W", "ordinary"),
            ("US", "XPEV", "XPeng Inc.", "adr"),
        ),
    ),
    (
        "理想汽车",
        (
            ("HK", "02015", "理想汽车-W", "ordinary"),
            ("US", "LI", "Li Auto Inc.", "adr"),
        ),
    ),
    (
        "蔚来",
        (
            ("HK", "09866", "蔚来-SW", "ordinary"),
            ("US", "NIO", "NIO Inc.", "adr"),
        ),
    ),
    (
        "贝壳",
        (
            ("HK", "02423", "贝壳-W", "ordinary"),
            ("US", "BEKE", "KE Holdings Inc.", "adr"),
        ),
    ),
    (
        "中通快递",
        (
            ("HK", "02057", "中通快递-W", "ordinary"),
            ("US", "ZTO", "ZTO Express (Cayman) Inc.", "adr"),
        ),
    ),
    (
        "腾讯音乐",
        (
            ("HK", "01698", "腾讯音乐-SW", "ordinary"),
            ("US", "TME", "Tencent Music Entertainment Group", "adr"),
        ),
    ),
)


_NAME_NOISE = re.compile(
    r"(股份有限公司|有限公司|控股集团|控股|集团|股份|有限|公司|"
    r"\b(inc|ltd|limited|corp|corporation|company|co|group|holdings?|plc)\b|"
    r"[,.\-－()（）]|[-－]?[WHSAN]+$|\s+)",
    flags=re.IGNORECASE,
)
# Generic normalized names that must NOT anchor a fuzzy cross-market link.
_FUZZY_NAME_STOPWORDS = frozenset({"china", "bank", "international", "中国", "国际", "银行"})


def _fuzzy_name_key(name: object) -> str | None:
    text = str(name or "").strip()
    if not text:
        return None
    text = _NAME_NOISE.sub("", text).lower()
    if len(text) < 2 or text in _FUZZY_NAME_STOPWORDS:
        return None
    return text


def derive_fuzzy_identity_mappings(
    securities: pd.DataFrame, curated: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Infer cross-market same-entity links by normalized-name equality.

    Curated mappings (``default_identity_mappings``) always win: a security already
    covered by a curated link is skipped here. Only normalized names that match across
    two or more *different* markets produce a fuzzy link (``confidence="fuzzy"``), so a
    single-market name collision never creates one. This augments — never overrides —
    the hand-curated table, and stays traceable via ``source``/``confidence``.
    """
    if securities is None or securities.empty:
        return pd.DataFrame()
    if not {"market", "symbol", "name"}.issubset(securities.columns):
        return pd.DataFrame()

    covered: set[tuple[str, str]] = set()
    if curated is not None and not curated.empty:
        covered = {
            (str(m), _normalize_symbol(str(m), str(s)))
            for m, s in zip(curated["market"], curated["symbol"])
        }

    frame = securities[["market", "symbol", "name"]].copy()
    frame["market"] = frame["market"].astype(str)
    frame["symbol"] = [
        _normalize_symbol(str(m), str(s)) for m, s in zip(frame["market"], frame["symbol"])
    ]
    frame["key"] = frame["name"].map(_fuzzy_name_key)
    frame = frame.dropna(subset=["key"])
    keep_mask = [(m, s) not in covered for m, s in zip(frame["market"], frame["symbol"])]
    frame = frame[pd.Series(keep_mask, index=frame.index)]
    frame = frame.drop_duplicates(["market", "symbol"])
    if frame.empty:
        return pd.DataFrame()

    updated_at = pd.Timestamp(datetime.now())
    rows: list[dict[str, object]] = []
    for key, group in frame.groupby("key"):
        if group["market"].nunique() < 2:
            continue
        canonical_id = f"fuzzy:{key}"
        for _, row in group.iterrows():
            rows.append(
                {
                    "canonical_id": canonical_id,
                    "market": row["market"],
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "listing_type": "fuzzy_cross_market",
                    "source": "fuzzy_name_match",
                    "confidence": "fuzzy",
                    "updated_at": updated_at,
                }
            )
    return pd.DataFrame(rows)


def _normalize_symbol(market: str, symbol: str) -> str:
    if market == "A":
        return symbol.zfill(6)
    if market == "HK":
        return symbol.zfill(5)
    return symbol.upper()


def default_identity_mappings() -> pd.DataFrame:
    updated_at = pd.Timestamp(datetime.now())
    rows: list[dict[str, object]] = []
    for canonical_id, listings in DEFAULT_IDENTITY_LINKS:
        for market, symbol, name, listing_type in listings:
            rows.append(
                {
                    "canonical_id": canonical_id,
                    "market": market,
                    "symbol": _normalize_symbol(market, symbol),
                    "name": name,
                    "listing_type": listing_type,
                    "source": "curated_cross_market_identity",
                    "confidence": "high",
                    "updated_at": updated_at,
                }
            )
    return pd.DataFrame(rows)
