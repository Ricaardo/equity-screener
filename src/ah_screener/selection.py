"""Selection / de-duplication service layer.

Thin seam between scoring (``etf_model`` etc.) and presentation (``reporting``,
Streamlit UI). Reporting and the dashboard must call these functions rather than
re-implementing de-dup logic, so the later pipeline refactor (master-plan stage 8)
can move internals without touching consumers. See docs/master-plan.md R14.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ah_screener.etf_model import consolidate_etf_candidates, enrich_etf_snapshot

# A track that merely echoes its category (or the explicit "未识别"/"其他ETF"
# fallbacks) means classification failed — such ETFs must stay distinct rather
# than collapsing into a single bogus group.
_UNCLASSIFIED_TRACKS = ("其他ETF", "未识别")


def etf_category_overview(pool: pd.DataFrame) -> pd.DataFrame:
    """Full-pool size by category (table ① of the two-table layout, decision D1)."""
    if pool is None or pool.empty:
        return pd.DataFrame(columns=["分类", "数量"])
    enriched = enrich_etf_snapshot(pool)
    if enriched.empty:
        return pd.DataFrame(columns=["分类", "数量"])
    return (
        enriched.groupby("etf_category")
        .size()
        .rename("数量")
        .reset_index()
        .rename(columns={"etf_category": "分类"})
        .sort_values("数量", ascending=False)
    )


def dedup_etf_pool(
    pool: pd.DataFrame, technicals: pd.DataFrame | None = None, top: int = 20
) -> pd.DataFrame:
    """Double-layer de-duplicated ETF leaders (table ② of the two-table layout).

    Folding granularity (conservative, see R16):
      - tracks mapped to a correlation cluster fold to that cluster
        (e.g. 沪深300/上证50/中证A500 → 大盘宽基);
      - identified-but-unclustered tracks fold to their own track
        (e.g. multiple 军工ETF from different houses → 1);
      - unclassified ETFs (track echoes category) stay distinct.
    Keeps the best candidate per group and surfaces ``peer_count`` /
    ``peer_alternatives`` so the homogeneity is both folded and traceable.
    ``technicals`` supplies the real technical score (Stage 2); neutral when None.
    """
    if pool is None or pool.empty:
        return pd.DataFrame()
    enriched = enrich_etf_snapshot(pool, technicals=technicals)
    if enriched.empty:
        return enriched

    track = enriched["etf_track"].astype(str)
    category = enriched["etf_category"].astype(str)
    cluster = enriched["etf_cluster"].astype(str)
    unclassified = track.eq(category) | track.isin(_UNCLASSIFIED_TRACKS)
    enriched = enriched.assign(
        etf_dedup_group=cluster.where(~unclassified, cluster + "#" + enriched["symbol"].astype(str))
    )
    return consolidate_etf_candidates(enriched, top=top, group_col="etf_dedup_group")


def validate_etf_clusters(
    pool: pd.DataFrame,
    prices: pd.DataFrame,
    min_corr: float = 0.9,
    min_overlap: int = 60,
) -> pd.DataFrame:
    """Empirically validate the manual cluster table against return correlations (stage 9, D2).

    Picks the most liquid ETF per track as its representative, builds a daily-return
    matrix, and reports track pairs where the empirical correlation disagrees with the
    manual grouping:
      - ``weak_fold``  — same cluster but corr < ``min_corr`` (folded too aggressively);
      - ``merge_candidate`` — different clusters but corr >= ``min_corr`` (could be folded).
    Returns one row per evaluated track pair (only the flagged ``relation`` rows).
    """
    if pool is None or pool.empty or prices is None or prices.empty:
        return pd.DataFrame()
    enriched = enrich_etf_snapshot(pool)
    if enriched.empty:
        return pd.DataFrame()
    enriched = enriched.assign(
        amount_num=pd.to_numeric(enriched.get("amount"), errors="coerce").fillna(0)
    )
    # One representative (most liquid) ETF per track.
    reps = (
        enriched.sort_values("amount_num", ascending=False)
        .drop_duplicates("etf_track", keep="first")
        .loc[:, ["market", "symbol", "etf_track", "etf_cluster"]]
    )
    px = prices.copy()
    px["trade_date"] = pd.to_datetime(px["trade_date"], errors="coerce")
    px = px[px["symbol"].isin(set(reps["symbol"]))]
    if px.empty:
        return pd.DataFrame()
    wide = (
        px.sort_values("trade_date")
        .drop_duplicates(["symbol", "trade_date"], keep="last")
        .pivot(index="trade_date", columns="symbol", values="close")
        .astype(float)
    )
    returns = wide.pct_change().dropna(how="all")
    sym_to_track = dict(zip(reps["symbol"], reps["etf_track"]))
    sym_to_cluster = dict(zip(reps["symbol"], reps["etf_cluster"]))
    symbols = [s for s in returns.columns if s in sym_to_track]
    rows: list[dict[str, object]] = []
    for i, sym_a in enumerate(symbols):
        for sym_b in symbols[i + 1 :]:
            pair = returns[[sym_a, sym_b]].dropna()
            if len(pair) < min_overlap:
                continue
            corr = float(pair[sym_a].corr(pair[sym_b]))
            if np.isnan(corr):
                continue
            same_cluster = sym_to_cluster[sym_a] == sym_to_cluster[sym_b]
            relation = None
            if same_cluster and corr < min_corr:
                relation = "weak_fold"
            elif not same_cluster and corr >= min_corr:
                relation = "merge_candidate"
            if relation is None:
                continue
            rows.append(
                {
                    "track_a": sym_to_track[sym_a],
                    "track_b": sym_to_track[sym_b],
                    "cluster_a": sym_to_cluster[sym_a],
                    "cluster_b": sym_to_cluster[sym_b],
                    "corr": round(corr, 3),
                    "overlap_days": int(len(pair)),
                    "relation": relation,
                }
            )
    return pd.DataFrame(rows).sort_values("corr", ascending=False) if rows else pd.DataFrame(
        columns=["track_a", "track_b", "cluster_a", "cluster_b", "corr", "overlap_days", "relation"]
    )
