from __future__ import annotations

from datetime import datetime, timedelta
from time import sleep
from typing import Literal

import pandas as pd

from ah_screener.classification import (
    infer_a_board,
    infer_a_exchange,
    infer_board,
    infer_status,
    is_st_name,
)


Market = Literal["A", "HK"]


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now())


def _first_existing(df: pd.DataFrame, names: list[str]) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series([None] * len(df), index=df.index)


def _number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _clean_a_symbol(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6)


def _clean_hk_symbol(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().str.replace(r"^hk", "", regex=True).str.zfill(5)


def normalize_a_spot(raw: pd.DataFrame, source: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    today = pd.Timestamp.today().normalize()
    updated_at = _now()

    symbol = _clean_a_symbol(_first_existing(raw, ["代码", "股票代码"]))
    name = _first_existing(raw, ["名称", "股票简称"]).astype(str)

    snapshots = pd.DataFrame(
        {
            "market": "A",
            "symbol": symbol,
            "asset_type": "stock",
            "board": symbol.map(lambda value: infer_a_board(value, "stock")),
            "trade_date": today,
            "name": name,
            "last_price": _number(_first_existing(raw, ["最新价", "收盘"])),
            "pct_change": _number(_first_existing(raw, ["涨跌幅"])),
            "volume": _number(_first_existing(raw, ["成交量"])),
            "amount": _number(_first_existing(raw, ["成交额"])),
            "turnover_rate": _number(_first_existing(raw, ["换手率"])),
            "pe_ttm": _number(_first_existing(raw, ["市盈率-动态", "市盈率"])),
            "pb": _number(_first_existing(raw, ["市净率"])),
            "market_cap": _number(_first_existing(raw, ["总市值"])),
            "source": source,
            "updated_at": updated_at,
        }
    )

    securities = pd.DataFrame(
        {
            "market": "A",
            "symbol": symbol,
            "asset_type": "stock",
            "board": symbol.map(lambda value: infer_a_board(value, "stock")),
            "name": name,
            "exchange": symbol.map(infer_a_exchange),
            "currency": "CNY",
            "status": name.map(lambda value: infer_status(value, "stock")),
            "is_st": name.map(is_st_name),
            "is_hk_connect": False,
            "metadata_source": source,
            "metadata_confidence": "high",
            "updated_at": updated_at,
        }
    )

    snapshots = snapshots.dropna(subset=["symbol"])
    return securities.drop_duplicates(["market", "symbol"]), snapshots


def normalize_hk_spot(
    raw: pd.DataFrame,
    source: str,
    hk_connect_symbols: set[str] | None = None,
    hk_connect_source: str = "akshare.hk_connect.unavailable",
    hk_connect_confidence: str = "low",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    today = pd.Timestamp.today().normalize()
    updated_at = _now()

    symbol = _clean_hk_symbol(_first_existing(raw, ["代码", "股票代码"]))
    name = _first_existing(raw, ["中文名称", "名称", "股票简称"]).astype(str)
    hk_connect_symbols = hk_connect_symbols or set()
    is_hk_connect = symbol.isin(hk_connect_symbols)

    snapshots = pd.DataFrame(
        {
            "market": "HK",
            "symbol": symbol,
            "asset_type": "stock",
            "board": is_hk_connect.map(lambda value: "港股通" if value else "非港股通"),
            "trade_date": today,
            "name": name,
            "last_price": _number(_first_existing(raw, ["最新价", "收盘"])),
            "pct_change": _number(_first_existing(raw, ["涨跌幅"])),
            "volume": _number(_first_existing(raw, ["成交量"])),
            "amount": _number(_first_existing(raw, ["成交额"])),
            "turnover_rate": _number(_first_existing(raw, ["换手率"])),
            "pe_ttm": _number(_first_existing(raw, ["市盈率", "市盈率-动态"])),
            "pb": _number(_first_existing(raw, ["市净率"])),
            "market_cap": _number(_first_existing(raw, ["总市值"])),
            "source": source,
            "updated_at": updated_at,
        }
    )

    securities = pd.DataFrame(
        {
            "market": "HK",
            "symbol": symbol,
            "asset_type": "stock",
            "board": is_hk_connect.map(lambda value: infer_board("HK", "", "", "stock", value)),
            "name": name,
            "exchange": "HKEX",
            "currency": "HKD",
            "status": "listed",
            "is_st": False,
            "is_hk_connect": is_hk_connect,
            "metadata_source": f"{source}; hk_connect={hk_connect_source}",
            "metadata_confidence": hk_connect_confidence,
            "updated_at": updated_at,
        }
    )

    snapshots = snapshots.dropna(subset=["symbol"])
    return securities.drop_duplicates(["market", "symbol"]), snapshots


def _a_symbol_with_exchange(symbol: str) -> str:
    clean = _clean_a_symbol(pd.Series([symbol])).iloc[0]
    if clean.startswith(("60", "68", "90")):
        return f"sh{clean}"
    if clean.startswith(("00", "30", "20")):
        return f"sz{clean}"
    if clean.startswith(("43", "83", "87", "88", "92")):
        return f"bj{clean}"
    return clean


def _fetch_first_available(calls: list[tuple[str, object]]) -> tuple[pd.DataFrame, str]:
    last_error: Exception | None = None
    for source, func in calls:
        for _ in range(2):
            try:
                raw = func()
                if raw is None or raw.empty:
                    raise RuntimeError(f"{source} returned empty data")
                return raw, source
            except Exception as exc:
                last_error = exc
                sleep(1)
    raise RuntimeError(f"All AKShare spot sources failed. Last error: {last_error}") from last_error


def fetch_spot(market: Market) -> tuple[pd.DataFrame, pd.DataFrame]:
    import akshare as ak

    if market == "A":
        raw, source = _fetch_first_available(
            [
                ("akshare.stock_zh_a_spot_em", ak.stock_zh_a_spot_em),
                ("akshare.stock_zh_a_spot", ak.stock_zh_a_spot),
            ]
        )
        return normalize_a_spot(raw, source)
    if market == "HK":
        hk_connect_symbols, hk_connect_source, hk_connect_confidence = fetch_hk_connect_symbols_with_meta()
        raw, source = _fetch_first_available(
            [
                ("akshare.stock_hk_spot_em", ak.stock_hk_spot_em),
                ("akshare.stock_hk_spot", ak.stock_hk_spot),
            ]
        )
        return normalize_hk_spot(
            raw,
            source,
            hk_connect_symbols=hk_connect_symbols,
            hk_connect_source=hk_connect_source,
            hk_connect_confidence=hk_connect_confidence,
        )
    raise ValueError(f"Unsupported market: {market}")


def normalize_a_etf_spot(raw: pd.DataFrame, source: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    today = pd.Timestamp.today().normalize()
    updated_at = _now()

    symbol = _clean_a_symbol(_first_existing(raw, ["代码", "基金代码"]))
    name = _first_existing(raw, ["名称", "基金简称"]).astype(str)
    board = pd.Series(["ETF"] * len(raw), index=raw.index)

    snapshots = pd.DataFrame(
        {
            "market": "A",
            "symbol": symbol,
            "asset_type": "etf",
            "board": board,
            "trade_date": today,
            "name": name,
            "last_price": _number(_first_existing(raw, ["最新价", "收盘"])),
            "pct_change": _number(_first_existing(raw, ["涨跌幅"])),
            "volume": _number(_first_existing(raw, ["成交量"])),
            "amount": _number(_first_existing(raw, ["成交额"])),
            "turnover_rate": _number(_first_existing(raw, ["换手率"])),
            "pe_ttm": pd.NA,
            "pb": pd.NA,
            "market_cap": _number(_first_existing(raw, ["总市值", "流通市值"])),
            "source": source,
            "updated_at": updated_at,
        }
    )

    securities = pd.DataFrame(
        {
            "market": "A",
            "symbol": symbol,
            "asset_type": "etf",
            "board": board,
            "name": name,
            "exchange": symbol.map(infer_a_exchange),
            "currency": "CNY",
            "status": "listed",
            "is_st": False,
            "is_hk_connect": False,
            "metadata_source": source,
            "metadata_confidence": "high",
            "updated_at": updated_at,
        }
    )

    snapshots = snapshots.dropna(subset=["symbol"])
    return securities.drop_duplicates(["market", "symbol"]), snapshots


def fetch_a_etf_spot() -> tuple[pd.DataFrame, pd.DataFrame]:
    import akshare as ak

    raw, source = _fetch_first_available(
        [
            ("akshare.fund_etf_spot_em", ak.fund_etf_spot_em),
        ]
    )
    return normalize_a_etf_spot(raw, source)


def fetch_hk_connect_symbols_with_meta() -> tuple[set[str], str, str]:
    import akshare as ak

    calls = [
        ("akshare.stock_hk_ggt_components_em", ak.stock_hk_ggt_components_em),
        ("akshare.stock_hsgt_sh_hk_spot_em", ak.stock_hsgt_sh_hk_spot_em),
    ]
    for source, func in calls:
        try:
            raw = func()
            if raw is None or raw.empty:
                continue
            symbol = _clean_hk_symbol(_first_existing(raw, ["代码", "股票代码"]))
            values = {item for item in symbol.dropna().astype(str) if item and item != "00000"}
            if values:
                return values, source, "high"
        except Exception:
            continue
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
    try:
        raw = ak.stock_hsgt_stock_statistics_em(
            symbol="南向持股",
            start_date=start_date,
            end_date=end_date,
        )
        if raw is not None and not raw.empty:
            symbol = _clean_hk_symbol(_first_existing(raw, ["股票代码", "代码"]))
            values = {item for item in symbol.dropna().astype(str) if item and item != "00000"}
            if values:
                return values, "akshare.stock_hsgt_stock_statistics_em", "medium"
    except Exception:
        pass
    return set(), "akshare.hk_connect.unavailable", "low"


def fetch_hk_connect_symbols() -> set[str]:
    symbols, _, _ = fetch_hk_connect_symbols_with_meta()
    return symbols


def _normalize_history(
    raw: pd.DataFrame,
    market: Market,
    symbol: str,
    source: str,
    adj_type: str,
) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()

    updated_at = _now()
    date_series = pd.to_datetime(_first_existing(raw, ["日期", "date", "时间"]), errors="coerce")
    return pd.DataFrame(
        {
            "market": market,
            "symbol": _clean_a_symbol(pd.Series([symbol])).iloc[0]
            if market == "A"
            else _clean_hk_symbol(pd.Series([symbol])).iloc[0],
            "trade_date": date_series,
            "open": _number(_first_existing(raw, ["开盘", "open"])),
            "high": _number(_first_existing(raw, ["最高", "high"])),
            "low": _number(_first_existing(raw, ["最低", "low"])),
            "close": _number(_first_existing(raw, ["收盘", "close"])),
            "volume": _number(_first_existing(raw, ["成交量", "volume"])),
            "amount": _number(_first_existing(raw, ["成交额", "amount"])),
            "adj_type": adj_type,
            "source": source,
            "updated_at": updated_at,
        }
    ).dropna(subset=["trade_date", "close"])


def fetch_history(
    market: Market,
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    import akshare as ak

    if market == "A":
        clean = _clean_a_symbol(pd.Series([symbol])).iloc[0]
        prefixed = _a_symbol_with_exchange(clean)
        calls = [
            (
                "akshare.stock_zh_a_daily",
                lambda: ak.stock_zh_a_daily(
                    symbol=prefixed,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                ),
            ),
            (
                "akshare.stock_zh_a_hist_tx",
                lambda: ak.stock_zh_a_hist_tx(
                    symbol=prefixed,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                    timeout=15,
                ),
            ),
            (
                "akshare.stock_zh_a_hist",
                lambda: ak.stock_zh_a_hist(
                    symbol=clean,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                    timeout=15,
                ),
            ),
        ]
    elif market == "HK":
        clean = _clean_hk_symbol(pd.Series([symbol])).iloc[0]
        calls = [
            (
                "akshare.stock_hk_daily",
                lambda: ak.stock_hk_daily(symbol=clean, adjust=adjust),
            ),
            (
                "akshare.stock_hk_hist",
                lambda: ak.stock_hk_hist(
                    symbol=clean,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                ),
            ),
        ]
    else:
        raise ValueError(f"Unsupported market: {market}")

    last_error: Exception | None = None
    for source, func in calls:
        try:
            raw = func()
            history = _normalize_history(raw, market=market, symbol=symbol, source=source, adj_type=adjust)
            if history.empty:
                raise RuntimeError(f"{source} returned empty history")
            start = pd.to_datetime(start_date)
            end = pd.to_datetime(end_date)
            return history[(history["trade_date"] >= start) & (history["trade_date"] <= end)]
        except Exception as exc:
            last_error = exc
            sleep(0.3)
    raise RuntimeError(f"All history sources failed for {market}:{symbol}. Last error: {last_error}")


def _empty_tags() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "market",
            "symbol",
            "tag_type",
            "tag_name",
            "evidence_level",
            "source",
            "updated_at",
        ]
    )


def _fetch_a_board_tags_em(
    kind: Literal["industry", "concept"], limit: int | None = None
) -> pd.DataFrame:
    import akshare as ak

    if kind == "industry":
        boards = ak.stock_board_industry_name_em()
        name_column = "板块名称"
        fetch_members = ak.stock_board_industry_cons_em
        source = "akshare.stock_board_industry_cons_em"
    elif kind == "concept":
        boards = ak.stock_board_concept_name_em()
        name_column = "板块名称"
        fetch_members = ak.stock_board_concept_cons_em
        source = "akshare.stock_board_concept_cons_em"
    else:
        raise ValueError(f"Unsupported tag kind: {kind}")

    if name_column not in boards.columns:
        raise RuntimeError(f"AKShare board list missing expected column: {name_column}")

    board_names = boards[name_column].dropna().astype(str).tolist()
    if limit is not None:
        board_names = board_names[:limit]

    rows: list[pd.DataFrame] = []
    updated_at = _now()
    for board_name in board_names:
        try:
            members = fetch_members(symbol=board_name)
        except Exception:
            continue
        symbol = _first_existing(members, ["代码", "股票代码"]).astype(str).str.zfill(6)
        tag_df = pd.DataFrame(
            {
                "market": "A",
                "symbol": symbol,
                "tag_type": kind,
                "tag_name": board_name,
                "evidence_level": "C",
                "source": source,
                "updated_at": updated_at,
            }
        )
        rows.append(tag_df.dropna(subset=["symbol"]))

    if not rows:
        return _empty_tags()

    return pd.concat(rows, ignore_index=True).drop_duplicates(
        ["market", "symbol", "tag_type", "tag_name", "source"]
    )


def _fetch_a_board_tags_sina(
    kind: Literal["industry", "concept"], limit: int | None = None
) -> pd.DataFrame:
    import akshare as ak

    indicator = "新浪行业" if kind == "industry" else "概念"
    source = "akshare.stock_sector_detail.sina"
    boards = ak.stock_sector_spot(indicator=indicator)
    required = {"label", "板块"}
    if not required.issubset(boards.columns):
        raise RuntimeError(f"Sina board list missing expected columns: {required}")

    board_rows = boards[["label", "板块"]].dropna().drop_duplicates().to_dict("records")
    if limit is not None:
        board_rows = board_rows[:limit]

    rows: list[pd.DataFrame] = []
    updated_at = _now()
    for board in board_rows:
        try:
            members = ak.stock_sector_detail(sector=str(board["label"]))
        except Exception:
            continue
        symbol = _clean_a_symbol(_first_existing(members, ["code", "symbol", "代码", "股票代码"]))
        tag_df = pd.DataFrame(
            {
                "market": "A",
                "symbol": symbol,
                "tag_type": kind,
                "tag_name": str(board["板块"]),
                "evidence_level": "C",
                "source": source,
                "updated_at": updated_at,
            }
        )
        rows.append(tag_df.dropna(subset=["symbol"]))

    if not rows:
        return _empty_tags()

    return pd.concat(rows, ignore_index=True).drop_duplicates(
        ["market", "symbol", "tag_type", "tag_name", "source"]
    )


def fetch_a_board_tags(kind: Literal["industry", "concept"], limit: int | None = None) -> pd.DataFrame:
    try:
        em_tags = _fetch_a_board_tags_em(kind=kind, limit=limit)
        if not em_tags.empty:
            return em_tags
    except Exception:
        pass
    return _fetch_a_board_tags_sina(kind=kind, limit=limit)
