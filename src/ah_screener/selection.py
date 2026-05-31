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
_FALLBACK_DEDUP_CATEGORIES = {"商品ETF", "债券ETF", "货币ETF", "宽基指数ETF", "行业ETF"}


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


def _security_key(market: object, symbol: object) -> tuple[str, str]:
    return str(market or "").upper(), str(symbol or "").zfill(6)


def _component_key(symbol: object, name: object) -> str:
    raw_symbol = str(symbol or "").strip().upper()
    raw_name = str(name or "").strip().upper()
    if raw_symbol and raw_symbol not in {"NAN", "NONE", "<NA>"}:
        return raw_symbol
    return raw_name


def _latest_exposure_order(frame: pd.DataFrame) -> pd.Series:
    if "report_date" in frame.columns:
        parsed = pd.to_datetime(frame["report_date"], errors="coerce").fillna(
            pd.Timestamp("1900-01-01")
        )
        return parsed.map(pd.Timestamp.toordinal)
    if "report_period" in frame.columns:
        period = frame["report_period"].astype(str)
        extracted = period.str.extract(r"(?P<year>\d{4}).*?(?P<quarter>[1-4])\s*季")
        year = pd.to_numeric(extracted["year"], errors="coerce").fillna(0)
        quarter = pd.to_numeric(extracted["quarter"], errors="coerce").fillna(0)
        return year * 10 + quarter
    return pd.Series(0, index=frame.index)


def _weight_vectors(
    df: pd.DataFrame | None,
    *,
    key_column: str,
    name_column: str,
    max_items: int = 15,
) -> tuple[dict[tuple[str, str], dict[str, float]], dict[tuple[str, str], str], dict[tuple[str, str], float]]:
    if df is None or df.empty:
        return {}, {}, {}
    required = {"market", "symbol", key_column, "weight_pct"}
    if not required.issubset(df.columns):
        return {}, {}, {}
    frame = df.copy()
    frame["weight_num"] = pd.to_numeric(frame["weight_pct"], errors="coerce").fillna(0.0)
    frame = frame[frame["weight_num"] > 0]
    if frame.empty:
        return {}, {}, {}
    frame["security_key"] = list(zip(frame["market"].astype(str).str.upper(), frame["symbol"].astype(str)))
    frame["period_order"] = _latest_exposure_order(frame)
    latest = frame.groupby("security_key")["period_order"].transform("max")
    frame = frame[frame["period_order"].eq(latest)]
    name_series = frame[name_column] if name_column in frame.columns else pd.Series("", index=frame.index)
    frame["component_key"] = [
        _component_key(component, name)
        for component, name in zip(frame[key_column], name_series, strict=False)
    ]
    frame = frame[frame["component_key"].astype(str).str.len() > 0]

    vectors: dict[tuple[str, str], dict[str, float]] = {}
    labels: dict[tuple[str, str], str] = {}
    coverage: dict[tuple[str, str], float] = {}
    for raw_key, group in frame.groupby("security_key", dropna=False):
        market, symbol = raw_key
        key = _security_key(market, symbol)
        top = group.sort_values("weight_num", ascending=False).head(max_items)
        vectors[key] = dict(zip(top["component_key"], top["weight_num"].astype(float)))
        coverage[key] = round(float(top["weight_num"].sum()), 2)
        label_items: list[str] = []
        for _, item in top.head(5).iterrows():
            label = str(item.get(name_column) or item.get(key_column) or item["component_key"]).strip()
            label_items.append(f"{label} {float(item['weight_num']):.1f}%")
        labels[key] = " | ".join(label_items)
    return vectors, labels, coverage


def _weighted_overlap(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    shared = set(a).intersection(b)
    if not shared:
        return 0.0
    numerator = sum(min(a[item], b[item]) for item in shared)
    denominator = min(sum(a.values()), sum(b.values()))
    if denominator <= 0:
        return 0.0
    return float(numerator / denominator)


def _fallback_group(row: pd.Series) -> str:
    category = str(row.get("etf_category") or "")
    track = str(row.get("etf_track") or "")
    if track in _UNCLASSIFIED_TRACKS or track == category:
        return f"{category}:{track}:{row.get('symbol')}"
    return f"{category}:{track}"


def _can_rule_fallback_merge(row: pd.Series, leader: pd.Series) -> bool:
    category = str(row.get("etf_category") or "")
    if category in _FALLBACK_DEDUP_CATEGORIES:
        return True
    if category == "跨境ETF":
        return "LOF" not in str(row.get("name") or "").upper() and "LOF" not in str(
            leader.get("name") or ""
        ).upper()
    return False


def dedup_etf_pool_by_exposure(
    pool: pd.DataFrame,
    *,
    holdings: pd.DataFrame | None = None,
    allocations: pd.DataFrame | None = None,
    technicals: pd.DataFrame | None = None,
    top: int = 20,
    category: str | None = None,
    similarity_threshold: float = 0.72,
) -> pd.DataFrame:
    """De-duplicate ETFs by type plus disclosed underlying distribution.

    Priority:
      1. Same type/family and similar top-holding weights.
      2. Same type/family and similar industry/allocation weights.
      3. Conservative rule fallback for passive/commodity/cash/bond/industry tools.

    Active cross-border LOFs without exposure data stay separate instead of being
    collapsed solely by name keywords.
    """
    if pool is None or pool.empty:
        return pd.DataFrame()

    enriched = enrich_etf_snapshot(pool, technicals=technicals)
    if category:
        enriched = enriched[enriched["etf_category"].eq(category)].copy()
    if enriched.empty:
        return enriched

    holding_vectors, holding_labels, holding_coverage = _weight_vectors(
        holdings,
        key_column="component_symbol",
        name_column="component_name",
    )
    allocation_vectors, allocation_labels, allocation_coverage = _weight_vectors(
        allocations,
        key_column="allocation_name",
        name_column="allocation_name",
        max_items=8,
    )

    enriched = enriched.copy()
    enriched["amount_num"] = pd.to_numeric(enriched.get("amount"), errors="coerce").fillna(0)
    enriched["score_num"] = pd.to_numeric(enriched.get("etf_score"), errors="coerce").fillna(0)
    enriched["_security_key"] = [
        _security_key(market, symbol)
        for market, symbol in zip(enriched["market"], enriched["symbol"], strict=False)
    ]
    enriched["etf_top_holdings"] = enriched["_security_key"].map(holding_labels).fillna("")
    enriched["etf_holding_coverage_pct"] = (
        enriched["_security_key"].map(holding_coverage).fillna(0.0)
    )
    enriched["etf_primary_allocation"] = enriched["_security_key"].map(allocation_labels).fillna("")
    enriched["etf_allocation_coverage_pct"] = (
        enriched["_security_key"].map(allocation_coverage).fillna(0.0)
    )
    enriched["_fallback_group"] = enriched.apply(_fallback_group, axis=1)

    ranked = enriched.sort_values(["score_num", "amount_num"], ascending=[False, False]).copy()
    group_ids: dict[int, str] = {}
    group_basis: dict[int, str] = {}
    leaders: list[tuple[str, pd.Series]] = []
    next_group = 1

    for idx, row in ranked.iterrows():
        row_key = row["_security_key"]
        row_holding = holding_vectors.get(row_key, {})
        row_allocation = allocation_vectors.get(row_key, {})
        assigned_group: str | None = None
        assigned_basis = "rule_fallback"
        for group_id, leader in leaders:
            if row["_fallback_group"] != leader["_fallback_group"]:
                continue
            leader_key = leader["_security_key"]
            leader_holding = holding_vectors.get(leader_key, {})
            leader_allocation = allocation_vectors.get(leader_key, {})
            if row_holding and leader_holding:
                if _weighted_overlap(row_holding, leader_holding) >= similarity_threshold:
                    assigned_group = group_id
                    assigned_basis = "holding_overlap"
                    break
                continue
            if row_allocation and leader_allocation:
                if _weighted_overlap(row_allocation, leader_allocation) >= similarity_threshold:
                    assigned_group = group_id
                    assigned_basis = "allocation_overlap"
                    break
                continue
            if _can_rule_fallback_merge(row, leader):
                assigned_group = group_id
                assigned_basis = "rule_fallback"
                break

        if assigned_group is None:
            assigned_group = f"exposure_{next_group}"
            assigned_basis = (
                "holding_seed"
                if row_holding
                else "allocation_seed"
                if row_allocation
                else "rule_seed"
            )
            leaders.append((assigned_group, row))
            next_group += 1
        group_ids[idx] = assigned_group
        group_basis[idx] = assigned_basis

    grouped = ranked.assign(
        etf_exposure_group=pd.Series(group_ids),
        etf_dedup_basis=pd.Series(group_basis),
    )
    out = consolidate_etf_candidates(grouped, top=top, group_col="etf_exposure_group")
    if out.empty:
        return out
    out["selection_note"] = out.apply(
        lambda row: (
            f"{row.get('etf_exposure_group')} 按底层分布/类型去重，"
            f"同组{int(row.get('peer_count') or 1)}只，依据 {row.get('etf_dedup_basis')}"
        ),
        axis=1,
    )
    return out.drop(columns=["_security_key", "_fallback_group"], errors="ignore")


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
