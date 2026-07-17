"""Label taxonomy — the single language source for the measured-trust rails.

Pure constants, no first-party imports (mypy-strict). Phase-2 app surfaces import
SURFACE_LANGUAGE; Phase-4 hand-labeling and the trust metrics consume the outcome
taxonomy and the worksheet CSV schemas. Encoded here rather than in prose:
"ended" and "consolidation_or_split" require POSITIVE evidence — absence of a
visible successor is undeterminable(no_notice_found), never "ended".
"""

OUTCOME_LABELS: tuple[str, ...] = (
    "recompete_unchanged",
    "extension_bridge",
    "vehicle_migration",
    "consolidation_or_split",
    "ended",
    "sole_source_follow_on",
    "undeterminable",
    "successor_out_of_scope",
)

# Outcomes with positive evidence the work CONTINUED somewhere public data can see.
CONTINUATION_OUTCOMES: tuple[str, ...] = tuple(o for o in OUTCOME_LABELS if o not in ("ended", "undeterminable"))

# Canonical key -> UI phrase (every OUTCOME_LABELS key, exactly; S37 renders these).
OUTCOME_DISPLAY: dict[str, str] = {
    "recompete_unchanged": "Recompeted — substantially the same work",
    "extension_bridge": "Extended / bridged — follow-on still pending",
    "vehicle_migration": "Moved onto a contract vehicle",
    "consolidation_or_split": "Consolidated or split (positive evidence)",
    "ended": "Ended — positive evidence of no follow-on",
    "sole_source_follow_on": "Sole-source follow-on",
    "undeterminable": "Undeterminable from public data",
    "successor_out_of_scope": "Successor outside this dataset's scope (e.g. assisted acquisition)",
}

UNDETERMINABLE_REASONS: tuple[str, ...] = (
    "no_notice_found",
    "conflicting_evidence",
    "successor_ambiguous",
    "insufficient_history",
)

LABEL_CONFIDENCE_GRADES: tuple[str, ...] = ("high", "medium", "low")

LINK_LABEL_VALUES: tuple[str, ...] = ("correct", "incorrect", "unsure")

# "carried" = a labeled case migrated from a prior sample (e.g. the order->vehicle grain
# change) that the current random draw did not re-select; kept so a filled label is never
# lost, but flagged so it never contaminates the random stratified/top50 rates.
SAMPLE_SETS: tuple[str, ...] = ("stratified", "top50", "both", "carried")

INCUMBENT_RETAINED_VALUES: tuple[str, ...] = ("yes", "no", "unclear")

# Ordered CSV schema for data/labels/link_labels.csv (S16's worksheet generator
# appends rows in exactly this shape; ingest.load_link_labels enforces it).
LINK_LABEL_COLUMNS: tuple[str, ...] = (
    "case_id",  # "L-{candidate_id}-{linked_notice_id}", unique
    "sampled_snapshot_date",
    "candidate_id",
    "linked_notice_id",
    "link_confidence",  # frozen at sampling time
    "link_reason",  # frozen at sampling time
    "candidate_piid",
    "candidate_title",
    "candidate_subagency",
    "candidate_naics",
    "notice_title",
    "notice_solicitation_number",
    "notice_posted_date",
    "candidate_source_url",  # copied from baked source_url columns, NEVER constructed
    "notice_source_url",
    "label",  # BLANK until filled: correct | incorrect | unsure
    "labeler_notes",
    "labeled_date",
)

# Ordered CSV schema for data/labels/outcome_labels.csv. AWARDEE-BLIND BY SCHEMA:
# no successor/awardee identity column exists above the fill line — identity is a
# post-unmask observation, never an input to selection or adjudication.
OUTCOME_LABEL_COLUMNS: tuple[str, ...] = (
    "case_id",  # "O-{candidate_id}", unique
    "sample_set",  # stratified | top50 | both | carried
    "sampled_snapshot_date",
    "candidate_id",
    "predecessor_piid",
    "predecessor_title",
    "subagency",
    "naics",
    "psc",
    "potential_end_date",
    "value_band",  # "<$1M" | "$1M-$10M" | ">=$10M" on total_obligated_amount
    "vehicle_kind",  # vehicle | standalone (referenced_idv_piid non-blank / blank)
    "predecessor_source_url",
    # ---- labeler fills below; NO successor/awardee identity column above this line ----
    "outcome_label",  # one of OUTCOME_LABELS
    "undeterminable_reason",  # required iff outcome_label == "undeterminable"
    "label_confidence",  # high | medium | low
    "notice_anchored",  # Y | N — N caps label_confidence at "medium" (validated)
    "evidence_notice_id",
    "evidence_url_1",
    "evidence_url_2",
    "unmask_performed",  # Y | N
    "unmask_date",
    "incumbent_retained_observed",  # yes | no | unclear; post-unmask only
    "label_changed_after_unmask",  # Y requires explanation in labeler_notes (audit trail)
    "labeler_notes",
    "labeled_date",
)

# App-surface copy (S27 re-points streamlit_app/components/data.py at these —
# the two bridge-watch strings are MOVED here verbatim, one language source).
SURFACE_LANGUAGE: dict[str, str] = {
    "bridge_watch_label": "Recently lapsed, no successor visible yet",
    "bridge_watch_note": "no successor visible in public data yet (DoD reporting lags ~90 days)",
    "extension_bridge": OUTCOME_DISPLAY["extension_bridge"],
}
