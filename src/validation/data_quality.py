"""
validation.py — Data quality report: row counts, null/duplicate checks,
confidence distribution, and documented source-coverage limitations.
"""

import pandas as pd

# Static limitations that don't depend on run outputs. Source/coverage notes that
# DO depend on the run (SAM linkage, recovered fields) are built dynamically in
# build_data_quality_report so they can never go stale against the shipped data.
KNOWN_LIMITATIONS = [
    "USAspending extraction filters on last_modified_date over a configurable lookback "
    "window (default 6 years), NOT on period_of_performance_current_end_date directly — "
    "USAspending's spending_by_award endpoint has no server-side filter for PoP end dates "
    "(confirmed by API testing: an unsupported date_type value returns HTTP 500). Contracts "
    "with no obligation/modification activity within the lookback window are systematically "
    "absent from this dataset, even if still active with a future end date. This is the "
    "single largest known coverage gap in this pipeline.",
    "The 'office' field specified in the original data model is not populated; its exact "
    "USAspending field name was not verified against the live API and is omitted rather "
    "than guessed. (The 'set_aside' field IS now populated — recovered from the FPDS "
    "type_of_set_aside code in the bulk export.)",
    "Recompete timing windows, pursuit scores/tiers, the Competitive Price Range, and "
    "incumbent analysis are analytical estimates from public signals only, never official "
    "government predictions.",
]


def _sam_linkage_note(sam_count) -> str:
    """Accurate, non-stale SAM.gov coverage note built from the actual run."""
    if not sam_count:
        return ("SAM.gov Contract Opportunity notices are not present in this build — the SAM "
                "bulk CSV was absent and api.sam.gov needs a SAM_GOV_API_KEY. Award↔notice "
                "linking (bridge_award_opportunity_links.csv) therefore shows 'No Match' for "
                "every candidate.")
    return (f"SAM.gov Contract Opportunity notices ARE present ({sam_count:,} loaded via the "
            "no-key bulk CSV path) and fuzzy-matched to awards in "
            "bridge_award_opportunity_links.csv. Link coverage is intentionally low (only a "
            "small share of candidates match a live notice) because a recompete's solicitation "
            "is usually posted only months before award — so contracts expiring 6-24 months out "
            "typically have no notice yet. A linked notice is a strong early signal; its absence "
            "is no signal, never evidence a recompete isn't coming.")


def build_data_quality_report(search_count, detail_count, sam_count,
                               awards_clean: pd.DataFrame, classified_awards: pd.DataFrame,
                               recompete_candidates: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"metric": "usaspending_search_records_pulled", "value": search_count, "category": "row_count"},
        {"metric": "usaspending_detail_records_hydrated", "value": detail_count, "category": "row_count"},
        {"metric": "sam_gov_opportunity_records_pulled", "value": sam_count, "category": "row_count"},
        {"metric": "awards_clean_row_count", "value": len(awards_clean), "category": "row_count"},
        {"metric": "cyber_it_classified_count",
         "value": int(classified_awards["cyber_it_flag"].sum()) if not classified_awards.empty else 0,
         "category": "row_count"},
        {"metric": "recompete_candidate_count", "value": len(recompete_candidates), "category": "row_count"},
        {"metric": "duplicate_piid_count",
         "value": int(awards_clean["piid"].duplicated().sum()) if not awards_clean.empty else 0,
         "category": "duplicate_check"},
        {"metric": "missing_end_date_count",
         "value": int(awards_clean["missing_end_date_flag"].sum()) if not awards_clean.empty else 0,
         "category": "missing_value"},
        {"metric": "missing_vendor_count",
         "value": int(awards_clean["missing_vendor_flag"].sum()) if not awards_clean.empty else 0,
         "category": "missing_value"},
        {"metric": "missing_agency_count",
         "value": int(awards_clean["missing_agency_flag"].sum()) if not awards_clean.empty else 0,
         "category": "missing_value"},
    ]

    if not classified_awards.empty:
        for tier, count in classified_awards["classification_confidence"].value_counts().items():
            rows.append({
                "metric": f"classification_confidence_{str(tier).lower().replace(' ', '_')}",
                "value": int(count), "category": "confidence_distribution",
            })

    notes = list(KNOWN_LIMITATIONS) + [_sam_linkage_note(sam_count)]
    for i, limitation in enumerate(notes, start=1):
        rows.append({"metric": f"known_limitation_{i}", "value": limitation, "category": "source_coverage_notes"})

    return pd.DataFrame(rows)
