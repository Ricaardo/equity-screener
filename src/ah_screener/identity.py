from __future__ import annotations

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
