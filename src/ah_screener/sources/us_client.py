from __future__ import annotations

import os
from datetime import datetime, timedelta
from io import StringIO
from time import sleep
from typing import Any

import pandas as pd
import requests

from ah_screener.classification import infer_us_board, infer_us_exchange


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
STOOQ_DAILY_URL = "https://stooq.com/q/d/l/"

US_DEFAULT_SYMBOLS: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "AVGO",
    "TSLA",
    "BRK.B",
    "JPM",
    "LLY",
    "V",
    "MA",
    "UNH",
    "XOM",
    "COST",
    "WMT",
    "HD",
    "PG",
    "JNJ",
    "ABBV",
    "MRK",
    "ORCL",
    "AMD",
    "NFLX",
    "CRM",
    "ADBE",
    "QCOM",
    "TXN",
    "INTC",
    "BAC",
    "KO",
    "PEP",
    "MCD",
    "TMO",
    "LIN",
    "CAT",
    "GE",
    "NKE",
    "DIS",
    "BABA",
    "JD",
    "BIDU",
    "NTES",
    "BILI",
    "PDD",
    "LI",
    "NIO",
    "XPEV",
    "SPY",
    "QQQ",
    "DIA",
    "IWM",
    "XLK",
    "SMH",
    "SOXX",
    "XLE",
    "GLD",
)


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now())


def _sec_headers() -> dict[str, str]:
    return {
        "User-Agent": os.getenv(
            "AH_SCREENER_SEC_USER_AGENT",
            "ah-stock-screener/0.1 research-tool contact@example.com",
        ),
        "Accept-Encoding": "gzip, deflate",
    }


def _clean_us_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper().replace("/", ".")


def _number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _read_symbol_directory(url: str) -> pd.DataFrame:
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    text = response.text.strip()
    frame = pd.read_csv(StringIO(text), sep="|")
    first_column = frame.columns[0]
    frame = frame[~frame[first_column].astype(str).str.contains("File Creation Time", na=False)]
    return frame


def fetch_us_security_master() -> pd.DataFrame:
    updated_at = _now()
    rows: list[pd.DataFrame] = []

    try:
        nasdaq = _read_symbol_directory(NASDAQ_LISTED_URL)
        if not nasdaq.empty:
            symbol = nasdaq["Symbol"].map(_clean_us_symbol)
            etf = nasdaq.get("ETF", pd.Series("N", index=nasdaq.index)).astype(str).str.upper().eq("Y")
            exchange = pd.Series("NASDAQ", index=nasdaq.index)
            rows.append(
                pd.DataFrame(
                    {
                        "market": "US",
                        "symbol": symbol,
                        "asset_type": etf.map(lambda value: "etf" if value else "stock"),
                        "board": [
                            infer_us_board(exchange.iloc[i], "etf" if etf.iloc[i] else "stock")
                            for i in range(len(nasdaq))
                        ],
                        "name": nasdaq["Security Name"].astype(str),
                        "exchange": exchange,
                        "currency": "USD",
                        "status": "listed",
                        "is_st": False,
                        "is_hk_connect": False,
                        "metadata_source": "nasdaqtrader.nasdaqlisted",
                        "metadata_confidence": "high",
                        "updated_at": updated_at,
                    }
                )
            )
    except Exception:
        pass

    try:
        other = _read_symbol_directory(OTHER_LISTED_URL)
        if not other.empty:
            symbol = other["ACT Symbol"].map(_clean_us_symbol)
            etf = other.get("ETF", pd.Series("N", index=other.index)).astype(str).str.upper().eq("Y")
            exchange = other["Exchange"].map(infer_us_exchange)
            rows.append(
                pd.DataFrame(
                    {
                        "market": "US",
                        "symbol": symbol,
                        "asset_type": etf.map(lambda value: "etf" if value else "stock"),
                        "board": [
                            infer_us_board(exchange.iloc[i], "etf" if etf.iloc[i] else "stock")
                            for i in range(len(other))
                        ],
                        "name": other["Security Name"].astype(str),
                        "exchange": exchange,
                        "currency": "USD",
                        "status": "listed",
                        "is_st": False,
                        "is_hk_connect": False,
                        "metadata_source": "nasdaqtrader.otherlisted",
                        "metadata_confidence": "high",
                        "updated_at": updated_at,
                    }
                )
            )
    except Exception:
        pass

    if rows:
        return pd.concat(rows, ignore_index=True).drop_duplicates(["market", "symbol"], keep="first")

    return pd.DataFrame(
        {
            "market": "US",
            "symbol": list(US_DEFAULT_SYMBOLS),
            "asset_type": ["etf" if symbol in {"SPY", "QQQ", "DIA", "IWM", "XLK", "SMH", "SOXX", "XLE", "GLD"} else "stock" for symbol in US_DEFAULT_SYMBOLS],
            "board": ["US ETF" if symbol in {"SPY", "QQQ", "DIA", "IWM", "XLK", "SMH", "SOXX", "XLE", "GLD"} else "US Default" for symbol in US_DEFAULT_SYMBOLS],
            "name": list(US_DEFAULT_SYMBOLS),
            "exchange": "UNKNOWN",
            "currency": "USD",
            "status": "listed",
            "is_st": False,
            "is_hk_connect": False,
            "metadata_source": "curated_us_default_universe",
            "metadata_confidence": "medium",
            "updated_at": updated_at,
        }
    )


def _normalize_us_history(raw: pd.DataFrame, symbol: str, source: str, adj_type: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    updated_at = _now()
    date_column = "date" if "date" in raw.columns else "日期"
    close = _number(raw["close"] if "close" in raw.columns else raw["收盘"])
    volume = _number(raw["volume"] if "volume" in raw.columns else raw.get("成交量", pd.Series(pd.NA, index=raw.index)))
    return pd.DataFrame(
        {
            "market": "US",
            "symbol": _clean_us_symbol(symbol),
            "trade_date": pd.to_datetime(raw[date_column], errors="coerce"),
            "open": _number(raw["open"] if "open" in raw.columns else raw.get("开盘", pd.Series(pd.NA, index=raw.index))),
            "high": _number(raw["high"] if "high" in raw.columns else raw.get("最高", pd.Series(pd.NA, index=raw.index))),
            "low": _number(raw["low"] if "low" in raw.columns else raw.get("最低", pd.Series(pd.NA, index=raw.index))),
            "close": close,
            "volume": volume,
            "amount": close * volume,
            "adj_type": adj_type,
            "source": source,
            "updated_at": updated_at,
        }
    ).dropna(subset=["trade_date", "close"])


def _fetch_us_history_akshare(symbol: str, adjust: str) -> pd.DataFrame:
    import akshare as ak

    raw = ak.stock_us_daily(symbol=_clean_us_symbol(symbol), adjust=adjust)
    return _normalize_us_history(raw, symbol=symbol, source="akshare.stock_us_daily", adj_type=adjust or "raw")


def _fetch_us_history_stooq(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    api_key = os.getenv("STOOQ_API_KEY") or os.getenv("AH_SCREENER_STOOQ_API_KEY")
    if not api_key:
        return pd.DataFrame()
    params = {
        "s": f"{_clean_us_symbol(symbol).lower()}.us",
        "i": "d",
        "d1": start_date,
        "d2": end_date,
        "apikey": api_key,
    }
    response = requests.get(STOOQ_DAILY_URL, params=params, timeout=20)
    response.raise_for_status()
    raw = pd.read_csv(StringIO(response.text))
    if raw.empty or "No data" in response.text:
        return pd.DataFrame()
    raw = raw.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    return _normalize_us_history(raw, symbol=symbol, source="stooq.daily_csv", adj_type="raw")


def fetch_us_history(symbol: str, start_date: str, end_date: str, adjust: str = "") -> pd.DataFrame:
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    last_error: Exception | None = None
    calls = [
        lambda: _fetch_us_history_akshare(symbol, adjust=adjust),
        lambda: _fetch_us_history_stooq(symbol, start_date=start_date, end_date=end_date),
    ]
    for func in calls:
        try:
            history = func()
            if history.empty:
                raise RuntimeError("empty US history")
            return history[(history["trade_date"] >= start) & (history["trade_date"] <= end)]
        except Exception as exc:
            last_error = exc
            sleep(0.3)
    raise RuntimeError(f"All US history sources failed for {symbol}. Last error: {last_error}") from last_error


def fetch_us_spot(symbols: list[str] | None = None, lookback_days: int = 14) -> tuple[pd.DataFrame, pd.DataFrame]:
    master = fetch_us_security_master()
    selected = [_clean_us_symbol(symbol) for symbol in (symbols or list(US_DEFAULT_SYMBOLS))]
    master_index = master.set_index("symbol", drop=False)
    end = datetime.now()
    start_date = (end - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end_date = end.strftime("%Y%m%d")
    snapshot_rows: list[dict[str, object]] = []
    for symbol in selected:
        try:
            history = fetch_us_history(symbol, start_date=start_date, end_date=end_date)
        except Exception:
            continue
        if history.empty:
            continue
        latest = history.sort_values("trade_date").iloc[-1]
        previous_close = pd.to_numeric(history["close"], errors="coerce").dropna()
        pct_change = pd.NA
        if len(previous_close) >= 2 and float(previous_close.iloc[-2]) > 0:
            pct_change = (float(previous_close.iloc[-1]) / float(previous_close.iloc[-2]) - 1) * 100
        meta = master_index.loc[symbol] if symbol in master_index.index else None
        asset_type = str(meta["asset_type"]) if meta is not None else "stock"
        board = str(meta["board"]) if meta is not None else infer_us_board("", asset_type)
        snapshot_rows.append(
            {
                "market": "US",
                "symbol": symbol,
                "asset_type": asset_type,
                "board": board,
                "trade_date": latest["trade_date"],
                "name": str(meta["name"]) if meta is not None else symbol,
                "last_price": latest["close"],
                "pct_change": pct_change,
                "volume": latest["volume"],
                "amount": latest["amount"],
                "turnover_rate": pd.NA,
                "pe_ttm": pd.NA,
                "pb": pd.NA,
                "market_cap": pd.NA,
                "source": str(latest["source"]),
                "updated_at": _now(),
            }
        )

    securities = master[master["symbol"].isin(selected)].copy()
    missing = [symbol for symbol in selected if symbol not in set(securities["symbol"])]
    if missing:
        securities = pd.concat(
            [
                securities,
                pd.DataFrame(
                    {
                        "market": "US",
                        "symbol": missing,
                        "asset_type": "stock",
                        "board": "US Default",
                        "name": missing,
                        "exchange": "UNKNOWN",
                        "currency": "USD",
                        "status": "listed",
                        "is_st": False,
                        "is_hk_connect": False,
                        "metadata_source": "curated_us_default_universe",
                        "metadata_confidence": "medium",
                        "updated_at": _now(),
                    }
                ),
            ],
            ignore_index=True,
        )
    return securities.drop_duplicates(["market", "symbol"]), pd.DataFrame(snapshot_rows)


def fetch_sec_company_tickers() -> dict[str, dict[str, Any]]:
    response = requests.get(SEC_TICKERS_URL, headers=_sec_headers(), timeout=20)
    response.raise_for_status()
    raw = response.json()
    mapping: dict[str, dict[str, Any]] = {}
    for value in raw.values():
        ticker = _clean_us_symbol(value.get("ticker"))
        if ticker:
            mapping[ticker] = value
    return mapping


def fetch_sec_companyfacts(symbol: str) -> tuple[dict[str, Any], dict[str, Any]]:
    ticker_map = fetch_sec_company_tickers()
    meta = ticker_map.get(_clean_us_symbol(symbol))
    if not meta:
        raise RuntimeError(f"SEC company ticker map has no CIK for {symbol}")
    cik = int(meta["cik_str"])
    response = requests.get(SEC_COMPANYFACTS_URL.format(cik=cik), headers=_sec_headers(), timeout=30)
    response.raise_for_status()
    return meta, response.json()
