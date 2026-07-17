"""
evidence — the ONE door into the brief renderer (pure, strict).

``EVIDENCE_CONTRACT`` enumerates, table by table, every column the capture brief is allowed
to see. ``gather_evidence`` copies ONLY contract-listed keys into a frozen ``BriefEvidence``
— anything else (the raw ``contract_title`` record dump, ``description_raw``,
``classification_reason``, every ``*_demo`` / ``*_key`` helper) is unreachable by the
renderer, by construction. The PII policy holds here structurally, not by review.

Pure: no I/O, no clock (``today`` is injected), no config. Imports limited to stdlib +
strict first-party (scoring.reason_codes, scoring.eligibility_lane).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from scoring.eligibility_lane import LaneVerdict
from scoring.reason_codes import ReasonChip

# Asserted prior: matches Phase 4's disclosed top-50 sample; ~<1 MB baked.
BRIEF_TOP_N = 50

# The exact enumeration — the renderer can see NOTHING outside it.
EVIDENCE_CONTRACT: Mapping[str, tuple[str, ...]] = {
    "fact_recompete_candidates": (
        "candidate_id",
        "piid",
        "title_display",
        "subagency",
        "incumbent_vendor",
        "incumbent_uei",
        "naics",
        "psc",
        "award_type",
        "extent_competed",
        "extent_competed_code",
        "type_of_set_aside",
        "type_of_set_aside_code",
        "number_of_offers_received",
        "pop_start_date",
        "current_end_date",
        "potential_end_date",
        "selected_expiration_date",
        "expiration_date_basis",
        "days_until_expiration",
        "candidate_status",
        "capture_phase",
        "estimated_recompete_window_start",
        "estimated_recompete_window_end",
        "total_obligated_amount",
        "burn_basis",
        "ceiling_burn_ratio",
        "burn_pressure",
        "burn_band",
        "terminated",
        "termination_kind",
        "termination_code",
        "termination_action_date",
        "bridge_flag",
        "ceiling_balloon_flag",
        "ceiling_growth_ratio",
        "mods_basis",
        "successor_visible_basis",
        "displacement_signal_count",
        "displacement_signals_read",
        "displacement_signals",
        "displacement_unread",
        "displacement_band",
        "displacement_basis",
        "ptw_low",
        "ptw_market_median",
        "ptw_high",
        "ptw_data_strength",
        "ptw_n_comparables",
        "ptw_match_tier",
        "ptw_basis",
        "ptw_incumbent_runrate",
        "pursuit_score",
        "priority_tier",
        "source_url",
        "data_quality_notes",
    ),
    "dim_vendor": ("incumbent_uei", "size_standard_shift", "size_standard_basis"),
    "dim_agency": (
        "subagency",
        "number_of_contracts",
        "total_cyber_it_obligations",
        "expiring_contract_count_12_months",
        "expiring_pipeline_value",
        "average_award_size",
        # F4 — the per-component market-concentration join (scoring.market_concentration
        # .annotate_agency_concentration): double-gated Unknown rides through; missing
        # columns on an older bundle simply never reach the renderer.
        "concentration_top_share",
        "concentration_n_ueis",
        "concentration_basis",
        "concentration_reason",
    ),
    "fact_opportunity_notices": (
        "notice_id",
        "notice_type",
        "solicitation_number",
        "title",
        "posted_date",
        "response_deadline",
        "set_aside",
        "set_aside_code",
        "source_url",
    ),
    "bridge_award_opportunity_links": (
        "candidate_id",
        "linked_notice_id",
        "link_confidence",
        "link_reason",
    ),
}
# DELIBERATE EXCLUSIONS (pinned by test): contract_title, description_raw,
# classification_reason (PUBLIC_EXCLUDED_COLUMNS), every *_demo and *_key column.

# A notice row arrives pre-joined with its bridge link, so its allowed keys are the union
# of the two contract tuples (both enumerated above — nothing new leaks in).
_NOTICE_KEYS: tuple[str, ...] = (
    EVIDENCE_CONTRACT["fact_opportunity_notices"] + EVIDENCE_CONTRACT["bridge_award_opportunity_links"]
)


@dataclass(frozen=True)
class BriefEvidence:
    """Everything the brief renderer may read — nothing else exists from its view."""

    candidate: Mapping[str, object]
    vendor: Mapping[str, object] | None
    office: Mapping[str, object] | None
    notices: tuple[Mapping[str, object], ...]
    chips: tuple[ReasonChip, ...]  # from scoring.reason_codes (strict)
    lane: LaneVerdict | None  # None <=> no custom profile (baked mode)
    profile_label: str  # "demo profile" | company name | ""
    as_of: str
    today: date


def _take(row: Mapping[str, object], keys: tuple[str, ...]) -> dict[str, object]:
    """Copy ONLY contract-listed keys (missing keys simply absent — never invented)."""
    return {k: row[k] for k in keys if k in row}


def gather_evidence(
    candidate_row: Mapping[str, object],
    *,
    vendor_row: Mapping[str, object] | None = None,
    office_row: Mapping[str, object] | None = None,
    notice_rows: tuple[Mapping[str, object], ...] = (),
    chips: tuple[ReasonChip, ...] = (),
    lane: LaneVerdict | None = None,
    profile_label: str = "",
    as_of: str = "unknown",
    today: date,
) -> BriefEvidence:
    """Filter every input row down to the contract and freeze the result. Anything not
    enumerated in ``EVIDENCE_CONTRACT`` never reaches the renderer."""
    return BriefEvidence(
        candidate=_take(candidate_row, EVIDENCE_CONTRACT["fact_recompete_candidates"]),
        vendor=_take(vendor_row, EVIDENCE_CONTRACT["dim_vendor"]) if vendor_row is not None else None,
        office=_take(office_row, EVIDENCE_CONTRACT["dim_agency"]) if office_row is not None else None,
        notices=tuple(_take(n, _NOTICE_KEYS) for n in notice_rows),
        chips=tuple(chips),
        lane=lane,
        profile_label=profile_label,
        as_of=as_of,
        today=today,
    )
