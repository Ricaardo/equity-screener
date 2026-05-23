"""Single entry point for slicing the tradable universe by asset class.

Before this module, ``asset_type == 'stock'`` (and ``== 'etf'``) filters were
copy-pasted across pipeline / reporting. Routing every slice through
``select_assets`` means a new asset class is registered in one place. See
docs/master-plan.md stage 4 / R3.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

import pandas as pd


class AssetClass(str, Enum):
    STOCK = "stock"
    ETF = "etf"


STOCKS: tuple[AssetClass, ...] = (AssetClass.STOCK,)
ETFS: tuple[AssetClass, ...] = (AssetClass.ETF,)


def asset_values(asset_classes: Iterable[AssetClass | str]) -> set[str]:
    return {ac.value if isinstance(ac, AssetClass) else str(ac) for ac in asset_classes}


def select_assets(
    df: pd.DataFrame, asset_classes: Iterable[AssetClass | str] = STOCKS
) -> pd.DataFrame:
    """Filter a snapshots/securities frame by asset class (default: stocks only).

    A frame with no ``asset_type`` column is treated as all-stock and returned
    unchanged when stocks are requested, empty otherwise.
    """
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    wanted = asset_values(asset_classes)
    if "asset_type" not in df.columns:
        return df if AssetClass.STOCK.value in wanted else df.iloc[0:0]
    return df[df["asset_type"].fillna("stock").isin(wanted)]


def latest_per_security(df: pd.DataFrame, date_column: str = "trade_date") -> pd.DataFrame:
    """Latest row per (market, symbol) by ``date_column``."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    out[date_column] = pd.to_datetime(out[date_column], errors="coerce")
    return out.sort_values(date_column).drop_duplicates(["market", "symbol"], keep="last")
