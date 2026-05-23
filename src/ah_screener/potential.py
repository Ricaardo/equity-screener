"""Potential-stock scanner: setup validation + scenario cards.

This module is intentionally price-first for v1. It avoids look-ahead bias by
using only OHLCV history for validation (docs/master-plan.md R1/R10/R13). Financial
and theme pillars are neutral placeholders until point-in-time fundamentals and
historical theme snapshots are available.
"""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

SNAPSHOT_SOURCE = "potential_v1_price_only"
FORWARD_DAYS = 40  # ~8 trading weeks
SETUP_STEP_DAYS = 20
# Calibrated by sweep_potential_thresholds on real history (stage 9): rank>=70 gave
# ~76% win / +10.7% median 8w excess vs ~57% at 60. In-sample, price-only.
RS_RANK_CUT = 70.0
RET_60D_CAP = 0.35


def _rank_pct(series: pd.Series, ascending: bool = True) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() == 0:
        return pd.Series(50.0, index=series.index)
    return (numeric.rank(pct=True, ascending=ascending) * 100).fillna(50).clip(0, 100)


def _price_features(prices: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for (market, symbol), group in prices.groupby(["market", "symbol"]):
        g = group.sort_values("trade_date").dropna(subset=["close"]).copy()
        if len(g) < 80:
            continue
        close = pd.to_numeric(g["close"], errors="coerce")
        high = pd.to_numeric(g["high"], errors="coerce")
        low = pd.to_numeric(g["low"], errors="coerce")
        volume = pd.to_numeric(g.get("volume", pd.Series(np.nan, index=g.index)), errors="coerce")
        returns = close.pct_change()
        vol20 = returns.rolling(20).std() * np.sqrt(252)
        vol_pct120 = vol20.rolling(120).rank(pct=True)
        high60 = high.rolling(60).max()
        low60 = low.rolling(60).min()
        high120 = high.rolling(120).max()
        out = pd.DataFrame(
            {
                "market": market,
                "symbol": symbol,
                "trade_date": pd.to_datetime(g["trade_date"], errors="coerce"),
                "close": close,
                "high60": high60,
                "low60": low60,
                "box_tightness": high60 / low60.replace(0, np.nan) - 1,
                "pivot": high60,
                "stop": low60,
                "pct_from_120d_high": close / high120.replace(0, np.nan) - 1,
                "ma20": close.rolling(20).mean(),
                "ma60": close.rolling(60).mean(),
                "ma120": close.rolling(120).mean(),
                "return_20d": close / close.shift(20) - 1,
                "return_60d": close / close.shift(60) - 1,
                "forward_40d_return": close.shift(-FORWARD_DAYS) / close - 1,
                "volatility_20d": vol20,
                "volatility_pct120": vol_pct120,
                "volume_ratio_20d": volume / volume.rolling(20).mean(),
            }
        )
        rows.append(out)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _setup_scores(features: pd.DataFrame) -> pd.DataFrame:
    if features.empty:
        return features
    df = features.copy()
    tight_score = (100 - (pd.to_numeric(df["box_tightness"], errors="coerce") / 0.30 * 100)).clip(0, 100)
    vol_score = (100 - pd.to_numeric(df["volatility_pct120"], errors="coerce") * 100).clip(0, 100)
    ma_score = pd.Series(0.0, index=df.index)
    ma_score = ma_score.mask(df["close"] > df["ma20"], ma_score + 35)
    ma_score = ma_score.mask(df["ma20"] >= df["ma60"], ma_score + 35)
    ma_score = ma_score.mask(df["close"] >= df["ma120"], ma_score + 30)
    near_pivot = pd.to_numeric(df["pct_from_120d_high"], errors="coerce").between(-0.25, -0.03)
    not_extended = pd.to_numeric(df["return_20d"], errors="coerce").fillna(0) <= 0.15
    df["base_setup"] = (
        tight_score * 0.30 + vol_score * 0.35 + ma_score * 0.25 + near_pivot.astype(float) * 10
    ).clip(0, 100)
    df["quiet_setup"] = (df["base_setup"] >= 55) & not_extended
    return df


def _sampled_setups(prices: pd.DataFrame) -> pd.DataFrame:
    """Non-overlapping historical setup samples with forward excess return (no look-ahead).

    Shared by validation and the threshold sweep. Forward return is the label only;
    it is never used to define a setup.
    """
    features = _setup_scores(_price_features(prices))
    if features.empty:
        return pd.DataFrame()
    sampled = features.sort_values(["market", "symbol", "trade_date"]).copy()
    sampled["setup_index"] = sampled.groupby(["market", "symbol"]).cumcount()
    sampled = sampled[
        sampled["setup_index"].ge(120)
        & ((sampled["setup_index"] - 120) % SETUP_STEP_DAYS == 0)
    ]
    sampled = sampled.dropna(subset=["forward_40d_return"])
    if sampled.empty:
        return sampled
    sampled["return_60d_rank"] = sampled.groupby("trade_date")["return_60d"].rank(pct=True) * 100
    sampled["forward_median"] = sampled.groupby("trade_date")["forward_40d_return"].transform("median")
    sampled["excess_40d_return"] = sampled["forward_40d_return"] - sampled["forward_median"]
    return sampled


def _excess_stats(excess: pd.Series) -> dict[str, float]:
    excess = excess.dropna()
    return {
        "sample_count": int(len(excess)),
        "win_rate": float((excess > 0).mean() * 100) if len(excess) else np.nan,
        "median_excess_40d": float(excess.median()) if len(excess) else np.nan,
        "p25_excess_40d": float(excess.quantile(0.25)) if len(excess) else np.nan,
        "p75_excess_40d": float(excess.quantile(0.75)) if len(excess) else np.nan,
    }


def validate_potential_signals(prices: pd.DataFrame) -> pd.DataFrame:
    """Validate setup signals using forward 8-week excess return vs same-date universe median."""
    sampled = _sampled_setups(prices)
    if sampled.empty:
        return pd.DataFrame()

    signal_masks = {
        "technical_base": sampled["quiet_setup"],
        "rs_quiet": sampled["quiet_setup"]
        & sampled["return_60d_rank"].ge(60)
        & sampled["return_60d"].fillna(0).lt(0.35),
        "near_pivot": sampled["pct_from_120d_high"].between(-0.25, -0.03)
        & sampled["return_20d"].fillna(0).lt(0.15),
    }
    rows = []
    for signal, mask in signal_masks.items():
        group = sampled[mask]
        if group.empty:
            continue
        rows.append(
            {
                "signal": signal,
                **_excess_stats(group["excess_40d_return"]),
                "bias_note": "price-only; current-listed universe; survivorship bias remains",
            }
        )
    return pd.DataFrame(rows).sort_values("median_excess_40d", ascending=False)


def sweep_potential_thresholds(
    prices: pd.DataFrame,
    rank_cuts: tuple[float, ...] = (50.0, 60.0, 70.0),
    ret_caps: tuple[float, ...] = (0.25, 0.35, 0.45),
) -> pd.DataFrame:
    """Grid-search the rs_quiet thresholds over historical setups (stage 9 calibration).

    For each (RS-rank cutoff, 60d-return cap) it reports the forward-8w excess-return
    distribution. In-sample / price-only — same survivorship caveat as validation;
    use to compare relative threshold choices, not as a live edge guarantee.
    """
    sampled = _sampled_setups(prices)
    if sampled.empty:
        return pd.DataFrame()
    rows = []
    for rank_cut in rank_cuts:
        for ret_cap in ret_caps:
            mask = (
                sampled["quiet_setup"]
                & sampled["return_60d_rank"].ge(rank_cut)
                & sampled["return_60d"].fillna(0).lt(ret_cap)
            )
            group = sampled[mask]
            if group.empty:
                continue
            rows.append(
                {
                    "rs_rank_cut": rank_cut,
                    "ret_60d_cap": ret_cap,
                    **_excess_stats(group["excess_40d_return"]),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("median_excess_40d", ascending=False).reset_index(drop=True)


def scan_potential_candidates(
    prices: pd.DataFrame,
    snapshots: pd.DataFrame,
    validation: pd.DataFrame | None = None,
    top: int = 80,
) -> pd.DataFrame:
    features = _setup_scores(_price_features(prices))
    if features.empty:
        return pd.DataFrame()
    latest = features.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
    if latest.empty:
        return latest
    latest["return_60d_rank"] = latest.groupby("market")["return_60d"].rank(pct=True) * 100
    latest["validated_setup"] = (
        latest["quiet_setup"]
        & latest["return_60d_rank"].ge(RS_RANK_CUT)
        & latest["return_60d"].fillna(0).lt(RET_60D_CAP)
    )
    latest["technical_setup_score"] = latest["base_setup"].fillna(50).clip(0, 100)
    latest["relative_strength_score"] = latest["return_60d_rank"].fillna(50).clip(0, 100)
    latest["fundamental_turn_score"] = 50.0
    latest["theme_early_score"] = 50.0
    latest["extended_penalty"] = np.where(latest["return_60d"].fillna(0) > 0.35, 25.0, 0.0)
    latest["potential_score"] = (
        latest["technical_setup_score"] * 0.35
        + latest["relative_strength_score"] * 0.25
        + latest["fundamental_turn_score"] * 0.15
        + latest["theme_early_score"] * 0.25
        - latest["extended_penalty"]
    ).clip(0, 100)
    latest = latest[latest["validated_setup"]].copy()
    if latest.empty:
        return latest

    meta = snapshots.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
    meta_cols = [c for c in ["market", "symbol", "name", "asset_type", "amount", "board"] if c in meta.columns]
    latest = latest.merge(meta[meta_cols], on=["market", "symbol"], how="left")
    if "asset_type" in latest.columns:
        latest = latest[latest["asset_type"].fillna("stock").eq("stock")]
    if "amount" in latest.columns:
        latest = latest[pd.to_numeric(latest["amount"], errors="coerce").fillna(0) >= 20_000_000]

    val = validation if validation is not None and not validation.empty else validate_potential_signals(prices)
    val_index = val.set_index("signal") if val is not None and not val.empty else pd.DataFrame()
    win = float(val_index.loc["rs_quiet", "win_rate"]) if "rs_quiet" in val_index.index else np.nan
    med = float(val_index.loc["rs_quiet", "median_excess_40d"]) if "rs_quiet" in val_index.index else np.nan

    # Anchor to the data's latest trade date (matches technical_indicators), not wall-clock.
    latest_trade_date = pd.to_datetime(features["trade_date"], errors="coerce").max()
    latest["snapshot_date"] = (
        latest_trade_date.date() if pd.notna(latest_trade_date) else pd.Timestamp(datetime.now()).date()
    )
    latest["strategy"] = "potential_v1"
    latest["pivot_price"] = latest["pivot"]
    latest["target_price"] = latest["close"] + (latest["pivot"] - latest["stop"]).clip(lower=0)
    latest["stop_price"] = latest["stop"]
    latest["rr_ratio"] = (latest["target_price"] - latest["close"]) / (latest["close"] - latest["stop_price"]).replace(0, np.nan)
    latest["time_stop_days"] = 60
    latest["hist_win_rate"] = win
    latest["hist_median_excess_40d"] = med
    latest["bias_note"] = "validated signal=rs_quiet; price-only; survivorship bias; fundamentals/themes neutral in v1"
    latest["scenario_json"] = latest.apply(
        lambda row: json.dumps(
            {
                "trigger": f"收盘突破 {row['pivot_price']:.2f} 且量能温和放大",
                "target": f"量度目标 {row['target_price']:.2f}",
                "stop": f"跌破箱体下沿 {row['stop_price']:.2f}",
                "time_stop": "60 个自然日未触发则移出观察",
                "bias_note": row["bias_note"],
            },
            ensure_ascii=False,
        ),
        axis=1,
    )
    cols = [
        "snapshot_date",
        "strategy",
        "market",
        "symbol",
        "name",
        "potential_score",
        "technical_setup_score",
        "relative_strength_score",
        "fundamental_turn_score",
        "theme_early_score",
        "pivot_price",
        "target_price",
        "stop_price",
        "rr_ratio",
        "time_stop_days",
        "hist_win_rate",
        "hist_median_excess_40d",
        "bias_note",
        "scenario_json",
    ]
    return latest.sort_values("potential_score", ascending=False)[cols].head(top).reset_index(drop=True)
