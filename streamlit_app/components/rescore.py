"""
rescore.py — live re-scoring for the app: score every candidate against a user's OWN
company profile (not just the shipped demo profile). The eight component scorers +
priority_tier are imported from the ONE library, src/scoring/pursuit_score.py (this pilot
repo is a full checkout — app.py puts src/ on the path). There is no inlined twin to
hand-sync anymore; what remains here is app-only glue — profile-weighted assembly and the
UI breakdown rows. Every score is an ESTIMATE. The scorer's version history and the honesty
overhaul are documented at the source, src/scoring/pursuit_score.py (SCORER_VERSION 2.0.0).
"""

import pandas as pd

# The ONE scorer library + the ONE quality module (single source of truth) — imported
# once. The app keeps its _-prefixed names (used internally + referenced by the parity
# test) as aliases below.
from scoring import pursuit_score
from scoring import quality_flags as quality
from scoring.pursuit_score import (
    agency_fit_score,
    capability_match_score,
    data_quality_score,
    location_fit_score,
    priority_tier,
    recompete_confidence_score,
    set_aside_fit_score,
    urgency_score,
    value_score,
)
from utils.config import SCORING_WEIGHTS

SCORER_VERSION = pursuit_score.SCORER_VERSION
_capability_match = capability_match_score
_urgency = urgency_score
_value = value_score
_agency_fit = agency_fit_score
_set_aside_fit = set_aside_fit_score
_recompete_confidence = recompete_confidence_score
_location_fit = location_fit_score
_data_quality = data_quality_score
_tier = priority_tier

# Weights MUST sum to 1.0 (mirrors config/scoring_weights.yaml).
_WEIGHTS_REFERENCE = {
    "capability_match": 0.25,
    "expiration_urgency": 0.20,
    "estimated_value": 0.15,
    "agency_fit": 0.10,
    "set_aside_fit": 0.10,
    "recompete_confidence": 0.10,
    "location_fit": 0.05,
    "data_quality": 0.05,
}
# Single source of truth: the live re-score MUST use the same vector the baked pipeline used.
WEIGHTS = dict(SCORING_WEIGHTS)
assert WEIGHTS == _WEIGHTS_REFERENCE, (
    "rescore.WEIGHTS diverged from config/scoring_weights.yaml — re-sync the reference literal "
    f"or the YAML: {WEIGHTS} vs {_WEIGHTS_REFERENCE}")

# Which components move with the company profile (55%) vs. are intrinsic to the
# contract (45%). Used for honest "what your profile drove" messaging.
PROFILE_DRIVEN = {"capability_match", "estimated_value", "agency_fit", "location_fit"}

# The shipped synthetic demo profile (from config/vendor_profile_mock.yaml).
DEMO_PROFILE = {
    "company_name": "Meridian Cyber Solutions (demo)",
    "capabilities": [
        "cybersecurity compliance",
        "CMMC/NIST support",
        "RMF support",
        "help desk",
        "network operations",
        "cloud migration",
        "Power BI/data analytics",
        "software automation",
        "IT program management",
    ],
    "preferred_naics": ["541512", "541519", "541611"],
    "preferred_psc": ["D307", "D399", "DA01"],
    "agencies_with_past_performance": ["Department of the Army", "Defense Health Agency"],
    "max_comfortable_contract_value": 25_000_000,
    "states_served": ["VA", "MD", "DC", "TX", "CO", "AL", "GA"],
    "nationwide": False,
    "is_demo": True,
    # Self-attested certifications (eligibility lane). The demo profile NEVER
    # fabricates gate states — no certs, no 8(a) exit date, no size self-cert.
    "certs": [],
    "exit_8a": "",
    "sb_small_naics": False,
}
BLANK_PROFILE = {
    "company_name": "",
    "capabilities": [],
    "preferred_naics": [],
    "preferred_psc": [],
    "agencies_with_past_performance": [],
    "max_comfortable_contract_value": 25_000_000,
    "states_served": [],
    "nationwide": False,
    "is_demo": False,
    "certs": [],
    "exit_8a": "",
    "sb_small_naics": False,
}


# The eight component scorers + priority_tier are imported above from the ONE library
# (scoring.pursuit_score) as _urgency/_value/_capability_match/... — no inlined copies.
# What remains below is app-only glue: profile-weighted assembly for the live UI.


def score_components(row, p) -> dict:
    """Raw 0–100 component scores for one candidate row (dict-like)."""
    flag_count = len(quality.quality_flags(row.get("contract_title"), row.get("days_until_expiration")))
    return {
        "capability_match": _capability_match(row.get("naics"), row.get("psc"), row.get("contract_title"), p),
        "expiration_urgency": _urgency(row.get("days_until_expiration")),
        "estimated_value": _value(row.get("total_obligated_amount"), p),
        "agency_fit": _agency_fit(row.get("subagency"), p),
        "set_aside_fit": _set_aside_fit(row.get("extent_competed_code"), row.get("type_of_set_aside_code")),
        "recompete_confidence": _recompete_confidence(
            row.get("expiration_date_basis"), row.get("classification_confidence")
        ),
        "location_fit": _location_fit(row.get("place_of_performance_state"), p),
        "data_quality": _data_quality(row.get("data_quality_notes"), flag_count),
    }


def score_one(row, p) -> tuple:
    comps = score_components(row, p)
    total = round(sum(comps[k] * WEIGHTS[k] for k in comps), 1)
    tier = _tier(total)
    # Data Gap override: stale / missing-end-date / garbled rows are quarantined out
    # of the Tier 1-4 pipeline regardless of score (they cannot be a real lead).
    if quality.is_quarantined(row.get("contract_title"), row.get("days_until_expiration")):
        tier = "Data Gap"
    return total, tier, comps


def score_candidates(df: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """Return a copy of df with pursuit_score/priority_tier recomputed for `profile`.
    Fast (rule-based arithmetic over ~5k rows); wrap the caller in st.cache_data."""
    if df.empty:
        return df
    out = df.copy()
    scored = out.apply(lambda r: score_one(r, profile), axis=1)
    out["pursuit_score"] = [s[0] for s in scored]
    out["priority_tier"] = [s[1] for s in scored]
    return out


def breakdown_rows(row, profile: dict) -> pd.DataFrame:
    """Long-format weighted breakdown for one candidate (for Contract Detail),
    matching the fact_scoring_breakdown shape (score_component, weighted_score)."""
    comps = score_components(row, profile)
    labels = {
        "capability_match": "Capability match",
        "expiration_urgency": "Expiration urgency",
        "estimated_value": "Estimated value",
        "agency_fit": "Past-performance fit",
        "set_aside_fit": "Set-aside / competition fit",
        "recompete_confidence": "Recompete confidence",
        "location_fit": "Location fit",
        "data_quality": "Data quality",
    }
    return pd.DataFrame(
        [
            {
                "score_component": labels[k],
                "raw_score": comps[k],
                "weighted_score": round(comps[k] * WEIGHTS[k], 1),
                "profile_driven": k in PROFILE_DRIVEN,
            }
            for k in comps
        ]
    )


def tier_counts(df: pd.DataFrame) -> dict:
    if df.empty or "priority_tier" not in df:
        return {}
    return df["priority_tier"].value_counts().to_dict()
