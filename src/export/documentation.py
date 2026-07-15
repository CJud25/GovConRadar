"""
build_documentation_exports.py — Builds data_dictionary.csv and
source_inventory.csv for the Tableau export package.
"""

import pandas as pd

DATA_DICTIONARY = [
    ("fact_recompete_candidates", "candidate_id", "Synthetic ID: RC- prefix + source award ID", "string", False),
    ("fact_recompete_candidates", "selected_expiration_date", "current_end_date if potential_end_date is null, else potential_end_date", "date", False),
    ("fact_recompete_candidates", "expiration_date_basis", "Which date was used: current_end_date / potential_end_date / unknown / terminated (a complete_likely termination retargeted the expiration to the termination date)", "string", False),
    ("fact_recompete_candidates", "terminated", "FACT — some transaction carried a termination reason-for-modification code (action_type_code E/F/X/N; K=Close Out is never a termination)", "bool", False),
    ("fact_recompete_candidates", "termination_code", "FACT — earliest termination code (E=default, F=convenience, X=cause, N=legal cancellation); empty when not terminated", "string", False),
    ("fact_recompete_candidates", "termination_action_date", "FACT — earliest termination transaction's action date; empty when not terminated", "date", False),
    ("fact_recompete_candidates", "termination_kind", "ESTIMATE (inferred, conservative) — complete_likely only when the reported current end collapsed to within 31 days of the termination date; else partial_or_unclear; none when not terminated. DoD FPDS reporting lags ~90 days.", "string", True),
    ("fact_recompete_candidates", "termination_basis", "Basis for the termination read: observed_code / none", "string", True),
    ("fact_recompete_candidates", "mod_count", "FACT — distinct FPDS transactions observed for the award in the loaded fiscal-year window", "int", False),
    ("fact_recompete_candidates", "mod_velocity", "ESTIMATE — mod_count per active PoP-year; empty when history is unreadable (single transaction / unusable dates)", "float", True),
    ("fact_recompete_candidates", "mod_velocity_band", "ESTIMATE — low / normal / high (fitted priors in config/mods_signal.yaml) / not_applicable", "string", True),
    ("fact_recompete_candidates", "ceiling_growth_ratio", "Derived from facts — last/first positive CUMULATIVE ceiling (potential_total_value_of_award) across the award's transactions; empty when <2 positive readings", "float", False),
    ("fact_recompete_candidates", "ceiling_balloon_flag", "ESTIMATE — ceiling_growth_ratio above the fitted balloon threshold (1.5)", "bool", True),
    ("fact_recompete_candidates", "ceiling_basis", "Basis for the ceiling read: measured / insufficient", "string", True),
    ("fact_recompete_candidates", "has_deobligation", "ESTIMATE (weak) — a non-closeout transaction below the deobligation floor before the planned end; never labeled a cancellation", "bool", True),
    ("fact_recompete_candidates", "bridge_flag", "ESTIMATE — a non-competed (extent B/C/G) transaction pushed the current end >30 days past the ORIGINAL planned end (FPDS codes only, never text)", "bool", True),
    ("fact_recompete_candidates", "bridge_basis", "Basis for the bridge read: observed / insufficient", "string", True),
    ("fact_recompete_candidates", "mods_basis", "History-coverage label for the count/velocity/ceiling reads: measured / single_transaction / insufficient", "string", True),
    ("fact_recompete_candidates", "successor_visible", "ESTIMATE (inferred) — a later award in the same NAICS x PSC-class x DoD-component cell shows FPDS activity after this end date (same-parent-IDV task orders and the award itself excluded); empty = Unknown (thin cell)", "bool", True),
    ("fact_recompete_candidates", "successor_visible_basis", "Basis: observed / none_visible (no successor visible in public data yet — DoD reporting lags ~90 days; NEVER a 'missed recompete') / insufficient_cell", "string", True),
    ("dim_vendor", "size_standard_shift", "ESTIMATE (directional, per-procurement) — the vendor's same-NAICS awards' CO size determination moved S (small) to O (other-than-small) over time; empty = Unknown (insufficient determinations). Never a vendor-size verdict.", "bool", True),
    ("dim_vendor", "size_standard_basis", "Named basis for size_standard_shift (counts + codes per the flagged NAICS cell, or the insufficient label)", "string", True),
    ("fact_transactions", "transaction_id", "FACT — unique per emitted transaction ({award_id}:{modification_number}, deterministically suffixed on collision)", "string", False),
    ("fact_transactions", "action_type_code", "FACT — FPDS reason-for-modification code of the signal-bearing transaction (E/F/X/N terminations, K closeouts, etc.)", "string", False),
    ("fact_transactions", "action_obligation", "FACT — the transaction's obligation delta (federal_action_obligation)", "float", False),
    ("fact_transactions", "description", "FACT — transaction free text (bounded); LOCAL export only — excluded from every public artifact (sample + Release) and PII-scrubbed locally", "string", False),
    ("fact_recompete_candidates", "estimated_recompete_window_start", "ESTIMATE — not an official government date. See scoring_methodology.md", "date", True),
    ("fact_recompete_candidates", "estimated_recompete_window_end", "ESTIMATE — not an official government date. See scoring_methodology.md", "date", True),
    ("fact_recompete_candidates", "pursuit_score", "ESTIMATE — weighted composite of 8 component scores, 0-100", "float", True),
    ("fact_recompete_candidates", "priority_tier", "ESTIMATE — derived from pursuit_score thresholds", "string", True),
    ("fact_contract_awards", "naics", "NAICS code as reported by USAspending", "string", False),
    ("fact_contract_awards", "psc", "Product/Service Code as reported by USAspending", "string", False),
    ("fact_contract_awards", "classification_confidence", "ESTIMATE — High/Medium/Low/Not Classified rules-based classification", "string", True),
    ("dim_vendor", "incumbent_vulnerability_score", "ESTIMATE — value-weighted share of the vendor's forward book (days_until_expiration >= 0) expiring within 180 days; empty = Unknown (see vulnerability_basis). Read next to number_of_cyber_it_awards (n). See scoring_methodology.md", "float", True),
    ("dim_vendor", "vulnerability_basis", "Basis for incumbent_vulnerability_score: value_weighted_near_term_share (score present) / no_forward_book / insufficient_expiration_coverage (score empty — Unknown, never an imputed 0)", "string", True),
    ("dim_vendor", "pct_value_expired", "Share (%) of the vendor's total obligated value on already-expired rows (days_until_expiration < 0) — excluded from the vulnerability denominator", "float", False),
    ("dim_vendor", "pct_value_unknown_expiration", "Share (%) of the vendor's total obligated value with no usable expiration date — excluded from the vulnerability denominator", "float", False),
    ("internal_only", "VENDOR_PROFILE_SYNTHETIC", "SYNTHETIC — the pursuing vendor profile used by scoring.py is entirely fictional (see config.py). It is never exported to any Tableau CSV; it only informs pursuit_score/priority_tier calculations.", "n/a", True),
    ("bridge_award_opportunity_links", "link_confidence", "ESTIMATE — fuzzy-match confidence; 'No Match' for all rows until SAM.gov data is pulled locally", "string", True),
    ("streamlit_app view: Market map", "hhi_concentration", "Descriptive incumbent obligated-dollar concentration (top-incumbent dollar-share + incumbent count) of the expiring reportable set (Data Gap excluded), computed live in scoring.market_concentration.compute_hhi_concentration. A ratio of two sums of published obligated-dollar facts — a fact, not an estimate. Markets below min_market_ueis distinct incumbents or the UEI-coverage floor render 'Unknown' — never an imputed number. NOT a market-power, market-share, or contestability claim; NOT a persisted column. See scoring_methodology.md", "float", False),
]


def build_data_dictionary() -> pd.DataFrame:
    return pd.DataFrame(DATA_DICTIONARY, columns=["table", "field", "description", "data_type", "is_estimate"])


SOURCE_INVENTORY = [
    ("USAspending.gov", "api/v2/search/spending_by_award/", "Award search — PIID, recipient, obligations, NAICS/PSC, dates", "Live, no key required", "Confirmed reachable in build environment (2026-07-01)"),
    ("USAspending.gov", "api/v2/awards/<id>/", "Award detail — potential_end_date, extent_competed, parent award", "Live, no key required", "Confirmed reachable in build environment (2026-07-01)"),
    ("SAM.gov", "opportunities/v2/search", "Opportunity notices — sources sought, solicitations", "Live, requires SAM_GOV_API_KEY", "Unreachable from build sandbox — client built, must be run locally by user"),
    ("FPDS PSC Manual", "Manual reference", "PSC code descriptions and IT/cyber relevance", "Manual download / hand-curated", "No bulk API exists"),
    ("US Census NAICS 2022", "Manual reference", "NAICS code descriptions", "Manual reference", "Seed list from project brief"),
]


def build_source_inventory() -> pd.DataFrame:
    return pd.DataFrame(SOURCE_INVENTORY, columns=["source_system", "endpoint_or_method", "purpose", "access_method", "status_notes"])
