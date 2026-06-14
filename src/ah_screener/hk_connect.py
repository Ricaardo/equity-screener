"""港股通 (HK Stock Connect / Southbound) universe and eligibility provider.

This module ports the standalone ``build_hk_stock_report.py`` script into the
``ah_screener`` package as a reusable class hierarchy:

* ``HKConnectDataSource`` — Protocol (pluggable seam): any implementation must
  return four DataFrames (HKEX securities, SSE southbound, SZSE southbound,
  TradingView quotes).
* ``SnapshotDataSource`` — reads the bundled manual-snapshot files from
  ``src/ah_screener/data/hk_connect/``.
* ``LiveDataSource`` — stub; raises ``NotImplementedError``; documents how
  ``hkexnews_client`` / ``futu_client`` can be wired in later.
* ``HKConnectUniverse`` — takes any data source, builds the merged equity
  universe DataFrame with ``connect_eligible`` / ``connect_sh`` /
  ``connect_sz`` markers, and exposes ``eligible_universe()``,
  ``full_universe()``, and ``build_report()``.
"""

from __future__ import annotations

import json
import math
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pandas as pd


# ---------------------------------------------------------------------------
# Default snapshot data directory (bundled with the package)
# ---------------------------------------------------------------------------

_DEFAULT_DATA_DIR: Path = Path(__file__).resolve().parent / "data" / "hk_connect"

# Public URLs kept for the report footer (mirrors the standalone script)
_HKEX_SECURITIES_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
)
_HKEX_ELIGIBLE_URL = (
    "https://www.hkex.com.hk/Mutual-Market/Stock-Connect/Eligible-Stocks/"
    "View-All-Eligible-Securities?sc_lang=en"
)
_SSE_SOUTHBOUND_URL = "https://www.sse.com.cn/services/hkexsc/disclo/eligible/"
_SZSE_SOUTHBOUND_URL = "https://www.szse.cn/szhk/hkbussiness/underlylist/"
_TRADINGVIEW_ALL_HK_URL = (
    "https://www.tradingview.com/markets/stocks-hong-kong/market-movers-all-stocks/"
)


# ---------------------------------------------------------------------------
# Low-level helpers (ported verbatim from build_hk_stock_report.py)
# ---------------------------------------------------------------------------


def _normalize_code(value: Any) -> str:
    if pd.isna(value):
        return ""
    code = str(value).strip()
    if code.endswith(".0"):
        code = code[:-2]
    code = re.sub(r"\s+", "", code)
    if code.isdigit() and len(code) <= 5:
        return code.zfill(5)
    return code


def _clean_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).replace("　", " ").strip()


def _as_number(value: Any) -> float | None:
    if value in (None, "", "-", "N/A"):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(x):
        return None
    return x


def _fmt_money(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "-"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.2f} 万亿"
    if abs_value >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿"
    if abs_value >= 10_000:
        return f"{value / 10_000:.2f} 万"
    return f"{value:.2f}"


def _read_jsonp(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^[^(]*\((.*)\)\s*$", text, flags=re.S)
    return json.loads(match.group(1) if match else text)


def _xlsx_col_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref.upper())
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def _read_xlsx_first_sheet(path: Path) -> list[list[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                shared.append("".join(t.text or "" for t in si.findall(".//a:t", ns)))

        sheet_name = "xl/worksheets/sheet1.xml"
        sheet_root = ET.fromstring(archive.read(sheet_name))
        rows: list[list[str]] = []
        for row_node in sheet_root.findall(".//a:sheetData/a:row", ns):
            row_values: list[str] = []
            for cell in row_node.findall("a:c", ns):
                ref = cell.attrib.get("r", "")
                idx = _xlsx_col_index(ref)
                while len(row_values) <= idx:
                    row_values.append("")
                cell_type = cell.attrib.get("t")
                value_node = cell.find("a:v", ns)
                if cell_type == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall(".//a:t", ns))
                elif value_node is None:
                    value = ""
                elif cell_type == "s":
                    value = shared[int(value_node.text or "0")]
                else:
                    value = value_node.text or ""
                row_values[idx] = value
            rows.append(row_values)
    return rows


# ---------------------------------------------------------------------------
# Protocol: pluggable data-source seam
# ---------------------------------------------------------------------------


@runtime_checkable
class HKConnectDataSource(Protocol):
    """Protocol for 港股通 data providers.

    All four methods must return a DataFrame plus an optional metadata string
    (update label / date) so callers can surface freshness in reports.

    ``get_szse_southbound`` may return an empty DataFrame with a ``stock_code``
    column when the SZSE source is unavailable.
    """

    def get_hkex_securities(self) -> tuple[pd.DataFrame, str]:
        """Return (securities_df, update_label_str).

        The DataFrame must contain at minimum: ``stock_code``, ``name_en_hkex``,
        ``category``, ``trading_currency_hkex``.
        """
        ...

    def get_sse_southbound(self) -> tuple[pd.DataFrame, str]:
        """Return (sse_df, update_date_str).

        The DataFrame must contain ``stock_code`` plus SSE name columns.
        """
        ...

    def get_szse_southbound(self) -> tuple[pd.DataFrame, str]:
        """Return (szse_df, update_date_str).

        The DataFrame must contain ``stock_code`` plus SZSE name columns.
        """
        ...

    def get_tradingview_quotes(self) -> pd.DataFrame:
        """Return a quotes DataFrame with columns: ``stock_code``, ``last_price``,
        ``market_cap``, ``change_percent``, ``sector``, ``industry``, etc.
        """
        ...


# ---------------------------------------------------------------------------
# SnapshotDataSource: reads bundled manual snapshots from disk
# ---------------------------------------------------------------------------


class SnapshotDataSource:
    """Reads the manual snapshot files bundled in ``data/hk_connect/``.

    The default ``data_dir`` points to the files migrated into the package.
    Pass a custom path to load alternative snapshots.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or _DEFAULT_DATA_DIR

    # -- internal helpers ----------------------------------------------------

    def _path(self, filename: str) -> Path:
        return self._data_dir / filename

    # -- public API (satisfies HKConnectDataSource Protocol) -----------------

    def get_hkex_securities(self) -> tuple[pd.DataFrame, str]:
        """Load HKEX ListOfSecurities.xlsx from the snapshot directory."""
        xlsx_path = self._path("ListOfSecurities.xlsx")
        rows = _read_xlsx_first_sheet(xlsx_path)
        update_label = _clean_text(rows[0][0])
        headers = rows[2]
        raw = pd.DataFrame(rows[3:], columns=headers)
        raw = raw.rename(
            columns={
                "Stock Code": "stock_code",
                "Name of Securities": "name_en_hkex",
                "Category": "category",
                "Sub-Category": "sub_category",
                "Board Lot": "board_lot",
                "ISIN": "isin",
                "Expiry Date": "expiry_date",
                "Subject to Stamp Duty": "stamp_duty",
                "Shortsell Eligible": "shortsell_eligible",
                "CAS Eligible": "cas_eligible",
                "VCM Eligible": "vcm_eligible",
                "Admitted to CCASS": "ccass_eligible",
                "Debt Securities Board Lot (Nominal)": "debt_board_lot_nominal",
                "Debt Securities Investor Type": "debt_investor_type",
                "POS Eligible": "pos_eligible",
                "Trading Currency": "trading_currency_hkex",
                "RMB Counter": "rmb_counter",
            }
        )
        spread_cols = [c for c in raw.columns if "Spread Table" in c]
        if spread_cols:
            raw = raw.rename(columns={spread_cols[0]: "spread_table"})
        raw["stock_code"] = raw["stock_code"].map(_normalize_code)
        raw["name_en_hkex"] = raw["name_en_hkex"].map(_clean_text)
        raw = raw[raw["stock_code"] != ""].copy()
        return raw, update_label

    def get_sse_southbound(self) -> tuple[pd.DataFrame, str]:
        """Load SSE southbound eligible list from the snapshot JSONP file."""
        jsonp_path = self._path("sse_southbound.jsonp")
        data = _read_jsonp(jsonp_path)
        rows = data.get("result") or data.get("pageHelp", {}).get("data") or []
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["stock_code"]), ""
        df = df.rename(
            columns={
                "SECURITY_CODE": "stock_code",
                "ABBR_EN": "sse_name_en",
                "ABBR_CN": "sse_name_cn",
                "SECURITY_TYPE": "sse_security_type",
                "UPDATE_DATE": "sse_update_date",
                "TRADE_FLAG": "sse_trade_flag",
            }
        )
        df["stock_code"] = df["stock_code"].map(_normalize_code)
        for col in ["sse_name_en", "sse_name_cn", "sse_security_type", "sse_update_date"]:
            if col in df.columns:
                df[col] = df[col].map(_clean_text)
        update_date = (
            _clean_text(df["sse_update_date"].dropna().iloc[0])
            if "sse_update_date" in df.columns and not df["sse_update_date"].dropna().empty
            else ""
        )
        return df, update_date

    def get_szse_southbound(self) -> tuple[pd.DataFrame, str]:
        """Load SZSE southbound eligible list from the snapshot JSON file."""
        all_path = self._path("szse_southbound_all.json")
        payload = json.loads(all_path.read_text(encoding="utf-8"))
        rows = payload["rows"]
        update_date = payload.get("update_date", "")
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["stock_code"]), update_date
        df = df.rename(
            columns={
                "zqdm": "stock_code",
                "zqjc": "szse_name_cn",
                "zqywjc": "szse_name_en",
            }
        )
        df["stock_code"] = df["stock_code"].map(_normalize_code)
        for col in ["szse_name_cn", "szse_name_en"]:
            if col in df.columns:
                df[col] = df[col].map(_clean_text)
        return df, update_date

    def get_tradingview_quotes(self) -> pd.DataFrame:
        """Load TradingView HK quotes from the snapshot JSON file."""
        tv_path = self._path("tradingview_hk_stocks.json")
        payload = json.loads(tv_path.read_text(encoding="utf-8"))
        columns = [
            "tv_code",
            "tv_name",
            "last_price",
            "change_percent",
            "volume",
            "market_cap",
            "price_currency",
            "sector",
            "industry",
            "exchange",
        ]
        rows: list[dict[str, Any]] = []
        for item in payload.get("data", []):
            values = item.get("d", [])
            row = dict(zip(columns, values, strict=False))
            row["tv_symbol"] = item.get("s", "")
            row["stock_code"] = _normalize_code(row.get("tv_code"))
            rows.append(row)
        df = pd.DataFrame(rows)
        for col in ["last_price", "change_percent", "volume", "market_cap"]:
            if col in df.columns:
                df[col] = df[col].map(_as_number)
        return df.drop_duplicates("stock_code", keep="first") if not df.empty else df


# ---------------------------------------------------------------------------
# LiveDataSource: stub for future live-fetch wiring
# ---------------------------------------------------------------------------


class LiveDataSource:
    """Stub live data source — not yet implemented.

    Future implementation can reuse:
    - ``ah_screener.sources.hkexnews_client`` for HKEX securities and announcements.
    - ``ah_screener.sources.futu_client`` for real-time HK quotes and market caps.
    - SSE / SZSE southbound APIs (see URLs in build_hk_stock_report.py) for
      eligible-stock lists.

    All four methods raise ``NotImplementedError`` until wired.
    """

    def get_hkex_securities(self) -> tuple[pd.DataFrame, str]:
        raise NotImplementedError(
            "live HK connect fetch not yet wired; see ah_screener.sources.hkexnews_client"
        )

    def get_sse_southbound(self) -> tuple[pd.DataFrame, str]:
        raise NotImplementedError(
            "live HK connect fetch not yet wired; see ah_screener.sources.hkexnews_client"
        )

    def get_szse_southbound(self) -> tuple[pd.DataFrame, str]:
        raise NotImplementedError(
            "live HK connect fetch not yet wired; see ah_screener.sources.hkexnews_client"
        )

    def get_tradingview_quotes(self) -> pd.DataFrame:
        raise NotImplementedError(
            "live HK connect fetch not yet wired; see ah_screener.sources.futu_client"
        )


# ---------------------------------------------------------------------------
# Internal merge helpers
# ---------------------------------------------------------------------------


def _merge_connect(sse: pd.DataFrame, szse: pd.DataFrame) -> pd.DataFrame:
    """Outer-join SSE and SZSE southbound lists, adding connect_sh / connect_sz flags."""
    sse_cols = ["stock_code", "sse_name_en", "sse_name_cn", "sse_security_type", "sse_update_date"]
    sse_extra = [c for c in ["sse_trade_flag"] if c in sse.columns]
    sse_small = sse[[c for c in sse_cols + sse_extra if c in sse.columns]].drop_duplicates(
        "stock_code"
    )
    szse_cols = ["stock_code"]
    for col in ["szse_name_cn", "szse_name_en"]:
        if col in szse.columns:
            szse_cols.append(col)
    szse_small = szse[szse_cols].drop_duplicates("stock_code")
    connect = pd.merge(sse_small, szse_small, on="stock_code", how="outer")
    connect["connect_sh"] = connect["sse_name_en"].notna() if "sse_name_en" in connect.columns else False
    connect["connect_sz"] = connect["szse_name_en"].notna() if "szse_name_en" in connect.columns else False
    connect["connect_name_cn"] = (
        connect["sse_name_cn"].combine_first(connect["szse_name_cn"])
        if "sse_name_cn" in connect.columns and "szse_name_cn" in connect.columns
        else connect.get("sse_name_cn", pd.Series(dtype=str))
    )
    connect["connect_name_en"] = (
        connect["sse_name_en"].combine_first(connect["szse_name_en"])
        if "sse_name_en" in connect.columns and "szse_name_en" in connect.columns
        else connect.get("sse_name_en", pd.Series(dtype=str))
    )
    connect["connect_source"] = connect.apply(
        lambda r: ";".join(
            source
            for source, flag in (
                ("Shanghai Connect", r["connect_sh"]),
                ("Shenzhen Connect", r["connect_sz"]),
            )
            if bool(flag)
        ),
        axis=1,
    )
    return connect


def _summarize_group(df: pd.DataFrame, label: str) -> dict[str, Any]:
    market_cap = df["market_cap"].dropna() if "market_cap" in df.columns else pd.Series(dtype=float)
    change = df["change_percent"].dropna() if "change_percent" in df.columns else pd.Series(dtype=float)
    price = df["last_price"] if "last_price" in df.columns else pd.Series(dtype=float)
    return {
        "group": label,
        "security_count": len(df),
        "quote_count": int(price.notna().sum()),
        "market_cap_count": int(market_cap.count()),
        "market_cap_sum": float(market_cap.sum()) if len(market_cap) else None,
        "market_cap_median": float(market_cap.median()) if len(market_cap) else None,
        "market_cap_mean": float(market_cap.mean()) if len(market_cap) else None,
        "avg_change_percent": float(change.mean()) if change.notna().any() else None,
    }


def _markdown_table(rows: list[list[Any]], headers: list[str]) -> str:
    def cell(value: Any) -> str:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "-"
        return str(value)

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HKConnectUniverse: the main public class
# ---------------------------------------------------------------------------


# Preferred column order for the full equity universe DataFrame
_PREFERRED_COLS = [
    "stock_code",
    "name_en_hkex",
    "category",
    "sub_category",
    "board_lot",
    "isin",
    "trading_currency_hkex",
    "rmb_counter",
    "stamp_duty",
    "shortsell_eligible",
    "cas_eligible",
    "vcm_eligible",
    "ccass_eligible",
    "connect_eligible",
    "connect_sh",
    "connect_sz",
    "connect_source",
    "connect_name_cn",
    "connect_name_en",
    "sse_update_date",
    "sse_security_type",
    "last_price",
    "price_currency",
    "change_percent",
    "volume",
    "market_cap",
    "sector",
    "industry",
    "tv_symbol",
    "tv_name",
    "quote_source",
]


class HKConnectUniverse:
    """港股通-enriched HK equity universe.

    Accepts any ``HKConnectDataSource`` implementation (``SnapshotDataSource``
    by default) and lazily builds the merged DataFrame on first access.

    Usage::

        u = HKConnectUniverse(SnapshotDataSource())
        eligible = u.eligible_universe()   # connect_eligible == True only
        full = u.full_universe()           # all Equity/REIT rows with markers
        report = u.build_report()          # markdown report string
    """

    def __init__(self, source: HKConnectDataSource) -> None:
        self._source = source
        self._equity: pd.DataFrame | None = None
        self._raw: pd.DataFrame | None = None
        self._meta: dict[str, Any] = {}

    # -- internal build ------------------------------------------------------

    def _build(self) -> None:
        """Fetch all data, merge, and cache results."""
        raw, hkex_update_label = self._source.get_hkex_securities()
        sse, sse_update_date = self._source.get_sse_southbound()
        szse, szse_update_date = self._source.get_szse_southbound()
        quotes = self._source.get_tradingview_quotes()
        tradingview_total = len(quotes)

        connect = _merge_connect(sse, szse)

        equity = raw[raw["category"].isin(["Equity", "Real Estate Investment Trusts"])].copy()
        equity = pd.merge(equity, connect, on="stock_code", how="left")
        equity["connect_sh"] = equity["connect_sh"].where(equity["connect_sh"].notna(), other=False).astype(bool)
        equity["connect_sz"] = equity["connect_sz"].where(equity["connect_sz"].notna(), other=False).astype(bool)
        equity["connect_eligible"] = equity["connect_sh"] | equity["connect_sz"]
        equity["connect_source"] = equity["connect_source"].fillna("")
        equity = pd.merge(equity, quotes, on="stock_code", how="left")
        equity["quote_source"] = equity["last_price"].map(
            lambda x: "TradingView" if pd.notna(x) else ""
        )

        remaining_cols = [c for c in equity.columns if c not in _PREFERRED_COLS]
        present_preferred = [c for c in _PREFERRED_COLS if c in equity.columns]
        equity = equity[present_preferred + remaining_cols]

        self._raw = raw
        self._equity = equity
        self._meta = {
            "hkex_update_label": hkex_update_label,
            "sse_update_date": sse_update_date,
            "szse_update_date": szse_update_date,
            "raw_count": len(raw),
            "tradingview_total": tradingview_total,
            "sse_count": len(sse),
            "szse_count": len(szse),
        }

    def _ensure_built(self) -> None:
        if self._equity is None:
            self._build()

    # -- public API ----------------------------------------------------------

    def full_universe(self) -> pd.DataFrame:
        """All Equity and REIT rows from the HKEX list, with connect_eligible markers."""
        self._ensure_built()
        assert self._equity is not None
        return self._equity.copy()

    def eligible_universe(self) -> pd.DataFrame:
        """Rows where ``connect_eligible == True`` (港股通南向并集)."""
        full = self.full_universe()
        return full[full["connect_eligible"]].copy()

    def build_report(self) -> str:
        """Generate the 港股通 vs 全港股 comparison report as a Markdown string."""
        self._ensure_built()
        assert self._equity is not None
        assert self._raw is not None
        equity = self._equity

        connect = equity[equity["connect_eligible"]].copy()
        non_connect = equity[~equity["connect_eligible"]].copy()
        hkd = equity[equity["trading_currency_hkex"].eq("HKD")].copy()
        hkd_connect = hkd[hkd["connect_eligible"]].copy()
        hkd_non_connect = hkd[~hkd["connect_eligible"]].copy()

        hkd_connect_cap = (
            hkd_connect["market_cap"].dropna().sum() if "market_cap" in hkd_connect.columns else 0.0
        )
        hkd_non_connect_cap = (
            hkd_non_connect["market_cap"].dropna().sum()
            if "market_cap" in hkd_non_connect.columns
            else 0.0
        )
        hkd_total_cap = hkd_connect_cap + hkd_non_connect_cap
        cap_share: float | None = (
            hkd_connect_cap / hkd_total_cap * 100 if hkd_total_cap else None
        )

        top_connect = connect.sort_values("market_cap", ascending=False, na_position="last").head(
            15
        )[
            [
                c
                for c in [
                    "stock_code",
                    "name_en_hkex",
                    "connect_name_cn",
                    "last_price",
                    "market_cap",
                    "change_percent",
                    "sector",
                ]
                if c in connect.columns
            ]
        ]
        top_rows = [
            [
                r.stock_code if hasattr(r, "stock_code") else "-",
                getattr(r, "name_en_hkex", "-"),
                getattr(r, "connect_name_cn", "-"),
                r.last_price if pd.notna(r.last_price) else "-",
                _fmt_money(r.market_cap if pd.notna(r.market_cap) else None),
                f"{r.change_percent:.2f}%" if pd.notna(r.change_percent) else "-",
                r.sector if pd.notna(r.sector) else "-",
            ]
            for r in top_connect.itertuples(index=False)
        ]

        overlap = int((connect["connect_sh"] & connect["connect_sz"]).sum())
        only_sh = int((connect["connect_sh"] & ~connect["connect_sz"]).sum())
        only_sz = int((~connect["connect_sh"] & connect["connect_sz"]).sum())

        summary = pd.DataFrame(
            [
                _summarize_group(equity, "All equity/REIT securities"),
                _summarize_group(connect, "Stock Connect eligible"),
                _summarize_group(non_connect, "Non Stock Connect"),
                _summarize_group(hkd, "HKD equity/REIT securities"),
                _summarize_group(hkd_connect, "HKD Stock Connect eligible"),
                _summarize_group(hkd_non_connect, "HKD Non Stock Connect"),
            ]
        )

        summary_rows = []
        for row in summary.itertuples(index=False):
            quote_cov = row.quote_count / row.security_count * 100 if row.security_count else 0
            summary_rows.append(
                [
                    row.group,
                    row.security_count,
                    f"{quote_cov:.1f}%",
                    _fmt_money(row.market_cap_sum),
                    _fmt_money(row.market_cap_median),
                    (
                        f"{row.avg_change_percent:.2f}%"
                        if row.avg_change_percent is not None
                        else "-"
                    ),
                ]
            )

        meta = self._meta
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
        cap_share_str = f"{cap_share:.1f}%" if cap_share is not None else "-"
        equity_pct = len(connect) / len(equity) * 100 if len(equity) else 0.0

        report = f"""# 港股通 vs 全港股标的对比报告

生成时间：{now}

## 数据口径

- 全证券清单：HKEX `ListOfSecurities.xlsx`，文件标注 `{meta["hkex_update_label"]}`。
- 港股通南向名单：上交所港股通标的证券名单更新日 `{meta["sse_update_date"]}`；深交所港股通标的证券名单更新日 `{meta["szse_update_date"]}`。
- 行情/市值：TradingView Hong Kong stock screener，抓取 `{meta["tradingview_total"]}` 条香港股票记录。
- 主对比表只纳入 HKEX `Equity` 与 `Real Estate Investment Trusts`，排除了衍生权证、牛熊证、债券和 ETP。原始全证券清单另存 CSV。

## 关键结论

- HKEX 原始全证券清单共有 `{meta["raw_count"]:,}` 条；股票/REIT 主宇宙共有 `{len(equity):,}` 条。
- 港股通南向并集共有 `{len(connect):,}` 条，其中沪深两边都在名单内 `{overlap:,}` 条，仅沪港通 `{only_sh:,}` 条，仅深港通 `{only_sz:,}` 条。
- 按 HKD 交易证券且有 TradingView 市值的样本计算，港股通标的市值合计约 `{_fmt_money(hkd_connect_cap)}`，非港股通约 `{_fmt_money(hkd_non_connect_cap)}`；港股通约占 `{cap_share_str}`。
- 港股通数量只占股票/REIT 主宇宙约 `{equity_pct:.1f}%`，但市值覆盖明显更高，说明名单集中在大中型、高流动性标的。

## 分组对比

{_markdown_table(summary_rows, ["分组", "证券数", "价格覆盖率", "市值合计", "市值中位数", "平均涨跌幅"])}

## 港股通市值前 15

{_markdown_table(top_rows, ["代码", "HKEX 英文名", "中文名", "最新价", "市值", "涨跌幅", "行业"])}

## 输出文件

- `hkex_all_securities_raw.csv`：HKEX 全证券清单原样清洗版。
- `hk_equity_universe_with_connect_quotes.csv`：股票/REIT 主宇宙，含港股通标记、行情和市值。
- `hk_connect_constituents_with_quotes.csv`：港股通南向并集清单，含行情和市值。
- `hk_equity_comparison_summary.csv`：分组统计。

## 限制

- TradingView 行情字段没有逐行提供"行情时间"，报告用抓取时间和市场日历解释；不应用于交易级实时判断。
- 市值字段有缺失，合计和占比只统计有市值数据的样本。
- 双柜台、不同交易货币、暂停交易或新上市证券可能造成覆盖差异；CSV 保留原始交易货币，便于后续再按 issuer 去重。

## 数据源链接

- HKEX Securities Lists: {_HKEX_SECURITIES_URL}
- HKEX Stock Connect Eligible Securities: {_HKEX_ELIGIBLE_URL}
- SSE 港股通标的证券名单: {_SSE_SOUTHBOUND_URL}
- SZSE 港股通标的证券名单: {_SZSE_SOUTHBOUND_URL}
- TradingView All Hong Kong Stocks: {_TRADINGVIEW_ALL_HK_URL}
"""
        return report

    def summary_stats(self) -> dict[str, Any]:
        """Return a dict of key counts for CLI printing."""
        self._ensure_built()
        assert self._equity is not None
        equity = self._equity
        connect = equity[equity["connect_eligible"]]
        return {
            "raw_securities": self._meta.get("raw_count", 0),
            "equity_universe": len(equity),
            "connect_eligible": len(connect),
            "sse_rows": self._meta.get("sse_count", 0),
            "szse_rows": self._meta.get("szse_count", 0),
            "tradingview_quotes": self._meta.get("tradingview_total", 0),
        }
