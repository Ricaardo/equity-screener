"""Futu OpenD as a preferred history source across markets (US / HK / A).

Optional dependency by design: if ``futu-api`` is not installed or OpenD is not
reachable, ``fetch_futu_history`` returns an empty frame so callers fall back to
AKShare. OpenD runs locally (default 127.0.0.1:11111); set AH_SCREENER_USE_FUTU=0
to disable.
"""

from __future__ import annotations

import importlib.util
import os

import pandas as pd

FUTU_HOST = os.getenv("AH_SCREENER_FUTU_HOST", "127.0.0.1")
FUTU_PORT = int(os.getenv("AH_SCREENER_FUTU_PORT", "11111"))
USE_FUTU = os.getenv("AH_SCREENER_USE_FUTU", "1").lower() not in {"0", "false", "no"}


def futu_available() -> bool:
    return USE_FUTU and importlib.util.find_spec("futu") is not None


def futu_code(market: str, symbol: str) -> str:
    """Map a (market, symbol) to a Futu code, e.g. US.AAPL / HK.00700 / SH.600000."""
    market = str(market).upper()
    raw = str(symbol).strip().upper()
    if market == "US":
        return f"US.{raw.replace('.', '-')}"
    if market == "HK":
        digits = "".join(ch for ch in raw if ch.isdigit()).zfill(5)
        return f"HK.{digits}"
    if market == "A":
        digits = "".join(ch for ch in raw if ch.isdigit())
        prefix = "SH" if digits.startswith(("60", "68", "90", "51", "56", "58", "50")) else "SZ"
        return f"{prefix}.{digits}"
    raise ValueError(f"Unsupported market for Futu: {market}")


def _normalize(raw: pd.DataFrame, market: str, symbol: str, adj_type: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    frame = raw.rename(
        columns={
            "time_key": "date",
            "data_date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "open_price": "open",
            "high_price": "high",
            "low_price": "low",
            "close_price": "close",
            "volume": "volume",
            "turnover": "amount",
        }
    )
    num = lambda col: pd.to_numeric(frame[col], errors="coerce") if col in frame.columns else pd.NA  # noqa: E731
    out = pd.DataFrame(
        {
            "market": market,
            "symbol": str(symbol),
            "trade_date": pd.to_datetime(frame.get("date"), errors="coerce"),
            "open": num("open"),
            "high": num("high"),
            "low": num("low"),
            "close": num("close"),
            "volume": num("volume"),
            "amount": num("amount"),
            "adj_type": adj_type,
            "source": "futu.opend.history_kline",
            "updated_at": pd.Timestamp.now(),
        }
    )
    return out.dropna(subset=["trade_date", "close"])


def fetch_futu_history(
    market: str, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"
) -> pd.DataFrame:
    """Daily K-lines from local Futu OpenD; empty frame when unavailable (caller falls back)."""
    if not futu_available():
        return pd.DataFrame()
    import futu

    code = futu_code(market, symbol)
    quote_ctx = futu.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    try:
        autype = getattr(futu, "AuType", None)
        autype_value = getattr(autype, "QFQ", None) if adjust else getattr(autype, "NONE", None)
        if autype_value is None:
            autype_value = "qfq" if adjust else None
        ret, raw = quote_ctx.request_history_kline(
            code,
            start=pd.to_datetime(start_date).strftime("%Y-%m-%d"),
            end=pd.to_datetime(end_date).strftime("%Y-%m-%d"),
            ktype=futu.KLType.K_DAY,
            autype=autype_value,
        )
        if ret != getattr(futu, "RET_OK", 0):
            raise RuntimeError(str(raw))
        return _normalize(raw, market=market, symbol=symbol, adj_type=adjust or "raw")
    finally:
        quote_ctx.close()


def _normalize_hk_etf(basics: pd.DataFrame, snapshot: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build (securities, market_snapshots) frames from Futu HK ETF basics + snapshot.

    Pure function (unit-testable) so the Futu field mapping is verifiable without OpenD.
    """
    now = pd.Timestamp.now()
    if basics is None or basics.empty:
        return pd.DataFrame(), pd.DataFrame()
    b = basics.copy()
    b["symbol"] = b["code"].astype(str).str.split(".").str[-1].str.zfill(5)
    snap = snapshot.copy() if snapshot is not None and not snapshot.empty else pd.DataFrame()
    if not snap.empty:
        snap["symbol"] = snap["code"].astype(str).str.split(".").str[-1].str.zfill(5)
        snap = snap.set_index("symbol")

    def _col(sym: str, name: str):
        return snap.loc[sym, name] if (not snap.empty and sym in snap.index and name in snap.columns) else pd.NA

    securities = pd.DataFrame(
        {
            "market": "HK",
            "symbol": b["symbol"],
            "name": b.get("name", b["symbol"]),
            "asset_type": "etf",
            "board": "HK ETF",
            "exchange": "HKEX",
            "currency": "HKD",
            "status": "listed",
            "is_st": False,
            "is_hk_connect": False,
            "metadata_source": "futu.opend.get_stock_basicinfo",
            "metadata_confidence": "high",
            "updated_at": now,
        }
    )
    rows = []
    for _, row in b.iterrows():
        sym = row["symbol"]
        last = pd.to_numeric(pd.Series([_col(sym, "last_price")]), errors="coerce").iloc[0]
        prev = pd.to_numeric(pd.Series([_col(sym, "prev_close_price")]), errors="coerce").iloc[0]
        pct = ((last / prev - 1) * 100) if pd.notna(last) and pd.notna(prev) and prev else pd.NA
        rows.append(
            {
                "market": "HK",
                "symbol": sym,
                "asset_type": "etf",
                "board": "HK ETF",
                "trade_date": pd.to_datetime(_col(sym, "update_time"), errors="coerce"),
                "name": row.get("name", sym),
                "last_price": last,
                "pct_change": pct,
                "volume": pd.to_numeric(pd.Series([_col(sym, "volume")]), errors="coerce").iloc[0],
                "amount": pd.to_numeric(pd.Series([_col(sym, "turnover")]), errors="coerce").iloc[0],
                "turnover_rate": pd.to_numeric(pd.Series([_col(sym, "turnover_rate")]), errors="coerce").iloc[0],
                "pe_ttm": pd.NA,
                "pb": pd.NA,
                "market_cap": pd.NA,
                "source": "futu.opend.get_market_snapshot",
                "updated_at": now,
            }
        )
    snapshots = pd.DataFrame(rows)
    if not snapshots.empty:
        snapshots["trade_date"] = snapshots["trade_date"].fillna(now.normalize())
    return securities, snapshots


def fetch_futu_hk_etf_spot() -> tuple[pd.DataFrame, pd.DataFrame]:
    """HK ETF universe + spot via Futu OpenD; empty when unavailable (caller falls back)."""
    if not futu_available():
        return pd.DataFrame(), pd.DataFrame()
    import futu

    quote_ctx = futu.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    try:
        ret, basics = quote_ctx.get_stock_basicinfo(futu.Market.HK, futu.SecurityType.ETF)
        if ret != getattr(futu, "RET_OK", 0) or basics is None or basics.empty:
            return pd.DataFrame(), pd.DataFrame()
        codes = basics["code"].astype(str).tolist()
        snaps = []
        for i in range(0, len(codes), 200):  # Futu snapshot caps batch size
            sret, sdf = quote_ctx.get_market_snapshot(codes[i : i + 200])
            if sret == getattr(futu, "RET_OK", 0) and sdf is not None and not sdf.empty:
                snaps.append(sdf)
        snapshot = pd.concat(snaps, ignore_index=True) if snaps else pd.DataFrame()
        return _normalize_hk_etf(basics, snapshot)
    finally:
        quote_ctx.close()
