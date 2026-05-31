"""Centralized, documented model parameters for the expert scoring model.

Why this module exists (P2-3): the composite weights, decision thresholds, master
proxies and risk penalties used to be magic numbers scattered across
``expert_model.py`` and ``scoring.py`` with no single place to inspect, document or
tune them.

PROVENANCE — read before changing anything:
    Every number here is a HAND-SET PRIOR derived from investing-framework intuition.
    None of it has been calibrated against forward returns. Treat these as priors,
    NOT as edges. Any "this weighting works" claim must come from out-of-sample
    validation (``ah-screener expert-validate`` / potential walk-forward), never from
    the fact that a number lives here.

Changing a value here is a model change. The composition is locked by
``tests/test_scoring_weights.py`` — update that characterization test deliberately
when you intend a behavioural change.
"""

from __future__ import annotations


# --- Final expert_score composition (sums to 1.0; penalty subtracted separately) ---
# Prior: China-master frameworks carry the most weight, then generic master proxies
# and standalone fundamentals; liquidity/peer are light tie-breakers.
EXPERT_COMPOSITE: dict[str, float] = {
    "master_score": 0.20,
    "china_master_score": 0.28,
    "fundamental_score": 0.18,
    "industry_fit_score": 0.10,
    "technical_score": 0.14,
    "liquidity_score": 0.04,
    "peer_score": 0.06,
}


# --- Decision cut points on expert_score (hand-set buckets) ---
DECISION: dict[str, float] = {
    "reject_penalty": 80.0,  # penalty at/above this -> reject regardless of score
    "reject_below": 42.0,  # expert_score below this -> reject
    "core_min": 68.0,  # core_candidate needs score >= this ...
    "core_technical_min": 55.0,  # ... and technical_score >= this
    "watchlist_min": 56.0,  # watchlist needs score >= this; else reserve
}


# --- Generic master proxies: each is a 0-100 blend of base sub-scores ---
MASTER_PROXY: dict[str, dict[str, float]] = {
    "graham": {"valuation": 0.75, "defensive_theme": 0.25},
    "buffett": {"liquidity": 0.40, "cap": 0.35, "risk_inverse": 0.25},
    "fisher": {"fundamental": 0.45, "technical": 0.35, "liquidity": 0.20},
    "lynch": {"fundamental": 0.35, "valuation": 0.35, "technical": 0.20, "liquidity": 0.10},
    "oneil": {"technical": 0.78, "liquidity": 0.22},
}
GRAHAM_DEFENSIVE_HIT = 100.0  # graham defensive component when a defensive theme matches
GRAHAM_DEFENSIVE_MISS = 45.0  # ... and when it does not
MASTER_COMPOSITE: dict[str, float] = {
    "graham": 0.18,
    "buffett": 0.22,
    "fisher": 0.22,
    "lynch": 0.18,
    "oneil": 0.20,
}


# --- China-master proxies (same 0-100 base-score blend idea) ---
CHINA_MASTER_PROXY: dict[str, dict[str, float]] = {
    "zhang_lei_long_term": {"fundamental": 0.52, "liquidity": 0.15, "risk_inverse": 0.33},
    "qiu_guolu_quality_value": {"valuation": 0.36, "fundamental": 0.36, "risk_inverse": 0.28},
    "dan_bin_lin_yuan_compounder": {"fundamental": 0.65, "risk_inverse": 0.35},
    "deng_xiaofeng_cycle_quality": {
        "fundamental": 0.36,
        "valuation": 0.26,
        "technical": 0.23,
        "risk_inverse": 0.15,
    },
    "chen_guangming_balanced": {
        "fundamental": 0.42,
        "technical": 0.24,
        "liquidity": 0.20,
        "risk_inverse": 0.14,
    },
}
CHINA_MASTER_COMPOSITE: dict[str, float] = {
    "zhang_lei_long_term": 0.24,
    "qiu_guolu_quality_value": 0.22,
    "dan_bin_lin_yuan_compounder": 0.18,
    "deng_xiaofeng_cycle_quality": 0.16,
    "chen_guangming_balanced": 0.20,
}


# --- Industry-relative peer score blend ---
PEER_SCORE: dict[str, float] = {
    "fundamental": 0.45,
    "valuation": 0.25,
    "technical": 0.20,
    "liquidity": 0.10,
}


# --- Theme score (context-only; never feeds expert_score) ---
THEME_SCORE: dict[str, float] = {
    "base_no_match": 28.0,
    "base_match": 38.0,
    "per_theme": 18.0,
    "top_n": 4.0,
}


# --- Default base scores when an input table has no row for a security ---
DEFAULT_TECHNICAL_SCORE = 42.0
DEFAULT_FUNDAMENTAL_SCORE = 50.0


# =============================================================================
# US screener (src/us_screener) — independent model parameters.
# Same PROVENANCE caveat as above: hand-set priors from US-market practice, NOT
# return-calibrated edges. Kept here so the US blend is inspectable / tunable in
# one place rather than buried as magic numbers in ``scoring_us.py``.
# =============================================================================

# Final US expert_score composition (sums to 1.0). Prior: fundamentals + momentum
# (heat/RS) lead; valuation/liquidity are peer-relative tie-breakers; macro is a
# light transmission tilt. Theme/short are context-only and deliberately excluded.
US_EXPERT_COMPOSITE: dict[str, float] = {
    "fundamental": 0.24,
    "technical": 0.20,
    "valuation": 0.14,
    "liquidity": 0.14,
    "heat": 0.18,
    "macro": 0.10,
}

# Momentum factor = blend of absolute heat (RVOL/return/52w) and relative strength
# (excess return vs market). Leadership shows up in RS first.
US_HEAT_RS_BLEND: dict[str, float] = {"heat": 0.65, "rs": 0.35}

# Peer-relative valuation blend (lower multiple scores higher), ranked within sector.
US_VALUATION_WEIGHTS: dict[str, float] = {"pe": 0.45, "pb": 0.25, "peg": 0.30}

# china_master_score proxy for the US schema row (reporting/compat only; not the
# primary US decision input, which is US_EXPERT_COMPOSITE).
US_CHINA_MASTER_PROXY: dict[str, float] = {
    "fundamental": 0.55,
    "valuation": 0.20,
    "technical": 0.15,
    "macro": 0.10,
}

# Decision cut points on the US expert_score.
US_DECISION: dict[str, float] = {
    "core_min": 70.0,  # core_candidate needs score >= this ...
    "core_technical_min": 55.0,  # ... and technical_score >= this
    "watchlist_min": 60.0,  # watchlist needs score >= this ...
    "reserve_min": 50.0,  # ... reserve needs score >= this; else reject
}

# Theme score (context-only bucket; never feeds US expert_score).
US_THEME_SCORE: dict[str, float] = {
    "base_no_match": 35.0,
    "base_match": 45.0,
    "per_theme": 8.0,
    "cap": 80.0,
}

# US tradeability risk penalties (additive; subtracted from 100). china_concept is a
# hard exclude (full penalty); the rest are price/liquidity floors guarding delisting
# and untradeable names.
US_RISK_PENALTY: dict[str, float] = {
    "china_concept": 100.0,
    "price_missing": 55.0,
    "us_penny": 45.0,
    "amount_missing": 60.0,
    "low_amount": 35.0,
    "low_market_cap": 25.0,
}


# --- Risk penalties (additive). Centralizes scoring._risk_penalty + expert gate. ---
# Prior: hard red flags (ST/退市/退市生命周期命中) dominate; missing data is treated as
# uncertainty rather than neutrality (P2-2); price/penny floors guard delisting risk.
RISK_PENALTY: dict[str, float] = {
    # name / lifecycle hard flags
    "a_st_name": 100.0,
    "delisted_lifecycle": 100.0,
    "name_distress": 45.0,
    # liquidity / price floors
    "amount_missing": 60.0,
    "amount_below_floor": 35.0,
    "price_missing": 50.0,
    "hk_penny": 30.0,
    "us_penny": 30.0,
    # technical overheating (追高)
    "rsi_hot": 8.0,
    "return_20d_hot": 8.0,
    # missing-data uncertainty discount (P2-2): missing data is uncertainty, not
    # neutrality. A name with no technicals/fundamentals must rank below a name with
    # real-but-mediocre data, so the discount is meaningful and compounds when both
    # are absent (the security is effectively unevaluable).
    "missing_technical": 12.0,
    "missing_fundamental": 14.0,
    "missing_both_extra": 8.0,
}

# Thresholds used alongside the penalties above.
RSI_HOT = 78.0
RETURN_20D_HOT = 0.45
HK_PENNY_PRICE = 0.5  # HKD; below this a name is effectively a 仙股
US_PENNY_PRICE = 1.0  # USD; sustained sub-$1 triggers exchange delisting review
NAME_DISTRESS_MARKERS: tuple[str, ...] = (
    "清盘",
    "除牌",
    "退市",
    "停牌",
    "破产",
    "重整",
    "delist",
    "liquidat",
    "bankrupt",
)
