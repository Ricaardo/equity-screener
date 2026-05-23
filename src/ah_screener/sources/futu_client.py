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
