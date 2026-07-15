"""adapter.py — maps the real GovConRecompeteRadar star-schema onto the
alerting-bridge snapshot contract (spec §2).

Verified against the live pipeline output on 2026-07-06:
  data/powerbi/fact_recompete_candidates.csv   (the candidates fact table)
  data/powerbi/bridge_award_opportunity_links.csv
  data/powerbi/fact_opportunity_notices.csv
  data/powerbi/dim_priority_tier.csv

Two corrections vs. the spec's default assumptions:
  1. `notice_ids` is NOT a column on the candidate row. Notice links live in a
     separate bridge table (one row per link). DR-5 must JOIN, not read a list.
  2. Award/notice source links already exist as verified `source_url` columns.
     Do NOT build links from templates + candidate_id: `RC-<PIID>` is not a
     USAspending award id, so a templated URL would be fabricated data —
     forbidden by CLAUDE.md product rule #2 (facts vs estimates).
"""

from __future__ import annotations

# --- Contract field  ->  real star-schema column ---------------------------
# Engine-internal contract name (spec §2)      real column in fact_recompete_candidates
COLUMN_MAP: dict[str, str] = {
    "award_key": "candidate_id",  # stable: RC-{award_id} (recompete.py:76)
    "recipient_name": "incumbent_vendor",
    "recipient_uei": "incumbent_uei",
    "naics": "naics",
    "psc": "psc",
    "agency": "agency",
    # The pipeline's *resolved* expiry (accounts for exercised options / basis) is
    # `selected_expiration_date`; `current_end_date` is the raw PoP end. The spec's
    # contract field means "PoP end incl. exercised options" -> selected_expiration_date.
    "current_end_date": "selected_expiration_date",
    "potential_end_date": "potential_end_date",
    "tier": "priority_tier",
    "score": "pursuit_score",
    "obligated_to_date": "total_obligated_amount",
}

# The award key is already a stable single field; no composite needed.
AWARD_KEY_FIELDS: list[str] = ["candidate_id"]

# Verified per-row source link on the candidate row. Use directly; never template.
SOURCE_URL_FIELD: str = "source_url"

# Optional cross-check field the pipeline already computes (whole-ish months proxy).
# Prefer recomputing months_to_expiry from `current_end_date` + injected clock per
# spec §4, but this is available if you want a determinism cross-check.
DAYS_TO_EXPIRY_FIELD: str = "days_until_expiration"

# --- Notice links: a JOIN, not a column ------------------------------------
# DR-5 (new notice link) compares the set of linked notice ids between prev/curr.
# Build that set by joining the bridge table on candidate_id.
NOTICE_LINK_TABLE: str = "bridge_award_opportunity_links"
NOTICE_LINK_JOIN_KEY: str = "candidate_id"  # -> COLUMN_MAP["award_key"]
NOTICE_LINK_ID_FIELD: str = "linked_notice_id"  # the SAM notice id

# A DR-5 item's notice link is the notice's own verified source_url, obtained by
# joining fact_opportunity_notices on notice_id. No template.
NOTICE_TABLE: str = "fact_opportunity_notices"
NOTICE_ID_FIELD: str = "notice_id"
NOTICE_SOURCE_URL_FIELD: str = "source_url"

# --- Contract fields with NO real backing column ---------------------------
# `notice_ids` is intentionally absent from COLUMN_MAP: it is derived via the
# bridge join above, then attached to the contract frame as a list[str] column.
DERIVED_FIELDS: tuple[str, ...] = ("notice_ids",)
