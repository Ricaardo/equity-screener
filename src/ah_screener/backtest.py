"""Backtest engine for refined candidate snapshots.

Extracted from pipeline.py (master-plan stage 8) to shrink the orchestration
module. Behaviour is unchanged; pipeline re-exports the public entry points for
backward compatibility.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from ah_screener.expert_model import STRATEGY_NAME, refine_candidates
from ah_screener.sources.akshare_client import parse_benchmark
from ah_screener.storage import Store

RebalanceMode = Literal["snapshot", "monthly", "quarterly"]

BACKTEST_COLUMNS = [
    "period_start",
    "period_end",
    "signal_date",
    "holdings",
    "gross_return",
    "turnover",
    "cost_rate",
    "period_return",
    "equity",
    "benchmark",
    "benchmark_return",
    "benchmark_equity",
    "excess_return",
    "excess_equity",
    "holding_symbols",
]


def get_store() -> Store:
    # Delegate to pipeline so monkeypatching pipeline.get_store (tests) is honoured
    # and there is a single store accessor. Lazy import avoids the cycle (pipeline
    # imports this module at top) regardless of which module is imported first.
    from ah_screener import pipeline

    return pipeline.get_store()


def _empty_backtest_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BACKTEST_COLUMNS)


def _rebalance_points(
    signal_dates: list[pd.Timestamp],
    final_price_date: pd.Timestamp,
    mode: RebalanceMode,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if not signal_dates:
        return []
    if mode == "snapshot":
        starts = signal_dates
    else:
        frequency = "MS" if mode == "monthly" else "QS"
        calendar_starts = [
            pd.Timestamp(value)
            for value in pd.date_range(signal_dates[0], final_price_date, freq=frequency)
            if pd.Timestamp(value) > signal_dates[0]
        ]
        starts = sorted(set([signal_dates[0], *calendar_starts]))

    points: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for start in starts:
        eligible = [date for date in signal_dates if date <= start]
        if eligible:
            points.append((start, eligible[-1]))
    return points


def _select_backtest_picks(
    picks: pd.DataFrame,
    max_names: int,
    industry_neutral: bool,
    max_per_group: int,
) -> pd.DataFrame:
    if picks.empty:
        return picks
    picks = picks.copy()
    for column, default in [
        ("expert_score", 0.0),
        ("peer_score", 50.0),
        ("industry_fit_score", 50.0),
        ("fundamental_score", 50.0),
        ("technical_score", 50.0),
        ("industry_peer_group", "未分类"),
    ]:
        if column not in picks.columns:
            picks[column] = default
    picks = picks.sort_values(
        ["expert_score", "industry_fit_score", "peer_score", "fundamental_score", "technical_score"],
        ascending=False,
    )
    if not industry_neutral:
        return picks.head(max_names)

    selected: list[int] = []
    group_counts: dict[str, int] = {}
    for idx, row in picks.iterrows():
        group = str(row.get("industry_peer_group") or row.get("bucket") or "未分类")
        if group_counts.get(group, 0) >= max_per_group:
            continue
        selected.append(idx)
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(selected) >= max_names:
            break
    return picks.loc[selected]


def _benchmark_frame(prices: pd.DataFrame, benchmark: str) -> pd.DataFrame:
    market, symbol = parse_benchmark(benchmark)
    frame = prices[
        (prices["market"].astype(str).str.upper() == market) & (prices["symbol"].astype(str) == symbol)
    ].copy()
    if frame.empty:
        return frame
    adj = frame.get("adj_type", pd.Series("", index=frame.index)).astype(str).str.lower()
    source = frame.get("source", pd.Series("", index=frame.index)).astype(str).str.lower()
    benchmark_rows = frame[adj.eq("benchmark") | source.str.contains("index", na=False)]
    return benchmark_rows if not benchmark_rows.empty else frame


def _period_price_return(
    prices: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> float | None:
    history = prices[
        (prices["trade_date"] >= start_date) & (prices["trade_date"] <= end_date)
    ].sort_values("trade_date")
    if len(history) < 2:
        return None
    start_close = pd.to_numeric(history["close"].iloc[0], errors="coerce")
    end_close = pd.to_numeric(history["close"].iloc[-1], errors="coerce")
    if pd.notna(start_close) and pd.notna(end_close) and float(start_close) > 0:
        return float(end_close) / float(start_close) - 1
    return None


def _historical_signal_dates(
    prices: pd.DataFrame,
    rebalance: RebalanceMode,
    min_snapshots: int,
) -> list[pd.Timestamp]:
    dates = pd.to_datetime(prices["trade_date"], errors="coerce").dropna().sort_values().unique()
    if len(dates) < 2:
        return []
    start = pd.Timestamp(dates[0])
    end = pd.Timestamp(dates[-1])
    if rebalance == "snapshot":
        candidates = [pd.Timestamp(date) for date in dates[:: max(len(dates) // max(min_snapshots, 1), 1)]]
    else:
        frequency = "MS" if rebalance == "monthly" else "QS"
        candidates = [pd.Timestamp(value) for value in pd.date_range(start, end, freq=frequency)]
    trading_dates = [pd.Timestamp(date) for date in dates]
    selected: list[pd.Timestamp] = []
    for candidate in candidates:
        eligible = [date for date in trading_dates if date <= candidate]
        if eligible:
            selected.append(eligible[-1])
    selected = sorted(set(selected))
    if selected and selected[-1] == end:
        selected = selected[:-1]
    return selected[-min_snapshots:]


def _trailing_return_score(
    prices: pd.DataFrame,
    market: str,
    symbol: str,
    signal_date: pd.Timestamp,
    lookback_days: int = 90,
) -> float:
    start_date = signal_date - pd.Timedelta(days=lookback_days)
    history = prices[
        (prices["market"].astype(str) == market)
        & (prices["symbol"].astype(str) == symbol)
        & (prices["trade_date"] >= start_date)
        & (prices["trade_date"] <= signal_date)
    ].sort_values("trade_date")
    if len(history) < 2:
        return 50.0
    start_close = pd.to_numeric(history["close"].iloc[0], errors="coerce")
    end_close = pd.to_numeric(history["close"].iloc[-1], errors="coerce")
    if pd.isna(start_close) or pd.isna(end_close) or float(start_close) <= 0:
        return 50.0
    trailing_return = float(end_close) / float(start_close) - 1
    return float(max(0, min(100, 50 + trailing_return * 180)))


def _trailing_return_scores(
    prices: pd.DataFrame,
    signal_date: pd.Timestamp,
    lookback_days: int = 90,
) -> dict[tuple[str, str], float]:
    start_date = signal_date - pd.Timedelta(days=lookback_days)
    window = prices[
        (prices["trade_date"] >= start_date)
        & (prices["trade_date"] <= signal_date)
    ].sort_values(["market", "symbol", "trade_date"])
    if window.empty:
        return {}
    scores: dict[tuple[str, str], float] = {}
    for (market, symbol), group in window.groupby(["market", "symbol"], sort=False):
        if len(group) < 2:
            continue
        start_close = pd.to_numeric(group["close"].iloc[0], errors="coerce")
        end_close = pd.to_numeric(group["close"].iloc[-1], errors="coerce")
        if pd.isna(start_close) or pd.isna(end_close) or float(start_close) <= 0:
            continue
        trailing_return = float(end_close) / float(start_close) - 1
        scores[(str(market), str(symbol))] = float(max(0, min(100, 50 + trailing_return * 180)))
    return scores


def backfill_refined_candidate_snapshots(
    min_snapshots: int = 6,
    rebalance: RebalanceMode = "quarterly",
    max_per_bucket: int = 3,
    max_per_style: int = 2,
) -> int:
    store = get_store()
    store.init_db()
    existing = store.query_df(
        """
        SELECT DISTINCT snapshot_date
        FROM refined_candidates
        WHERE strategy = ?
        ORDER BY snapshot_date
        """,
        [STRATEGY_NAME],
    )
    existing_dates = (
        set(pd.to_datetime(existing["snapshot_date"]).dt.normalize())
        if not existing.empty
        else set()
    )
    if len(existing_dates) >= min_snapshots:
        return 0

    expert = store.query_df(
        """
        SELECT *
        FROM expert_screening_results
        WHERE strategy = ?
        """,
        [STRATEGY_NAME],
    )
    prices = store.query_df("SELECT * FROM daily_prices")
    if expert.empty or prices.empty:
        return 0
    expert["snapshot_date"] = pd.to_datetime(expert["snapshot_date"])
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices["symbol"] = prices["symbol"].astype(str)
    latest_signal = expert["snapshot_date"].max()
    template = expert[expert["snapshot_date"] == latest_signal].copy()
    if template.empty:
        return 0

    target_count = max(min_snapshots - len(existing_dates), 0)
    signal_dates = _historical_signal_dates(prices, rebalance, min_snapshots + 2)
    signal_dates = [
        date.normalize()
        for date in signal_dates
        if date.normalize() not in existing_dates and date.normalize() < latest_signal.normalize()
    ][-target_count:]
    inserted = 0
    for signal_date in signal_dates:
        replay = template.copy()
        replay["snapshot_date"] = signal_date
        trailing_scores = _trailing_return_scores(prices, signal_date)
        replay["technical_score"] = replay.apply(
            lambda row: trailing_scores.get((str(row["market"]), str(row["symbol"])), 50.0),
            axis=1,
        )
        replay["expert_score"] = (
            pd.to_numeric(replay["expert_score"], errors="coerce").fillna(50) * 0.78
            + pd.to_numeric(replay["technical_score"], errors="coerce").fillna(50) * 0.16
            + pd.to_numeric(
                replay.get("liquidity_score", pd.Series(50, index=replay.index)),
                errors="coerce",
            ).fillna(50)
            * 0.06
        ).clip(0, 100)
        replay["reasons"] = replay["reasons"].astype(str) + f"; historical_replay_signal={signal_date.date()}"
        replay["snapshot_source"] = "historical_replay"
        replay["is_replay"] = True
        refined = refine_candidates(
            replay,
            max_per_bucket=max_per_bucket,
            max_per_style=max_per_style,
        )
        if refined.empty:
            continue
        store.execute(
            "DELETE FROM refined_candidates WHERE snapshot_date = ? AND strategy = ?",
            [pd.Timestamp(signal_date).date(), STRATEGY_NAME],
        )
        inserted += store.upsert_dataframe("refined_candidates", refined)
    return inserted


def backtest_refined_candidates(
    initial_capital: float = 1_000_000,
    max_names: int = 12,
    rebalance: RebalanceMode = "snapshot",
    fee_bps: float = 5.0,
    slippage_bps: float = 10.0,
    industry_neutral: bool = False,
    max_per_group: int = 2,
    benchmark: str | None = None,
    include_replay: bool = True,
) -> pd.DataFrame:
    store = get_store()
    refined = store.query_df(
        """
        SELECT *
        FROM refined_candidates
        WHERE strategy = ?
        """,
        [STRATEGY_NAME],
    )
    prices = store.query_df("SELECT * FROM daily_prices")
    if not include_replay and not refined.empty:
        if "is_replay" in refined.columns:
            is_replay = (
                refined["is_replay"]
                .fillna(False)
                .astype(str)
                .str.lower()
                .isin(["true", "1", "t", "yes"])
            )
            refined = refined[~is_replay].copy()
        if "snapshot_source" in refined.columns:
            source = refined["snapshot_source"].fillna("natural").astype(str)
            refined = refined[~source.str.contains("replay", case=False, na=False)].copy()
    if refined.empty or prices.empty:
        return _empty_backtest_frame()

    refined["snapshot_date"] = pd.to_datetime(refined["snapshot_date"])
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices["symbol"] = prices["symbol"].astype(str)
    dates = sorted(refined["snapshot_date"].dropna().unique())
    final_price_date = prices["trade_date"].max()
    if not dates or final_price_date <= dates[0]:
        return _empty_backtest_frame()

    rows: list[dict[str, object]] = []
    equity = float(initial_capital)
    benchmark_equity = float(initial_capital)
    previous_weights: dict[tuple[str, str], float] = {}
    cost_bps = max(fee_bps, 0) + max(slippage_bps, 0)
    points = _rebalance_points([pd.Timestamp(date) for date in dates], final_price_date, rebalance)
    benchmark_prices = _benchmark_frame(prices, benchmark) if benchmark else pd.DataFrame()
    for index, (start_date, signal_date) in enumerate(points):
        end_date = points[index + 1][0] if index + 1 < len(points) else final_price_date
        if end_date <= start_date:
            continue
        picks = _select_backtest_picks(
            refined[refined["snapshot_date"] == signal_date],
            max_names=max_names,
            industry_neutral=industry_neutral,
            max_per_group=max_per_group,
        )
        holding_returns: dict[tuple[str, str], float] = {}
        for _, pick in picks.iterrows():
            key = (str(pick["market"]), str(pick["symbol"]))
            history = prices[
                (prices["market"] == key[0])
                & (prices["symbol"] == key[1])
                & (prices["trade_date"] >= start_date)
                & (prices["trade_date"] <= end_date)
            ].sort_values("trade_date")
            if len(history) < 2:
                continue
            start_close = pd.to_numeric(history["close"].iloc[0], errors="coerce")
            end_close = pd.to_numeric(history["close"].iloc[-1], errors="coerce")
            if pd.notna(start_close) and pd.notna(end_close) and float(start_close) > 0:
                holding_returns[key] = float(end_close) / float(start_close) - 1
        if not holding_returns:
            continue
        current_weights = {key: 1 / len(holding_returns) for key in holding_returns}
        traded_notional = sum(
            abs(current_weights.get(key, 0.0) - previous_weights.get(key, 0.0))
            for key in set(current_weights) | set(previous_weights)
        )
        gross_return = float(
            sum(current_weights[key] * holding_returns[key] for key in holding_returns)
        )
        cost_rate = traded_notional * cost_bps / 10_000
        period_return = gross_return - cost_rate
        equity *= 1 + period_return
        benchmark_return = None
        if benchmark and not benchmark_prices.empty:
            benchmark_return = _period_price_return(benchmark_prices, start_date, end_date)
            if benchmark_return is not None:
                benchmark_equity *= 1 + benchmark_return
        benchmark_equity_value = benchmark_equity if benchmark_return is not None else None
        excess_return = period_return - benchmark_return if benchmark_return is not None else None
        excess_equity = equity - benchmark_equity if benchmark_return is not None else None
        rows.append(
            {
                "period_start": pd.Timestamp(start_date).date(),
                "period_end": pd.Timestamp(end_date).date(),
                "signal_date": pd.Timestamp(signal_date).date(),
                "holdings": len(holding_returns),
                "gross_return": gross_return,
                "turnover": traded_notional,
                "cost_rate": cost_rate,
                "period_return": period_return,
                "equity": equity,
                "benchmark": benchmark,
                "benchmark_return": benchmark_return,
                "benchmark_equity": benchmark_equity_value,
                "excess_return": excess_return,
                "excess_equity": excess_equity,
                "holding_symbols": ",".join(f"{market}:{symbol}" for market, symbol in current_weights),
            }
        )
        previous_weights = current_weights
    return pd.DataFrame(rows)
