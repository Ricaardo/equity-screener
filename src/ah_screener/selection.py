"""Selection / de-duplication service layer.

Thin seam between scoring (``etf_model`` etc.) and presentation (``reporting``,
Streamlit UI). Reporting and the dashboard must call these functions rather than
re-implementing de-dup logic, so the later pipeline refactor (master-plan stage 8)
can move internals without touching consumers. See docs/master-plan.md R14.
"""

from __future__ import annotations

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
