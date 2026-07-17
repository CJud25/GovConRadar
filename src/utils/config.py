"""
utils.config — Loads the YAML config files in config/ and exposes them as the
module-level constants the rest of the pipeline imports. Centralizing config in
YAML (not Python) keeps tuning data-driven and reviewable; this module is the
single typed adapter that turns that YAML back into the exact Python shapes
(dicts, sets, tuples) the code expects, and enforces invariants (weights sum to 1).
"""

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


_sources = _load("sources.yaml")
_keywords = _load("keywords.yaml")
_scoring = _load("scoring_weights.yaml")
_vendor = _load("vendor_profile_mock.yaml")
_ptw = _load("price_to_win.yaml")
_recompete = _load("recompete.yaml")
_burn = _load("burn_pressure.yaml")
_mods = _load("mods_signal.yaml")
_reasons = _load("reason_codes.yaml")
_hhi = _load("hhi_concentration.yaml")
_measurement = _load("measurement.yaml")
_displacement = _load("incumbent_displacement.yaml")
_opportunity_linking = _load("opportunity_linking.yaml")

# ─── SEARCH SCOPE / NAICS ─────────────────────────────────────────────────────
NAICS_SEED = {str(k): v for k, v in _sources["naics_seed"].items()}
NAICS_ALWAYS_RELEVANT = {str(c) for c in _sources["naics_always_relevant"]}
NAICS_CONDITIONAL = {str(c) for c in _sources["naics_conditional"]}

SEARCH_CONFIG = {
    "naics_codes": list(NAICS_SEED.keys()),
    "dod_toptier_agency": _sources["search"]["dod_toptier_agency"],
    "min_award_value": int(_sources["search"]["min_award_value"]),
    "lookback_years": int(_sources["search"]["lookback_years"]),
    "forward_windows_months": list(_sources["search"]["forward_windows_months"]),
    "max_search_pages": int(_sources["search"]["max_search_pages"]),
    "max_detail_hydrations": int(_sources["search"]["max_detail_hydrations"]),
}

DOD_COMPONENT_SUBAGENCIES = list(_sources["dod_component_subagencies"])
ENDPOINTS = dict(_sources.get("endpoints", {}))

# ─── SAM.GOV BULK EXPORT ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_sam_bulk = _sources.get("sam_bulk", {})
SAM_BULK_CSV = (
    (PROJECT_ROOT / _sam_bulk["contract_opportunities_csv"])
    if _sam_bulk.get("contract_opportunities_csv") else None
)
SAM_BULK_DOD_ONLY = bool(_sam_bulk.get("dod_only", True))

# ─── GSA PSC MANUAL (committed reference input) ───────────────────────────────
# Resolved against the project root; deliberately NO existence check at import
# time — the public clone must keep importing cleanly, and build_psc_lookup
# degrades to the curated-12-only lookup when the dir is absent or empty.
_psc_manual = _sources.get("psc_manual", {})
PSC_MANUAL_DIR: Path = PROJECT_ROOT / str(_psc_manual.get("dir", "data/reference/psc_manual"))

# ─── USASPENDING BULK EXPORT (awards) ─────────────────────────────────────────
_usa_bulk = _sources.get("usaspending_bulk", {})
# Accept either a list of globs (contracts_globs) or a single glob (contracts_glob).
_usa_globs = _usa_bulk.get("contracts_globs") or (
    [_usa_bulk["contracts_glob"]] if _usa_bulk.get("contracts_glob") else []
)


def _resolve_glob(g: str) -> str:
    """`~` expands to the user's home dir (so config need not hardcode a username);
    absolute globs are then used as-is, relative ones resolve against the project root."""
    g = str(Path(g).expanduser())
    return g if Path(g).is_absolute() else str(PROJECT_ROOT / g)


# List of resolved globs, or None when none configured (falsy → API fallback).
USASPENDING_BULK_GLOB = [_resolve_glob(g) for g in _usa_globs] or None

# ─── KEYWORD TAXONOMY ─────────────────────────────────────────────────────────
KEYWORD_TAXONOMY = {
    "cybersecurity": list(_keywords["cybersecurity"]),
    "it_services": list(_keywords["it_services"]),
}
DOD_FOCUS_TERMS = list(_keywords.get("dod_focus", []))

# ─── SCORING ──────────────────────────────────────────────────────────────────
SCORING_WEIGHTS = {k: float(v) for k, v in _scoring["weights"].items()}
assert abs(sum(SCORING_WEIGHTS.values()) - 1.0) < 1e-9, "SCORING_WEIGHTS must sum to 1.0"

PRIORITY_TIER_THRESHOLDS = [(int(t), str(label)) for t, label in _scoring["priority_tiers"]]

# ─── RECOMPETE EXPIRATION-BASIS POLICY ────────────────────────────────────────
# Which of a candidate's dates drives days_until_expiration / expiration_bucket.
# See config/recompete.yaml and transform.recompete.select_expiration_date.
EXPIRATION_BASIS_POLICIES = ("potential", "current", "earliest")
EXPIRATION_BASIS_POLICY = str(_recompete.get("expiration_basis_policy", "potential")).strip().lower()
assert EXPIRATION_BASIS_POLICY in EXPIRATION_BASIS_POLICIES, (
    f"expiration_basis_policy must be one of {EXPIRATION_BASIS_POLICIES}, got {EXPIRATION_BASIS_POLICY!r}"
)

# ─── OPPORTUNITY-LINK RECENCY GATE (asserted priors — see config/opportunity_linking.yaml) ───
# Consumed by transform.opportunity_linking: an establishing candidate->notice match is
# rejected when both dates are known and the notice's posted_date falls outside
# [expiration - before, expiration + after] — a recompete solicitation appears near/after
# the incumbent's expiry, never years before it. STRUCTURAL asserts only (positivity);
# the window VALUES are documented priors in the yaml.
_recency = _opportunity_linking["recency_window"]
OPPORTUNITY_LINKING = {
    "recency_months_before": int(_recency["max_months_before_expiry"]),
    "recency_months_after": int(_recency["max_months_after_expiry"]),
}
assert OPPORTUNITY_LINKING["recency_months_before"] >= 1, "linker: max_months_before_expiry >= 1"
assert OPPORTUNITY_LINKING["recency_months_after"] >= 0, "linker: max_months_after_expiry >= 0"

# ─── SUCCESSOR-VISIBLE PROXY (recompete follow-on label — see config/recompete.yaml) ───
# Injected whole into scoring.successor_proxy.annotate_successor_visible as a Mapping (the
# module re-validates on use). STRUCTURAL asserts only: min_cell_awards >= 2 keeps the
# "cell is readable" gate meaningful; signed_after_days >= 0 (a successor is signed AFTER expiry).
SUCCESSOR_PROXY = {
    "successor_min_cell_awards": int(_recompete["successor_min_cell_awards"]),
    "successor_signed_after_days": int(_recompete["successor_signed_after_days"]),
}
assert SUCCESSOR_PROXY["successor_min_cell_awards"] >= 2, "successor: successor_min_cell_awards >= 2"
assert SUCCESSOR_PROXY["successor_signed_after_days"] >= 0, "successor: successor_signed_after_days >= 0"

# ─── MEASUREMENT THRESHOLDS (label publication gates — config/measurement.yaml) ─
def validate_measurement(m: dict) -> dict:
    """Structural floors for the label-measurement gates (floors, not values — the
    numbers themselves are asserted priors documented in the YAML). Consumed by the
    trust-metrics stack as a plain Mapping; strict modules never import this module."""
    from datetime import date

    out = {
        "link_labels": {
            "target_per_tier": int(m["link_labels"]["target_per_tier"]),
            "min_labels_per_tier": int(m["link_labels"]["min_labels_per_tier"]),
            # G4 bulk-fill override (default False): must be an explicit bool, never coerced —
            # trust_metrics.link_precision_rows refuses a single-date/0-note/0-unsure tier without it.
            "allow_bulk_fill": m["link_labels"].get("allow_bulk_fill", False),
        },
        "outcome_labels": {
            "cohort_fy_start": int(m["outcome_labels"]["cohort_fy_start"]),
            "cohort_fy_end": int(m["outcome_labels"]["cohort_fy_end"]),
            "stratified_n": int(m["outcome_labels"]["stratified_n"]),
            "top_k": int(m["outcome_labels"]["top_k"]),
            "min_determinable_for_precision": int(m["outcome_labels"]["min_determinable_for_precision"]),
            # Candidate grain for the outcome cohort (default "order" keeps legacy behavior).
            "grain": str(m["outcome_labels"].get("grain", "order")).strip().lower(),
            "idv_attributes": str(m["outcome_labels"].get("idv_attributes", "data/reference/idv_attributes.csv")),
        },
        "lead_time": {"min_link_confidence": str(m["lead_time"]["min_link_confidence"])},
        "rank_stability": {
            "min_snapshots": int(m["rank_stability"]["min_snapshots"]),
            "top_k": int(m["rank_stability"]["top_k"]),
            "comparable_since": str(m["rank_stability"]["comparable_since"]),
        },
        "sampling_seed": m["sampling_seed"],
        "wilson_z": float(m["wilson_z"]),
    }
    assert out["link_labels"]["min_labels_per_tier"] >= 30, "measurement: min_labels_per_tier >= 30"
    assert isinstance(out["link_labels"]["allow_bulk_fill"], bool), \
        "measurement: allow_bulk_fill is a bool (the bulk-fill override is explicit, never coerced)"
    assert out["outcome_labels"]["top_k"] == 50, \
        "measurement: top_k == 50 (precision@10 is structurally impossible by design)"
    assert 40 <= out["outcome_labels"]["min_determinable_for_precision"] <= out["outcome_labels"]["top_k"], \
        "measurement: 40 <= min_determinable_for_precision <= top_k"
    assert out["outcome_labels"]["grain"] in ("vehicle", "order"), \
        "measurement: outcome_labels.grain in (vehicle, order)"
    assert out["rank_stability"]["min_snapshots"] >= 3, "measurement: min_snapshots >= 3"
    assert out["lead_time"]["min_link_confidence"] in ("High", "Medium"), \
        "measurement: min_link_confidence in (High, Medium)"
    assert isinstance(out["sampling_seed"], int) and not isinstance(out["sampling_seed"], bool), \
        "measurement: sampling_seed is an int (changing it = a new sample = relabel)"
    assert 1.0 <= out["wilson_z"] <= 3.0, "measurement: 1.0 <= wilson_z <= 3.0"
    assert date.fromisoformat(out["rank_stability"]["comparable_since"]), \
        "measurement: comparable_since parses as a date"
    return out


MEASUREMENT = validate_measurement(_measurement)

# ─── SYNTHETIC MOCK VENDOR PROFILE ────────────────────────────────────────────
VENDOR_PROFILE_SYNTHETIC = dict(_vendor)

# ─── COMPETITIVE PRICE RANGE (price-to-win) ───────────────────────────────────
# Passed whole to scoring.price_to_win.attach_ptw. The app's live recompute
# (streamlit_app/components/price_to_win.py) imports this same object — one config,
# no inlined mirror (collapsed 2026-07-07; the deploy repo ships config/ too).
PRICE_TO_WIN = dict(_ptw)
assert PRICE_TO_WIN["comparable_selection"]["min_comparables"] >= 2, \
    "PTW min_comparables must be >= 2 (a percentile needs at least a few points)"

# ─── BURN-PRESSURE SIGNAL (asserted priors — see config/burn_pressure.yaml) ────
BURN_PRESSURE = {
    "hot_threshold": float(_burn["hot_threshold"]),
    "cold_threshold": float(_burn["cold_threshold"]),
    "fully_funded_ratio": float(_burn["fully_funded_ratio"]),
    "ceiling_exceeded_ratio": float(_burn["ceiling_exceeded_ratio"]),
    "min_planned_days": int(_burn["min_planned_days"]),
    "max_planned_days": int(_burn["max_planned_days"]),
    "idv_award_types": [str(x).strip().upper() for x in _burn["idv_award_types"]],
}
assert BURN_PRESSURE["cold_threshold"] < 0.0 < BURN_PRESSURE["hot_threshold"], \
    "burn: cold_threshold < 0 < hot_threshold"
assert 0.0 < BURN_PRESSURE["fully_funded_ratio"] <= BURN_PRESSURE["ceiling_exceeded_ratio"], \
    "burn: 0 < fully_funded_ratio <= ceiling_exceeded_ratio"
assert 1 <= BURN_PRESSURE["min_planned_days"] <= BURN_PRESSURE["max_planned_days"], \
    "burn: 1 <= min_planned_days <= max_planned_days"

# ─── MODS / TERMINATION SIGNAL (asserted priors — see config/mods_signal.yaml) ─
# Passed whole to scoring.mods_signal.load_mods_config, which enforces the stronger
# cross-checks against the fold's module constants at pipeline time — utils.config must
# NOT import scoring modules (only the reverse edge exists), so only STRUCTURAL sanity
# (sign / ordering / positivity) is asserted here.
MODS_SIGNAL = {
    "termination_codes": [str(x).strip().upper() for x in _mods["termination_codes"]],
    "complete_grace_days": int(_mods["complete_grace_days"]),
    "min_transactions": int(_mods["min_transactions"]),
    "ceiling_balloon_ratio": float(_mods["ceiling_balloon_ratio"]),
    "deobligation_floor_usd": float(_mods["deobligation_floor_usd"]),
    "velocity_low": float(_mods["velocity_low"]),
    "velocity_high": float(_mods["velocity_high"]),
    "bridge_min_extension_days": int(_mods["bridge_min_extension_days"]),
    "bridge_noncompeted_codes": [str(x).strip().upper() for x in _mods["bridge_noncompeted_codes"]],
}
assert MODS_SIGNAL["termination_codes"], "mods: termination_codes must be non-empty"
assert MODS_SIGNAL["velocity_low"] < MODS_SIGNAL["velocity_high"], \
    "mods: velocity_low < velocity_high"
assert MODS_SIGNAL["ceiling_balloon_ratio"] > 1.0, "mods: ceiling_balloon_ratio > 1.0"
assert MODS_SIGNAL["deobligation_floor_usd"] < 0, "mods: deobligation_floor_usd < 0"
assert MODS_SIGNAL["complete_grace_days"] > 0, "mods: complete_grace_days > 0"
assert MODS_SIGNAL["min_transactions"] >= 1, "mods: min_transactions >= 1"

# ─── REASON-CODES EXPLAINABILITY LAYER (asserted priors — see config/reason_codes.yaml) ───
# Range/ordering/template-completeness validation lives ONCE in scoring.reason_codes.load_reason_config
# (the reusable validator the adapter builds at import and the tests hammer with corrupted configs).
# config.py keeps only the fast structural assert on the shipped yaml: the priority projection must
# name every signal exactly once, uniquely — so cfg.priority[code] can never KeyError at render time.
_rt = _reasons["thresholds"]
REASON_CODES = {
    "priority": {str(k): int(v) for k, v in _reasons["priority"].items()},
    "capability_strong_min": float(_rt["capability_strong_min"]),
    "capability_partial_min": float(_rt["capability_partial_min"]),
    "urgency_near_days": int(_rt["urgency_near_days"]),
    "urgency_soon_days": int(_rt["urgency_soon_days"]),
    "offers_sentinels": [int(x) for x in _rt["offers_sentinels"]],
    "offers_max_plausible": int(_rt["offers_max_plausible"]),
    "data_quality_neutral": float(_rt["data_quality_neutral"]),
    "max_chips_detail": int(_rt["max_chips_detail"]),
    "max_chips_explorer": int(_rt["max_chips_explorer"]),
    "templates": {str(k): str(v) for k, v in _reasons["templates"].items()},
}
_REASON_SIGNALS = {
    "data_gap_title",
    "data_gap_end_date",
    "data_gap_stale",
    "incumbent_lock",
    "set_aside",
    "urgency",
    "expired_grace",
    "capability",
    "value",
    "agency",
    "location",
    "recompete",
    "ptw",
    "idv_task_order",
    "data_quality",
    "data_gap_code_prefix",
    "data_gap_short_title",
    "displacement",
    "empty_state",
}  # 19 signals (burn chip CUT per Corrections v2 C2.1; competition CUT per Spec 2 §12; displacement lane F1)
# STRUCTURAL asserts only — the single source of truth for the priority projection.
assert set(REASON_CODES["priority"]) == _REASON_SIGNALS, "reason_codes: priority must name every signal exactly once"
assert len(set(REASON_CODES["priority"].values())) == len(REASON_CODES["priority"]), (
    "reason_codes: priority ranks must be unique"
)

# ─── INCUMBENT-DISPLACEMENT LANE (asserted priors — see config/incumbent_displacement.yaml) ───
# Passed whole to scoring.incumbent_displacement.load_displacement_config (the comprehensive
# validator). The offers junk-count guards are threaded VERBATIM from reason_codes.yaml's
# thresholds — one yaml home for them, so the lane's sole-offer read and the incumbent_lock
# chip can never disagree on what a junk offer count is. STRUCTURAL asserts only.
INCUMBENT_DISPLACEMENT = {
    "min_signals_read": int(_displacement["min_signals_read"]),
    "offers_sentinels": [int(x) for x in _rt["offers_sentinels"]],
    "offers_max_plausible": int(_rt["offers_max_plausible"]),
}
assert 1 <= INCUMBENT_DISPLACEMENT["min_signals_read"] <= 6, "displacement: 1 <= min_signals_read <= 6"
assert INCUMBENT_DISPLACEMENT["offers_max_plausible"] >= 1, "displacement: offers_max_plausible >= 1"

# ─── INCUMBENT CONCENTRATION (descriptive) — asserted priors, see config/hhi_concentration.yaml ───
# Corrections v2 (Option A): a top-incumbent obligated-dollar-SHARE read — NO HHI number, NO
# DOJ/FTC bands (both dropped), so there are no band-cutoff priors. Injected whole into
# scoring.market_concentration.compute_hhi_concentration as a Mapping. The moderate<high assert
# is intentionally ABSENT (those keys no longer exist — asserting them would KeyError).
HHI_CONCENTRATION_CONFIG = {
    "min_market_ueis": int(_hhi["hhi_concentration"]["min_market_ueis"]),
    "max_unknown_uei_share": float(_hhi["hhi_concentration"]["max_unknown_uei_share"]),
}
assert HHI_CONCENTRATION_CONFIG["min_market_ueis"] >= 2, "hhi: min_market_ueis >= 2"
assert 0.0 <= HHI_CONCENTRATION_CONFIG["max_unknown_uei_share"] <= 1.0, "hhi: 0 <= max_unknown_uei_share <= 1"
