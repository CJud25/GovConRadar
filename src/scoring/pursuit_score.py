"""
scoring.py — 8-component weighted Pursuit Score (0-100) against the synthetic
mock vendor profile, plus priority tiering. Every score here is an ESTIMATE.

SCORER_VERSION 2.0.0 — Honesty overhaul. Must stay in lockstep with the app port
streamlit_app/components/rescore.py (tests/test_rescore.py asserts parity):
  * urgency_score: replaced the v1 expired-cliff (days<=0 -> 100) with a graduated
    curve (active linear decay; -90..-1 grace; stale/NaN -> 0).
  * data_quality_score: empty notes now neutral 70 (not 100); -20 per note, -15 per
    quality flag, floor 20.
  * score_candidate: Data Gap override for stale / missing-end-date / garbled rows.
See src/scoring/quality_flags.py (the one quality module; its app mirror was collapsed).
"""

import pandas as pd

from scoring.quality_flags import is_quarantined, quality_flags
from utils.config import PRIORITY_TIER_THRESHOLDS, SCORING_WEIGHTS, VENDOR_PROFILE_SYNTHETIC

SCORER_VERSION = "2.0.0"


def _safe_str(value) -> str:
    """Returns '' for None/NaN instead of the literal string 'nan' -- a bare
    `str(value or "")` treats NaN as truthy and would produce the substring
    "nan", corrupting keyword/capability matching."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def capability_match_score(naics, psc, contract_title, vendor=VENDOR_PROFILE_SYNTHETIC) -> float:
    score = 0
    # Coerce codes to strings before comparing: a CSV round-trip can hand back
    # naics as int64 (e.g. 541512), which would never `== "541512"`. Mirrors the
    # app port (rescore._capability_match) so the two scorers stay in parity for
    # any input dtype.
    if _safe_str(naics).split(".")[0] in {str(x) for x in vendor["preferred_naics"]}:
        score += 40
    if _safe_str(psc) in {str(x) for x in vendor["preferred_psc"]}:
        score += 30
    title_lower = _safe_str(contract_title).lower()
    capability_hits = sum(1 for cap in vendor["capabilities"] if cap.lower() in title_lower)
    score += min(capability_hits * 15, 30)
    return min(score, 100)


def urgency_score(days_until_expiration) -> float:
    """Graduated expiration urgency (v2.0.0). active (>=0): linear decay, day 0 ~=100;
    grace (-90..-1): high-but-decaying verify-urgency (95 -> 60); stale (<-90)/NaN: 0."""
    days = days_until_expiration
    if pd.isna(days):
        return 0
    if days >= 730:
        return 10
    if days >= 0:
        return round(100 - (days / 730 * 90), 1)
    if days >= -90:
        return round(95 - ((-days) - 1) * (35 / 89), 1)
    return 0


def value_score(total_obligated_amount, vendor=VENDOR_PROFILE_SYNTHETIC) -> float:
    if not total_obligated_amount or pd.isna(total_obligated_amount):
        return 0
    ceiling = vendor["max_comfortable_contract_value"]
    if not ceiling:  # unknown comfortable value (firm with no positive-value awards)
        return 50    # neutral — never a fabricated ceiling, never a divide-by-zero (T16)
    ratio = total_obligated_amount / ceiling
    if ratio < 0.1:
        return 30
    if ratio <= 1.0:
        return 100
    if ratio <= 3.0:
        return 60
    return 25


def _vendor_agencies(vendor) -> list:
    """Agencies with past performance, accepting the canonical key
    (`agencies_with_past_performance`, used by real UEI profiles and the app's
    DEMO_PROFILE) OR the legacy synthetic-mock key (`..._synthetic`). Keeps the mock
    and a real profile both working, and keeps the two scorers in parity."""
    return (
        vendor.get("agencies_with_past_performance")
        or vendor.get("agencies_with_past_performance_synthetic")
        or []
    )


def agency_fit_score(subagency, vendor=VENDOR_PROFILE_SYNTHETIC) -> float:
    # Match on the DoD **component** (subagency: "DEPARTMENT OF THE ARMY", …), NOT the
    # top-tier `agency` — the whole pipeline is DoD (agency code 097), so `agency` is a
    # constant and comparing against it made this component silently return 50 for every
    # candidate. The component arrives uppercased by normalize_agency_name; the synthetic
    # profile lists components in title case, so compare case-insensitively.
    component = _safe_str(subagency).strip().upper()
    past_performance = {a.strip().upper() for a in _vendor_agencies(vendor)}
    if component and component in past_performance:
        return 100
    return 50  # DoD-wide baseline — every candidate is already DoD by pipeline scope


# FPDS extent_competed_code values that mean the work was actually competed:
# A=full&open, D=full&open after exclusion of sources, F=competed under SAP.
_COMPETITIVE_EXTENT_CODES = {"A", "D", "F"}


def set_aside_fit_score(extent_competed_code=None, type_of_set_aside_code=None) -> float:
    # Was a documented v1 gap: the old code tested extent_competed against ("A","B")
    # but the field carries descriptive TEXT ("FULL AND OPEN COMPETITION"), so this
    # component silently returned the baseline for every candidate. Now driven by the
    # recovered FPDS codes. A genuine small-business/socioeconomic set-aside is the
    # strongest competition-posture proxy public data offers (restricted pool);
    # full-and-open competition is a weaker positive.
    set_aside = _safe_str(type_of_set_aside_code).strip().upper()
    if set_aside and set_aside != "NONE":
        return 70
    if _safe_str(extent_competed_code).strip().upper() in _COMPETITIVE_EXTENT_CODES:
        return 55
    return 50


def recompete_confidence_score(expiration_date_basis, classification_confidence) -> float:
    # "terminated" (the mods ghost-fix basis) scores 0 EXPLICITLY: a terminated contract is
    # not a confident forward recompete. Same value the .get default already gave — named so
    # the vocabulary is complete, not relied on as a fallthrough. Parity-safe: the app
    # imports this one function (Option D), there is no twin to sync.
    basis_score = {"potential_end_date": 100, "current_end_date": 75, "unknown": 0, "terminated": 0}.get(expiration_date_basis, 0)
    confidence_score = {"High": 100, "Medium": 65, "Low": 35}.get(classification_confidence, 0)
    return round((basis_score + confidence_score) / 2, 1)


def location_fit_score(place_of_performance_state, vendor=VENDOR_PROFILE_SYNTHETIC) -> float:
    # A `nationwide` profile fits anywhere; otherwise match the PoP state against the vendor's
    # states (coerced, so a CSV int/None never spuriously misses). Superset that serves both
    # the pipeline (synthetic profile: no `nationwide`) and the app's live/blank profiles.
    if vendor.get("nationwide"):
        return 100
    if _safe_str(place_of_performance_state) in {str(x) for x in vendor["states_served"]}:
        return 100
    return 30


def data_quality_score(data_quality_notes, flag_count: int = 0) -> float:
    """Neutral 70 for unknown (empty notes); -20 per recorded note; -15 per quality
    flag; floor 20. (v1 returned 100 for empty — the fake-100.0-KPI bug.)"""
    if pd.isna(data_quality_notes) or not data_quality_notes:
        base = 70
    else:
        issue_count = len([n for n in str(data_quality_notes).split(";") if n.strip()])
        base = 70 - issue_count * 20
    return max(base - flag_count * 15, 20)


def priority_tier(pursuit_score: float) -> str:
    for threshold, label in PRIORITY_TIER_THRESHOLDS:
        if pursuit_score >= threshold:
            return label
    return "Tier 4: Low Priority"


_WEIGHT_KEY_MAP = {
    "capability_match_score": "capability_match", "urgency_score": "expiration_urgency",
    "value_score": "estimated_value", "agency_fit_score": "agency_fit",
    "set_aside_fit_score": "set_aside_fit", "recompete_confidence_score": "recompete_confidence",
    "location_fit_score": "location_fit", "data_quality_score": "data_quality",
}


def score_candidate(candidate: dict, vendor=None) -> dict:
    # `vendor=None` resolves to the synthetic mock (not a mutable default arg), so
    # score_candidate(candidate) is byte-identical to before this parameter existed
    # (regression guard, AC-8). Pass a real UEI profile (firm_profile.VendorProfile from
    # build_profile_from_awards) to score against a firm's own history. Only the four
    # vendor-aware components move with the profile.
    vendor = VENDOR_PROFILE_SYNTHETIC if vendor is None else vendor
    title = candidate.get("contract_title")
    days = candidate.get("days_until_expiration")
    flag_count = len(quality_flags(title, days))
    components = {
        "capability_match_score": capability_match_score(candidate.get("naics"), candidate.get("psc"), title, vendor),
        "urgency_score": urgency_score(days),
        "value_score": value_score(candidate.get("total_obligated_amount"), vendor),
        "agency_fit_score": agency_fit_score(candidate.get("subagency"), vendor),
        "set_aside_fit_score": set_aside_fit_score(candidate.get("extent_competed_code"), candidate.get("type_of_set_aside_code")),
        "recompete_confidence_score": recompete_confidence_score(candidate.get("expiration_date_basis"), candidate.get("classification_confidence")),
        "location_fit_score": location_fit_score(candidate.get("place_of_performance_state"), vendor),
        "data_quality_score": data_quality_score(candidate.get("data_quality_notes"), flag_count),
    }
    pursuit_score = round(sum(components[c] * SCORING_WEIGHTS[_WEIGHT_KEY_MAP[c]] for c in components), 1)
    # Data Gap override: stale / missing-end-date / garbled rows are quarantined out
    # of the Tier 1-4 pipeline regardless of score.
    tier = "Data Gap" if is_quarantined(title, days) else priority_tier(pursuit_score)
    return {**components, "pursuit_score": pursuit_score, "priority_tier": tier}
