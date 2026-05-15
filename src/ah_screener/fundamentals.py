from __future__ import annotations

import math
import json
from datetime import datetime
from time import sleep
from typing import Any, Literal

import numpy as np
import pandas as pd

from ah_screener.sources.us_client import fetch_sec_companyfacts


Market = Literal["A", "HK", "US"]


STATEMENTS = ("income", "balance", "cashflow")
METADATA_COLUMNS = {
    "SECUCODE",
    "SECURITY_CODE",
    "SECURITY_NAME_ABBR",
    "ORG_CODE",
    "ORG_TYPE",
    "REPORT_DATE",
    "REPORT_TYPE",
    "REPORT_DATE_NAME",
    "SECURITY_TYPE_CODE",
    "NOTICE_DATE",
    "UPDATE_DATE",
    "CURRENCY",
    "DATE_TYPE_CODE",
    "FISCAL_YEAR",
    "START_DATE",
    "STD_REPORT_DATE",
    "IS_CNY_CODE",
    "IS_BZ",
}


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now())


def _num(value: object) -> float:
    if value is None:
        return np.nan
    try:
        if pd.isna(value):
            return np.nan
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _first(row: pd.Series, names: list[str]) -> float:
    for name in names:
        if name in row.index:
            value = _num(row[name])
            if not math.isnan(value):
                return value
    return np.nan


def _sum_first(row: pd.Series, names: list[str]) -> float:
    values = [_num(row.get(name)) for name in names if name in row.index]
    values = [value for value in values if not math.isnan(value)]
    return float(sum(values)) if values else np.nan


def _ratio(numerator: float, denominator: float, scale: float = 1.0) -> float:
    if math.isnan(numerator) or math.isnan(denominator) or denominator <= 0:
        return np.nan
    return float(numerator / denominator * scale)


def _clean_a_symbol(symbol: str) -> str:
    return str(symbol).lower().replace("sh", "").replace("sz", "").replace("bj", "").zfill(6)


def _clean_hk_symbol(symbol: str) -> str:
    return str(symbol).lower().replace("hk", "").zfill(5)


def _clean_us_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().replace("/", ".")


def _a_report_symbol(symbol: str) -> str:
    clean = _clean_a_symbol(symbol)
    if clean.startswith(("60", "68", "90")):
        return f"SH{clean}"
    if clean.startswith(("00", "30", "20")):
        return f"SZ{clean}"
    if clean.startswith(("43", "83", "87", "88", "92")):
        return f"BJ{clean}"
    return clean


def _a_indicator_symbol(symbol: str) -> str:
    clean = _clean_a_symbol(symbol)
    if clean.startswith(("60", "68", "90")):
        return f"{clean}.SH"
    if clean.startswith(("00", "30", "20")):
        return f"{clean}.SZ"
    if clean.startswith(("43", "83", "87", "88", "92")):
        return f"{clean}.BJ"
    return clean


def _rows_to_items(
    market: Market,
    symbol: str,
    statement_type: str,
    raw: pd.DataFrame,
    source: str,
) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    updated_at = _now()
    clean_symbol = _clean_a_symbol(symbol) if market == "A" else _clean_hk_symbol(symbol)
    for _, row in raw.iterrows():
        report_date = pd.to_datetime(row.get("REPORT_DATE") or row.get("日期"), errors="coerce")
        if pd.isna(report_date):
            continue
        report_type = row.get("REPORT_TYPE") or row.get("REPORT_DATE_NAME") or row.get("DATE_TYPE_CODE")
        currency = row.get("CURRENCY")
        for column, value in row.items():
            if column in METADATA_COLUMNS:
                continue
            amount = _num(value)
            if math.isnan(amount):
                continue
            rows.append(
                {
                    "market": market,
                    "symbol": clean_symbol,
                    "statement_type": statement_type,
                    "report_date": report_date,
                    "report_type": str(report_type) if report_type is not None else None,
                    "item_code": str(column),
                    "item_name": str(column),
                    "amount": amount,
                    "currency": str(currency) if currency is not None else None,
                    "source": source,
                    "updated_at": updated_at,
                }
            )
    return pd.DataFrame(rows)


def _hk_rows_to_items(
    statement_type: str,
    raw: pd.DataFrame,
    source: str,
) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    updated_at = _now()
    return pd.DataFrame(
        {
            "market": "HK",
            "symbol": raw["SECURITY_CODE"].astype(str).str.zfill(5),
            "statement_type": statement_type,
            "report_date": pd.to_datetime(raw["REPORT_DATE"], errors="coerce"),
            "report_type": raw.get("DATE_TYPE_CODE"),
            "item_code": raw["STD_ITEM_CODE"].astype(str),
            "item_name": raw["STD_ITEM_NAME"].astype(str),
            "amount": pd.to_numeric(raw["AMOUNT"], errors="coerce"),
            "currency": None,
            "source": source,
            "updated_at": updated_at,
        }
    ).dropna(subset=["report_date", "amount"])


def _latest_by_report_date(df: pd.DataFrame) -> pd.Series | None:
    if df.empty or "REPORT_DATE" not in df.columns:
        return None
    temp = df.copy()
    temp["REPORT_DATE"] = pd.to_datetime(temp["REPORT_DATE"], errors="coerce")
    temp = temp.dropna(subset=["REPORT_DATE"]).sort_values("REPORT_DATE", ascending=False)
    if temp.empty:
        return None
    return temp.iloc[0]


def _score_metric(value: float, low: float, high: float, reverse: bool = False) -> float:
    if math.isnan(value):
        return 50.0
    ratio = (value - low) / (high - low)
    score = np.clip(ratio * 100, 0, 100)
    return float(100 - score if reverse else score)


def _series_first(df: pd.DataFrame, names: list[str]) -> pd.Series:
    for name in names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _cagr(latest: float, earliest: float, years: float) -> float:
    if any(math.isnan(value) for value in [latest, earliest, years]):
        return np.nan
    if latest <= 0 or earliest <= 0 or years <= 0:
        return np.nan
    return float(((latest / earliest) ** (1 / years) - 1) * 100)


def _stability_score(values: pd.Series, penalty_per_point: float) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return 50.0
    return float(np.clip(100 - clean.std(ddof=0) * penalty_per_point, 0, 100))


def _innovation_efficiency_score(rd_expense_ratio: float, capex_to_operating_cashflow: float) -> float:
    rd_score = _score_metric(rd_expense_ratio, 1, 10)
    capex_funding_score = _score_metric(capex_to_operating_cashflow, 0.15, 1.25, reverse=True)
    return float(np.clip(rd_score * 0.55 + capex_funding_score * 0.45, 0, 100))


def _fundamental_trend_metrics(
    indicators: pd.DataFrame,
    revenue_names: list[str],
    profit_names: list[str],
    roe_names: list[str],
    margin_names: list[str],
) -> dict[str, float]:
    if indicators.empty or "REPORT_DATE" not in indicators.columns:
        return {
            "revenue_cagr_3y": np.nan,
            "net_profit_cagr_3y": np.nan,
            "roe_avg_3y": np.nan,
            "roe_stability_score": 50.0,
            "margin_stability_score": 50.0,
            "fundamental_trend_score": 50.0,
        }

    history = indicators.copy()
    history["REPORT_DATE"] = pd.to_datetime(history["REPORT_DATE"], errors="coerce")
    history = history.dropna(subset=["REPORT_DATE"]).sort_values("REPORT_DATE", ascending=False)
    history = history.drop_duplicates("REPORT_DATE")
    annual = history[history["REPORT_DATE"].dt.month.eq(12) & history["REPORT_DATE"].dt.day.eq(31)]
    if len(annual) >= 2:
        history = annual
    history = history.head(4).copy()
    if len(history) < 2:
        return {
            "revenue_cagr_3y": np.nan,
            "net_profit_cagr_3y": np.nan,
            "roe_avg_3y": np.nan,
            "roe_stability_score": 50.0,
            "margin_stability_score": 50.0,
            "fundamental_trend_score": 50.0,
        }

    latest = history.iloc[0]
    earliest = history.iloc[-1]
    years = max((latest["REPORT_DATE"] - earliest["REPORT_DATE"]).days / 365.25, 1.0)
    revenue = _series_first(history, revenue_names)
    profit = _series_first(history, profit_names)
    roe = _series_first(history, roe_names)
    margin = _series_first(history, margin_names)

    revenue_cagr = _cagr(float(revenue.iloc[0]), float(revenue.iloc[-1]), years)
    profit_cagr = _cagr(float(profit.iloc[0]), float(profit.iloc[-1]), years)
    roe_avg = float(roe.dropna().mean()) if roe.notna().any() else np.nan
    roe_stability = _stability_score(roe, penalty_per_point=4.0)
    margin_stability = _stability_score(margin, penalty_per_point=3.0)
    growth_trend = (
        _score_metric(revenue_cagr, -5, 20) * 0.45
        + _score_metric(profit_cagr, -10, 25) * 0.55
    )
    quality_trend = (
        _score_metric(roe_avg, 5, 18) * 0.45
        + roe_stability * 0.35
        + margin_stability * 0.20
    )
    trend_score = float(np.clip(growth_trend * 0.55 + quality_trend * 0.45, 0, 100))
    return {
        "revenue_cagr_3y": revenue_cagr,
        "net_profit_cagr_3y": profit_cagr,
        "roe_avg_3y": roe_avg,
        "roe_stability_score": roe_stability,
        "margin_stability_score": margin_stability,
        "fundamental_trend_score": trend_score,
    }


def _quality_scores(
    roe: float,
    gross_margin: float,
    net_margin: float,
    debt_asset_ratio: float,
    cashflow_to_profit: float,
    revenue_yoy: float,
    net_profit_yoy: float,
) -> tuple[float, float, float, float, float, list[str]]:
    warnings: list[str] = []
    quality = (
        _score_metric(roe, 0, 18) * 0.45
        + _score_metric(gross_margin, 15, 60) * 0.25
        + _score_metric(net_margin, 3, 25) * 0.30
    )
    growth = _score_metric(revenue_yoy, -5, 30) * 0.45 + _score_metric(net_profit_yoy, -10, 35) * 0.55
    balance = _score_metric(debt_asset_ratio, 20, 75, reverse=True)
    cashflow = _score_metric(cashflow_to_profit, 0.2, 1.2)

    if not math.isnan(roe) and roe < 5:
        warnings.append("ROE偏低")
    if not math.isnan(debt_asset_ratio) and debt_asset_ratio > 70:
        warnings.append("资产负债率偏高")
    if not math.isnan(cashflow_to_profit) and cashflow_to_profit < 0.6:
        warnings.append("经营现金流对利润覆盖不足")
    if not math.isnan(revenue_yoy) and revenue_yoy < 0:
        warnings.append("收入同比下滑")
    if not math.isnan(net_profit_yoy) and net_profit_yoy < 0:
        warnings.append("利润同比下滑")

    fundamental = quality * 0.35 + growth * 0.25 + balance * 0.20 + cashflow * 0.20
    return (
        float(np.clip(quality, 0, 100)),
        float(np.clip(growth, 0, 100)),
        float(np.clip(balance, 0, 100)),
        float(np.clip(cashflow, 0, 100)),
        float(np.clip(fundamental, 0, 100)),
        warnings,
    )


def _a_metric_row(
    symbol: str,
    indicators: pd.DataFrame,
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    latest = _latest_by_report_date(indicators)
    if latest is None:
        return pd.DataFrame()

    report_date = pd.to_datetime(latest["REPORT_DATE"])
    name = latest.get("SECURITY_NAME_ABBR")
    report_type = latest.get("REPORT_TYPE") or latest.get("REPORT_DATE_NAME")

    income_row = _row_for_report(income, report_date)
    balance_row = _row_for_report(balance, report_date)
    cash_row = _row_for_report(cashflow, report_date)

    revenue = _first(latest, ["TOTALOPERATEREVE"])
    revenue_yoy = _first(latest, ["TOTALOPERATEREVETZ", "DJD_TOI_YOY"])
    parent_profit = _first(latest, ["PARENTNETPROFIT"])
    net_profit_yoy = _first(latest, ["PARENTNETPROFITTZ", "DJD_DPNP_YOY"])
    deducted_profit = _first(latest, ["KCFJCXSYJLR"])
    roe = _first(latest, ["ROEJQ", "ROE_YEARLY"])
    roa = _first(latest, ["ROA", "ZZCJLL"])
    gross_margin = _first(latest, ["XSMLL"])
    net_margin = _first(latest, ["XSJLL", "NET_PROFIT_RATIO"])
    debt_asset_ratio = _first(latest, ["ZCFZL"])
    current_ratio = _first(latest, ["LD"])
    ocf_to_revenue = _first(latest, ["JYXJLYYSR", "OCF_SALES"])
    trend = _fundamental_trend_metrics(
        indicators,
        revenue_names=["TOTALOPERATEREVE", "OPERATE_INCOME"],
        profit_names=["PARENTNETPROFIT", "KCFJCXSYJLR"],
        roe_names=["ROEJQ", "ROE_YEARLY"],
        margin_names=["XSJLL", "NET_PROFIT_RATIO"],
    )

    total_assets = _first(balance_row, ["TOTAL_ASSETS"])
    total_liabilities = _first(balance_row, ["TOTAL_LIABILITIES"])
    total_equity = _first(balance_row, ["TOTAL_EQUITY", "TOTAL_PARENT_EQUITY"])
    operating_cashflow = _first(cash_row, ["NETCASH_OPERATE", "NETCASH_OPERATENOTE"])
    gross_profit = _first(income_row, ["OPERATE_PROFIT", "TOTAL_PROFIT"])
    rd_expense = _first(income_row, ["RESEARCH_EXPENSE", "ME_RESEARCH_EXPENSE"])
    capex = abs(_first(cash_row, ["CONSTRUCT_LONG_ASSET"]))

    cashflow_to_profit = operating_cashflow / parent_profit if parent_profit and parent_profit > 0 else np.nan
    if math.isnan(debt_asset_ratio) and total_assets and total_assets > 0:
        debt_asset_ratio = total_liabilities / total_assets * 100
    if math.isnan(ocf_to_revenue) and revenue and revenue > 0:
        ocf_to_revenue = operating_cashflow / revenue * 100

    scores = _quality_scores(
        roe=roe,
        gross_margin=gross_margin,
        net_margin=net_margin,
        debt_asset_ratio=debt_asset_ratio,
        cashflow_to_profit=cashflow_to_profit,
        revenue_yoy=revenue_yoy,
        net_profit_yoy=net_profit_yoy,
    )
    return _metric_frame(
        snapshot_date,
        "A",
        _clean_a_symbol(symbol),
        name,
        report_date,
        report_type,
        revenue,
        revenue_yoy,
        gross_profit,
        parent_profit,
        net_profit_yoy,
        deducted_profit,
        operating_cashflow,
        total_assets,
        total_liabilities,
        total_equity,
        roe,
        roa,
        gross_margin,
        net_margin,
        debt_asset_ratio,
        current_ratio,
        cashflow_to_profit,
        ocf_to_revenue,
        rd_expense,
        capex,
        scores,
        trend,
    )


def _row_for_report(df: pd.DataFrame, report_date: pd.Timestamp) -> pd.Series:
    if df.empty or "REPORT_DATE" not in df.columns:
        return pd.Series(dtype=object)
    temp = df.copy()
    temp["REPORT_DATE"] = pd.to_datetime(temp["REPORT_DATE"], errors="coerce")
    matched = temp[temp["REPORT_DATE"] == report_date]
    if matched.empty:
        matched = temp.sort_values("REPORT_DATE", ascending=False).head(1)
    return matched.iloc[0] if not matched.empty else pd.Series(dtype=object)


def _items_by_name_for_report(items: pd.DataFrame, report_date: pd.Timestamp) -> pd.Series:
    if items.empty or not {"REPORT_DATE", "STD_ITEM_NAME", "AMOUNT"}.issubset(items.columns):
        return pd.Series(dtype=float)
    item_dates = pd.to_datetime(items["REPORT_DATE"], errors="coerce")
    item_for_date = items[item_dates == report_date]
    if item_for_date.empty:
        return pd.Series(dtype=float)
    return item_for_date.set_index("STD_ITEM_NAME")["AMOUNT"]


def _metric_frame(
    snapshot_date: pd.Timestamp,
    market: Market,
    symbol: str,
    name: object,
    report_date: pd.Timestamp,
    report_type: object,
    revenue: float,
    revenue_yoy: float,
    gross_profit: float,
    parent_profit: float,
    net_profit_yoy: float,
    deducted_profit: float,
    operating_cashflow: float,
    total_assets: float,
    total_liabilities: float,
    total_equity: float,
    roe: float,
    roa: float,
    gross_margin: float,
    net_margin: float,
    debt_asset_ratio: float,
    current_ratio: float,
    cashflow_to_profit: float,
    ocf_to_revenue: float,
    rd_expense: float,
    capex: float,
    scores: tuple[float, float, float, float, float, list[str]],
    trend: dict[str, float] | None = None,
) -> pd.DataFrame:
    quality, growth, balance, cashflow, fundamental, warnings = scores
    trend_defaults = {
        "revenue_cagr_3y": np.nan,
        "net_profit_cagr_3y": np.nan,
        "roe_avg_3y": np.nan,
        "roe_stability_score": 50.0,
        "margin_stability_score": 50.0,
        "fundamental_trend_score": 50.0,
    }
    trend = {**trend_defaults, **(trend or {})}
    trend_score = _num(trend["fundamental_trend_score"])
    if math.isnan(trend_score):
        trend_score = 50.0
    if trend_score < 40:
        warnings.append("多期成长或稳定性偏弱")
    rd_expense_ratio = _ratio(rd_expense, revenue, scale=100)
    capex_to_revenue = _ratio(capex, revenue, scale=100)
    capex_to_operating_cashflow = _ratio(capex, operating_cashflow)
    innovation_score = _innovation_efficiency_score(rd_expense_ratio, capex_to_operating_cashflow)
    if not math.isnan(capex_to_operating_cashflow) and capex_to_operating_cashflow > 1.25:
        warnings.append("资本开支对经营现金流占用偏高")
    enhanced_fundamental = fundamental * 0.72 + trend_score * 0.22 + innovation_score * 0.06
    return pd.DataFrame(
        [
            {
                "snapshot_date": snapshot_date,
                "market": market,
                "symbol": symbol,
                "name": str(name) if name is not None else None,
                "report_date": report_date,
                "report_type": str(report_type) if report_type is not None else None,
                "revenue": revenue,
                "revenue_yoy": revenue_yoy,
                "gross_profit": gross_profit,
                "parent_net_profit": parent_profit,
                "net_profit_yoy": net_profit_yoy,
                "deducted_net_profit": deducted_profit,
                "operating_cashflow": operating_cashflow,
                "total_assets": total_assets,
                "total_liabilities": total_liabilities,
                "total_equity": total_equity,
                "roe": roe,
                "roa": roa,
                "gross_margin": gross_margin,
                "net_margin": net_margin,
                "debt_asset_ratio": debt_asset_ratio,
                "current_ratio": current_ratio,
                "cashflow_to_profit": cashflow_to_profit,
                "ocf_to_revenue": ocf_to_revenue,
                "rd_expense": rd_expense,
                "rd_expense_ratio": rd_expense_ratio,
                "capex": capex,
                "capex_to_revenue": capex_to_revenue,
                "capex_to_operating_cashflow": capex_to_operating_cashflow,
                "innovation_efficiency_score": innovation_score,
                "revenue_cagr_3y": trend["revenue_cagr_3y"],
                "net_profit_cagr_3y": trend["net_profit_cagr_3y"],
                "roe_avg_3y": trend["roe_avg_3y"],
                "roe_stability_score": trend["roe_stability_score"],
                "margin_stability_score": trend["margin_stability_score"],
                "fundamental_trend_score": trend_score,
                "quality_score": quality,
                "growth_score": growth,
                "balance_score": balance,
                "cashflow_score": cashflow,
                "fundamental_score": float(np.clip(enhanced_fundamental, 0, 100)),
                "warnings": json.dumps(warnings, ensure_ascii=False),
                "updated_at": _now(),
            }
        ]
    )


def _hk_metric_row(
    symbol: str,
    indicators: pd.DataFrame,
    income_items: pd.DataFrame,
    balance_items: pd.DataFrame,
    cashflow_items: pd.DataFrame,
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    latest = _latest_by_report_date(indicators)
    if latest is None:
        return pd.DataFrame()
    report_date = pd.to_datetime(latest["REPORT_DATE"])
    name = latest.get("SECURITY_NAME_ABBR")
    report_type = latest.get("DATE_TYPE_CODE")

    by_name = _items_by_name_for_report(balance_items, report_date)
    income_by_name = _items_by_name_for_report(income_items, report_date)
    cashflow_by_name = _items_by_name_for_report(cashflow_items, report_date)
    total_assets = _num(by_name.get("总资产"))
    total_liabilities = _num(by_name.get("总负债"))
    total_equity = _num(by_name.get("总权益", by_name.get("股东权益", by_name.get("净资产"))))
    rd_expense = abs(_num(income_by_name.get("研发费用")))
    capex = abs(_sum_first(cashflow_by_name, ["购建固定资产", "购建无形资产及其他资产"]))

    revenue = _first(latest, ["OPERATE_INCOME"])
    revenue_yoy = _first(latest, ["OPERATE_INCOME_YOY"])
    gross_profit = _first(latest, ["GROSS_PROFIT"])
    parent_profit = _first(latest, ["HOLDER_PROFIT"])
    net_profit_yoy = _first(latest, ["HOLDER_PROFIT_YOY"])
    roe = _first(latest, ["ROE_AVG", "ROE_YEARLY"])
    roa = _first(latest, ["ROA"])
    gross_margin = _first(latest, ["GROSS_PROFIT_RATIO"])
    net_margin = _first(latest, ["NET_PROFIT_RATIO"])
    debt_asset_ratio = _first(latest, ["DEBT_ASSET_RATIO"])
    current_ratio = _first(latest, ["CURRENT_RATIO"])
    ocf_to_revenue = _first(latest, ["OCF_SALES"])
    operating_cashflow = revenue * ocf_to_revenue / 100 if revenue and not math.isnan(ocf_to_revenue) else np.nan
    cashflow_to_profit = operating_cashflow / parent_profit if parent_profit and parent_profit > 0 else np.nan
    trend = _fundamental_trend_metrics(
        indicators,
        revenue_names=["OPERATE_INCOME"],
        profit_names=["HOLDER_PROFIT"],
        roe_names=["ROE_AVG", "ROE_YEARLY"],
        margin_names=["NET_PROFIT_RATIO"],
    )

    scores = _quality_scores(
        roe=roe,
        gross_margin=gross_margin,
        net_margin=net_margin,
        debt_asset_ratio=debt_asset_ratio,
        cashflow_to_profit=cashflow_to_profit,
        revenue_yoy=revenue_yoy,
        net_profit_yoy=net_profit_yoy,
    )
    return _metric_frame(
        snapshot_date,
        "HK",
        _clean_hk_symbol(symbol),
        name,
        report_date,
        report_type,
        revenue,
        revenue_yoy,
        gross_profit,
        parent_profit,
        net_profit_yoy,
        np.nan,
        operating_cashflow,
        total_assets,
        total_liabilities,
        total_equity,
        roe,
        roa,
        gross_margin,
        net_margin,
        debt_asset_ratio,
        current_ratio,
        cashflow_to_profit,
        ocf_to_revenue,
        rd_expense,
        capex,
        scores,
        trend,
    )


SEC_FACT_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
    "gross_profit": ("GrossProfit",),
    "parent_net_profit": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cashflow": ("NetCashProvidedByUsedInOperatingActivities",),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "total_equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "rd_expense": ("ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
}


def _sec_fact_rows(companyfacts: dict[str, Any], tags: tuple[str, ...]) -> pd.DataFrame:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    rows: list[dict[str, object]] = []
    for tag in tags:
        concept = facts.get(tag)
        if not concept:
            continue
        units = concept.get("units", {})
        unit_rows = units.get("USD") or units.get("USD/shares") or []
        for item in unit_rows:
            value = _num(item.get("val"))
            end = pd.to_datetime(item.get("end"), errors="coerce")
            if math.isnan(value) or pd.isna(end):
                continue
            rows.append(
                {
                    "tag": tag,
                    "REPORT_DATE": end,
                    "REPORT_TYPE": item.get("form"),
                    "FISCAL_YEAR": item.get("fy"),
                    "FISCAL_PERIOD": item.get("fp"),
                    "FILED": item.get("filed"),
                    "AMOUNT": value,
                }
            )
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["FILED"] = pd.to_datetime(frame["FILED"], errors="coerce")
    return frame.sort_values(["REPORT_DATE", "FILED"], ascending=[False, False])


def _latest_annual_fact(companyfacts: dict[str, Any], tags: tuple[str, ...]) -> pd.Series | None:
    rows = _sec_fact_rows(companyfacts, tags)
    if rows.empty:
        return None
    annual = rows[
        rows["REPORT_TYPE"].astype(str).str.upper().isin(["10-K", "20-F", "40-F"])
        | rows["FISCAL_PERIOD"].astype(str).str.upper().eq("FY")
    ]
    selected = annual if not annual.empty else rows
    selected = selected.drop_duplicates(["REPORT_DATE"], keep="first")
    return selected.iloc[0] if not selected.empty else None


def _annual_fact_history(companyfacts: dict[str, Any], tags: tuple[str, ...], column: str) -> pd.DataFrame:
    rows = _sec_fact_rows(companyfacts, tags)
    if rows.empty:
        return pd.DataFrame(columns=["REPORT_DATE", column])
    annual = rows[
        rows["REPORT_TYPE"].astype(str).str.upper().isin(["10-K", "20-F", "40-F"])
        | rows["FISCAL_PERIOD"].astype(str).str.upper().eq("FY")
    ]
    selected = annual if not annual.empty else rows
    selected = selected.drop_duplicates(["REPORT_DATE"], keep="first").copy()
    return selected.rename(columns={"AMOUNT": column})[["REPORT_DATE", column]]


def _sec_items(
    symbol: str,
    companyfacts: dict[str, Any],
    source: str,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    updated_at = _now()
    for item_code, tags in SEC_FACT_TAGS.items():
        facts = _sec_fact_rows(companyfacts, tags).head(8)
        for _, row in facts.iterrows():
            rows.append(
                {
                    "market": "US",
                    "symbol": _clean_us_symbol(symbol),
                    "statement_type": "sec_companyfacts",
                    "report_date": row["REPORT_DATE"],
                    "report_type": row.get("REPORT_TYPE"),
                    "item_code": str(row["tag"]),
                    "item_name": item_code,
                    "amount": row["AMOUNT"],
                    "currency": "USD",
                    "source": source,
                    "updated_at": updated_at,
                }
            )
    return pd.DataFrame(rows)


def _us_metric_row(
    symbol: str,
    meta: dict[str, Any],
    companyfacts: dict[str, Any],
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    values: dict[str, float] = {}
    report_dates: list[pd.Timestamp] = []
    for metric, tags in SEC_FACT_TAGS.items():
        latest = _latest_annual_fact(companyfacts, tags)
        values[metric] = _num(latest["AMOUNT"]) if latest is not None else np.nan
        if latest is not None:
            report_dates.append(pd.to_datetime(latest["REPORT_DATE"]))

    report_date = max(report_dates) if report_dates else pd.NaT
    revenue = values["revenue"]
    parent_profit = values["parent_net_profit"]
    gross_profit = values["gross_profit"]
    total_assets = values["total_assets"]
    total_liabilities = values["total_liabilities"]
    total_equity = values["total_equity"]
    operating_cashflow = values["operating_cashflow"]
    rd_expense = values["rd_expense"]
    capex = abs(values["capex"]) if not math.isnan(values["capex"]) else np.nan

    revenue_history = _annual_fact_history(companyfacts, SEC_FACT_TAGS["revenue"], "TOTALOPERATEREVE")
    profit_history = _annual_fact_history(companyfacts, SEC_FACT_TAGS["parent_net_profit"], "PARENTNETPROFIT")
    indicators = revenue_history.merge(profit_history, on="REPORT_DATE", how="outer")
    if not indicators.empty:
        indicators["ROE_YEARLY"] = parent_profit / total_equity * 100 if total_equity and total_equity > 0 else np.nan
        indicators["NET_PROFIT_RATIO"] = parent_profit / revenue * 100 if revenue and revenue > 0 else np.nan
    trend = _fundamental_trend_metrics(
        indicators,
        revenue_names=["TOTALOPERATEREVE"],
        profit_names=["PARENTNETPROFIT"],
        roe_names=["ROE_YEARLY"],
        margin_names=["NET_PROFIT_RATIO"],
    )

    history_sorted = revenue_history.sort_values("REPORT_DATE", ascending=False)
    revenue_yoy = np.nan
    if len(history_sorted) >= 2 and _num(history_sorted.iloc[1]["TOTALOPERATEREVE"]) > 0:
        revenue_yoy = (
            revenue / _num(history_sorted.iloc[1]["TOTALOPERATEREVE"]) - 1
        ) * 100
    profit_sorted = profit_history.sort_values("REPORT_DATE", ascending=False)
    net_profit_yoy = np.nan
    if len(profit_sorted) >= 2 and _num(profit_sorted.iloc[1]["PARENTNETPROFIT"]) != 0:
        previous_profit = _num(profit_sorted.iloc[1]["PARENTNETPROFIT"])
        net_profit_yoy = (parent_profit / abs(previous_profit) - 1) * 100

    roe = parent_profit / total_equity * 100 if total_equity and total_equity > 0 else np.nan
    roa = parent_profit / total_assets * 100 if total_assets and total_assets > 0 else np.nan
    gross_margin = gross_profit / revenue * 100 if revenue and revenue > 0 else np.nan
    net_margin = parent_profit / revenue * 100 if revenue and revenue > 0 else np.nan
    debt_asset_ratio = (
        total_liabilities / total_assets * 100 if total_assets and total_assets > 0 else np.nan
    )
    cashflow_to_profit = (
        operating_cashflow / parent_profit if parent_profit and parent_profit > 0 else np.nan
    )
    ocf_to_revenue = operating_cashflow / revenue * 100 if revenue and revenue > 0 else np.nan

    scores = _quality_scores(
        roe=roe,
        gross_margin=gross_margin,
        net_margin=net_margin,
        debt_asset_ratio=debt_asset_ratio,
        cashflow_to_profit=cashflow_to_profit,
        revenue_yoy=revenue_yoy,
        net_profit_yoy=net_profit_yoy,
    )
    return _metric_frame(
        snapshot_date,
        "US",
        _clean_us_symbol(symbol),
        meta.get("title", symbol),
        report_date,
        "10-K/20-F",
        revenue,
        revenue_yoy,
        gross_profit,
        parent_profit,
        net_profit_yoy,
        np.nan,
        operating_cashflow,
        total_assets,
        total_liabilities,
        total_equity,
        roe,
        roa,
        gross_margin,
        net_margin,
        debt_asset_ratio,
        np.nan,
        cashflow_to_profit,
        ocf_to_revenue,
        rd_expense,
        capex,
        scores,
        trend,
    )


def fetch_fundamentals(market: Market, symbol: str, snapshot_date: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    import akshare as ak

    items: list[pd.DataFrame] = []
    metrics = pd.DataFrame()
    if market == "A":
        report_symbol = _a_report_symbol(symbol)
        indicator_symbol = _a_indicator_symbol(symbol)
        income = _try(lambda: ak.stock_profit_sheet_by_report_em(symbol=report_symbol))
        balance = _try(lambda: ak.stock_balance_sheet_by_report_em(symbol=report_symbol))
        cashflow = _try(lambda: ak.stock_cash_flow_sheet_by_report_em(symbol=report_symbol))
        indicators = _try(
            lambda: ak.stock_financial_analysis_indicator_em(
                symbol=indicator_symbol,
                indicator="按报告期",
            )
        )
        items.extend(
            [
                _rows_to_items("A", symbol, "income", income, "akshare.stock_profit_sheet_by_report_em"),
                _rows_to_items("A", symbol, "balance", balance, "akshare.stock_balance_sheet_by_report_em"),
                _rows_to_items("A", symbol, "cashflow", cashflow, "akshare.stock_cash_flow_sheet_by_report_em"),
            ]
        )
        metrics = _a_metric_row(symbol, indicators, income, balance, cashflow, snapshot_date)
    elif market == "HK":
        clean = _clean_hk_symbol(symbol)
        income = _try(lambda: ak.stock_financial_hk_report_em(stock=clean, symbol="利润表", indicator="年度"))
        balance = _try(lambda: ak.stock_financial_hk_report_em(stock=clean, symbol="资产负债表", indicator="年度"))
        cashflow = _try(lambda: ak.stock_financial_hk_report_em(stock=clean, symbol="现金流量表", indicator="年度"))
        indicators = _try(lambda: ak.stock_financial_hk_analysis_indicator_em(symbol=clean, indicator="年度"))
        items.extend(
            [
                _hk_rows_to_items("income", income, "akshare.stock_financial_hk_report_em"),
                _hk_rows_to_items("balance", balance, "akshare.stock_financial_hk_report_em"),
                _hk_rows_to_items("cashflow", cashflow, "akshare.stock_financial_hk_report_em"),
            ]
        )
        metrics = _hk_metric_row(clean, indicators, income, balance, cashflow, snapshot_date)
    elif market == "US":
        meta, companyfacts = fetch_sec_companyfacts(symbol)
        source = "sec.edgar.companyfacts"
        items.append(_sec_items(symbol, companyfacts, source))
        metrics = _us_metric_row(symbol, meta, companyfacts, snapshot_date)
    else:
        raise ValueError(f"Unsupported market: {market}")

    item_df = pd.concat([frame for frame in items if not frame.empty], ignore_index=True) if items else pd.DataFrame()
    return item_df, metrics


def _try(func):
    last_error: Exception | None = None
    for _ in range(2):
        try:
            value = func()
            return value if value is not None else pd.DataFrame()
        except Exception as exc:
            last_error = exc
            sleep(0.5)
    return pd.DataFrame()
