"""Forward-return validation for the expert model's decision buckets (P2-4).

The expert model's decision buckets (core_candidate / watchlist / reserve / reject)
used to have NO forward-return check — only the potential scanner did. This mirrors
that out-of-sample discipline: it joins historical expert-decision snapshots to
forward prices and reports, per decision, the forward excess-return distribution
(vs the same-date universe median) plus whether median excess is monotonic in the
decision ordering.

Honest-evidence caveats (same as backtest / potential):
  - Current-listed universe only -> survivorship bias remains.
  - With only a couple of natural snapshots there is no edge to claim; the function
    reports ``sample_count`` so a thin result is visible rather than dressed up.
  - Forward return is the label; it never feeds the decision (no look-ahead).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DECISION_ORDER = ("core_candidate", "watchlist", "reserve", "reject")
FORWARD_DAYS = 40

EXPERT_VALIDATION_COLUMNS = [
    "decision",
    "sample_count",
    "win_rate",
    "median_excess",
    "p25_excess",
    "p75_excess",
    "mean_expert_score",
]


def _forward_returns(prices: pd.DataFrame, forward_days: int) -> pd.DataFrame:
    """Per (market, symbol, trade_date) forward return over ``forward_days`` rows."""
    rows: list[pd.DataFrame] = []
    for (market, symbol), group in prices.groupby(["market", "symbol"]):
        g = group.sort_values("trade_date").dropna(subset=["close"]).copy()
        if len(g) <= forward_days:
            continue
        close = pd.to_numeric(g["close"], errors="coerce")
        rows.append(
            pd.DataFrame(
                {
                    "market": market,
                    "symbol": symbol,
                    "trade_date": pd.to_datetime(g["trade_date"], errors="coerce"),
                    "forward_return": close.shift(-forward_days) / close - 1,
                }
            )
        )
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _attach_forward_returns(expert: pd.DataFrame, forward: pd.DataFrame) -> pd.DataFrame:
    """Vectorized as-of join anchoring the forward return at the snapshot's own date.

    The forward return is taken from the trade_date closest to (and at/before) the
    snapshot — i.e. the decision's *own* forward outcome. NaN tail rows are kept on
    purpose: a snapshot without ``forward_days`` of subsequent prices yields NaN and
    is dropped downstream, so a too-recent snapshot honestly produces no sample
    rather than borrowing a stale earlier window.
    """
    # merge_asof rejects null keys on either side; _forward_returns coerces dates so
    # a malformed trade_date would surface as NaT — drop those before the join.
    left = expert.dropna(subset=["snapshot_date"]).sort_values("snapshot_date")
    right = forward.dropna(subset=["trade_date"]).sort_values("trade_date")
    if left.empty or right.empty:
        return left.assign(forward_return=np.nan)
    merged = pd.merge_asof(
        left,
        right[["market", "symbol", "trade_date", "forward_return"]],
        left_on="snapshot_date",
        right_on="trade_date",
        by=["market", "symbol"],
        direction="backward",
        tolerance=pd.Timedelta(days=7),
    )
    return merged


def validate_expert_decisions(
    expert: pd.DataFrame, prices: pd.DataFrame, forward_days: int = FORWARD_DAYS
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Forward excess-return stats per decision bucket + a monotonicity verdict.

    Returns ``(stats_df, summary)`` where ``summary`` carries the sample count, a
    monotonicity flag over DECISION_ORDER and a bias note. Empty/insufficient inputs
    yield an empty frame and an explanatory summary rather than a misleading number.
    """
    note = (
        "current-listed universe only; survivorship bias remains; forward return is the "
        "label, never an input; thin samples are not an edge claim"
    )
    if expert is None or expert.empty or prices is None or prices.empty:
        return pd.DataFrame(columns=EXPERT_VALIDATION_COLUMNS), {
            "sample_count": 0,
            "monotonic": False,
            "bias_note": note,
        }

    forward = _forward_returns(prices, forward_days)
    if forward.empty:
        return pd.DataFrame(columns=EXPERT_VALIDATION_COLUMNS), {
            "sample_count": 0,
            "monotonic": False,
            "bias_note": note,
        }

    df = expert[["snapshot_date", "market", "symbol", "decision", "expert_score"]].copy()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], errors="coerce")
    df["market"] = df["market"].astype(str)
    df["symbol"] = df["symbol"].astype(str)
    forward = forward.copy()
    forward["market"] = forward["market"].astype(str)
    forward["symbol"] = forward["symbol"].astype(str)
    df = _attach_forward_returns(df, forward)
    df = df.dropna(subset=["forward_return"])
    if df.empty:
        return pd.DataFrame(columns=EXPERT_VALIDATION_COLUMNS), {
            "sample_count": 0,
            "monotonic": False,
            "bias_note": note,
        }

    # Excess vs the same-snapshot-date universe median (cross-section neutral).
    df["universe_median"] = df.groupby("snapshot_date")["forward_return"].transform("median")
    df["excess"] = df["forward_return"] - df["universe_median"]

    stats_rows: list[dict[str, object]] = []
    for decision in DECISION_ORDER:
        bucket = df[df["decision"] == decision]
        excess = bucket["excess"].dropna()
        if excess.empty:
            continue
        stats_rows.append(
            {
                "decision": decision,
                "sample_count": int(len(excess)),
                "win_rate": float((excess > 0).mean() * 100),
                "median_excess": float(excess.median()),
                "p25_excess": float(excess.quantile(0.25)),
                "p75_excess": float(excess.quantile(0.75)),
                "mean_expert_score": float(
                    pd.to_numeric(bucket["expert_score"], errors="coerce").mean()
                ),
            }
        )

    stats = pd.DataFrame(stats_rows, columns=EXPERT_VALIDATION_COLUMNS)
    medians = [
        row["median_excess"]
        for decision in DECISION_ORDER
        for row in stats_rows
        if row["decision"] == decision
    ]
    monotonic = bool(len(medians) >= 2 and all(np.diff(medians) <= 0))
    return stats, {
        "sample_count": int(len(df)),
        "snapshot_count": int(df["snapshot_date"].nunique()),
        "monotonic": monotonic,
        "bias_note": note,
    }
