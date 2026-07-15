"""
generate_sample_data.py — Builds the bundled SYNTHETIC sample star schema in
streamlit_app/assets/sample_data/ so the Streamlit app runs on Community Cloud
without the pipeline or any API access.

The award records here are 100% fictional, but they are pushed through the REAL
transform → classify → score → export chain, so the sample is internally
consistent with production logic (scores, tiers, buckets, and the star schema all
come from the same code paths). Run:  python streamlit_app/generate_sample_data.py

Requires the pipeline library (src/) — shipped in both the source repo and, since
2026-07-08, the public deploy repo; the guard below only fires on a checkout that
somehow lacks src/.
"""

import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

if not (REPO / "src").exists():
    sys.exit(
        "This script requires the private data-pipeline repo (src/), which is not part of this "
        "public case-study repo. Its output is already committed at streamlit_app/assets/sample_data/ "
        "— nothing needs regenerating to run the app."
    )

from export.powerbi_export import write_powerbi_exports
from scoring.price_to_win import attach_ptw
from scoring.pursuit_score import score_candidate
from transform.classification import classify_awards
from transform.incumbent_agency import build_agency_summary, build_incumbent_summary
from transform.opportunity_linking import build_bridge_table, build_opportunities_clean
from transform.recompete import build_recompete_candidates
from transform.reference_tables import build_naics_lookup, build_psc_lookup
from utils.config import PRICE_TO_WIN, SEARCH_CONFIG
from validation.data_quality import build_data_quality_report

# extent_competed_code -> descriptive text (the sample's letters are FPDS codes).
_EXTENT_TEXT = {
    "A": "FULL AND OPEN COMPETITION", "B": "NOT AVAILABLE FOR COMPETITION",
    "C": "NOT COMPETED", "D": "FULL AND OPEN COMPETITION AFTER EXCLUSION OF SOURCES",
}

TODAY = date(2026, 7, 1)
OUT = REPO / "streamlit_app" / "assets" / "sample_data"

# (piid, vendor, uei, subagency, naics, psc, desc, amount, potential_end, state, extent, notes)
_A = [
    ("W91-0001", "ACME CYBER LLC", "UEI0001", "DEPARTMENT OF THE ARMY", "541512", "D307",
     "Cybersecurity compliance, RMF support, and security operations center (SOC) services", 5_200_000, date(2026, 10, 1), "VA", "A", ""),
    ("W91-0002", "ACME CYBER LLC", "UEI0001", "DEPARTMENT OF THE ARMY", "541519", "D399",
     "Help desk and end user support services for enterprise IT", 1_100_000, date(2027, 5, 1), "VA", "B", ""),
    ("W91-0003", "ACME CYBER LLC", "UEI0001", "DEFENSE HEALTH AGENCY", "541512", "D307",
     "Cloud migration and enterprise architecture IT modernization", 12_500_000, date(2027, 10, 15), "MD", "A", ""),
    ("N00-0100", "MERIDIAN DEFENSE GROUP", "UEI0002", "DEPARTMENT OF THE NAVY", "541512", "DH01",
     "Zero trust cloud security and identity and access management (IAM)", 28_000_000, date(2028, 3, 1), "CA", "A", ""),
    ("N00-0101", "MERIDIAN DEFENSE GROUP", "UEI0002", "DEPARTMENT OF THE NAVY", "518210", "D316",
     "Network operations and infrastructure support, data center services", 3_400_000, date(2027, 1, 20), "VA", "C", ""),
    ("F33-0200", "NORTHSTAR IT SOLUTIONS", "UEI0003", "DEPARTMENT OF THE AIR FORCE", "541512", "DF01",
     "CMMC and NIST compliance, FISMA continuous monitoring, SIEM operations", 8_900_000, date(2027, 8, 1), "CO", "A", ""),
    ("F33-0201", "NORTHSTAR IT SOLUTIONS", "UEI0003", "DEPARTMENT OF THE AIR FORCE", "541519", "D399",
     "Software development and DevSecOps application development services", 6_100_000, date(2029, 1, 1), "TX", "D", ""),
    ("DISA-0300", "SUMMIT TECHNOLOGIES INC", "UEI0004", "DEFENSE INFORMATION SYSTEMS AGENCY", "541513", "D316",
     "Managed services and systems administration for DISA enterprise", 15_750_000, date(2027, 3, 10), "VA", "A", ""),
    ("DISA-0301", "SUMMIT TECHNOLOGIES INC", "UEI0004", "DEFENSE INFORMATION SYSTEMS AGENCY", "541512", "DF01",
     "Cyber defense operations, threat hunting, and vulnerability management", 22_300_000, date(2028, 6, 1), "MD", "A", ""),
    ("DHA-0400", "CAPITAL CYBER PARTNERS", "UEI0005", "DEFENSE HEALTH AGENCY", "541611", "D307",
     "Cybersecurity compliance advisory and RMF authorization (ATO) support", 950_000, date(2026, 11, 15), "DC", "B", ""),
    ("W91-0500", "BLUE RIDGE ANALYTICS", "UEI0006", "DEPARTMENT OF THE ARMY", "541519", "D399",
     "Data analytics, Power BI dashboards, and database administration", 2_750_000, date(2027, 6, 30), "GA", "C", "potential end date estimated from base plus options"),
    ("N00-0600", "BLUE RIDGE ANALYTICS", "UEI0006", "DEPARTMENT OF THE NAVY", "541512", "R425",
     "Engineering and technical services with cyber systems security engineering", 4_300_000, date(2028, 2, 1), "AL", "D", ""),
    ("F33-0700", "PATRIOT DIGITAL SERVICES", "UEI0007", "DEPARTMENT OF THE AIR FORCE", "541512", "9999",
     "Enterprise IT support and telecom managed services", 3_900_000, None, "TX", "C", ""),  # unknown end date
    ("W91-0800", "PATRIOT DIGITAL SERVICES", "UEI0007", "DEPARTMENT OF THE ARMY", "541512", "DF01",
     "Security operations center (SOC) and endpoint detection and response (EDR)", 58_000_000, date(2027, 9, 1), "VA", "A", ""),
]

AWARDS_CLEAN_COLUMNS = [
    "award_id", "piid", "referenced_idv_piid", "award_type", "awardee_name_raw", "awardee_name_clean",
    "awardee_uei", "awarding_agency_raw", "awarding_agency_clean", "awarding_subagency_raw",
    "awarding_subagency_clean", "funding_agency_clean", "date_signed", "total_obligated_amount",
    "potential_value", "pop_start_date", "pop_current_end_date", "pop_potential_end_date", "naics",
    "psc", "description_raw", "extent_competed", "extent_competed_code", "type_of_contract_pricing",
    "type_of_contract_pricing_code", "type_of_set_aside", "type_of_set_aside_code",
    "number_of_offers_received", "base_and_all_options_value", "place_of_performance_state",
    "missing_end_date_flag", "missing_vendor_flag", "missing_agency_flag", "data_quality_notes",
    "source_system", "business_size_determination_code",
]


def _awards_clean() -> pd.DataFrame:
    rows = []
    for i, (piid, vendor, uei, sub, naics, psc, desc, amt, pend, state, extent, notes) in enumerate(_A, start=1):
        rows.append({
            "award_id": f"SAMPLE-{i:03d}", "piid": piid, "referenced_idv_piid": None,
            "award_type": "DEFINITIVE CONTRACT", "awardee_name_raw": vendor, "awardee_name_clean": vendor,
            "awardee_uei": uei, "awarding_agency_raw": "Department of Defense",
            "awarding_agency_clean": "DEPARTMENT OF DEFENSE", "awarding_subagency_raw": sub.title(),
            "awarding_subagency_clean": sub, "funding_agency_clean": "DEPARTMENT OF DEFENSE",
            "date_signed": date(2023, 1, 15), "total_obligated_amount": float(amt),
            "potential_value": float(amt) * 1.4, "pop_start_date": date(2023, 1, 15),
            "pop_current_end_date": pend, "pop_potential_end_date": pend, "naics": naics, "psc": psc,
            "description_raw": desc, "extent_competed": _EXTENT_TEXT.get(extent, extent),
            "extent_competed_code": extent, "type_of_contract_pricing": "FIRM FIXED PRICE",
            "type_of_contract_pricing_code": "J", "type_of_set_aside": None, "type_of_set_aside_code": None,
            "number_of_offers_received": None, "base_and_all_options_value": float(amt) * 1.4,
            "place_of_performance_state": state,
            "missing_end_date_flag": pend is None, "missing_vendor_flag": False, "missing_agency_flag": False,
            "data_quality_notes": notes, "source_system": "SYNTHETIC_SAMPLE",
        })
    return pd.DataFrame(rows, columns=AWARDS_CLEAN_COLUMNS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    naics_lookup = build_naics_lookup()
    psc_lookup = build_psc_lookup()

    awards_clean = _awards_clean()
    classified = classify_awards(awards_clean, naics_lookup, psc_lookup)
    candidates = build_recompete_candidates(classified, today=TODAY, min_award_value=SEARCH_CONFIG["min_award_value"])
    candidates, ptw_comparables = attach_ptw(candidates, classified, PRICE_TO_WIN, today=TODAY)

    scores = candidates.apply(lambda r: score_candidate(r.to_dict()), axis=1, result_type="expand")
    scoring_breakdown = pd.concat([candidates[["candidate_id"]], scores], axis=1)
    candidates = candidates.merge(scoring_breakdown[["candidate_id", "pursuit_score", "priority_tier"]], on="candidate_id")

    incumbent_summary = build_incumbent_summary(candidates)
    agency_summary = build_agency_summary(candidates)
    bridge = build_bridge_table(candidates, build_opportunities_clean([]))
    dq = build_data_quality_report(
        search_count=len(awards_clean), detail_count=len(awards_clean), sam_count=0,
        awards_clean=awards_clean, classified_awards=classified, recompete_candidates=candidates,
    )

    written = write_powerbi_exports(
        powerbi_dir=OUT, recompete_candidates=candidates, classified_awards=classified,
        incumbent_summary=incumbent_summary, agency_summary=agency_summary,
        naics_lookup=naics_lookup, psc_lookup=psc_lookup, bridge_table=bridge,
        scoring_breakdown=scoring_breakdown, data_quality_report=dq, today=TODAY,
        ptw_comparables=ptw_comparables,
    )
    print(f"Wrote {len(written)} sample tables to {OUT}")
    print(f"  candidates: {len(candidates)} | tiers: {candidates['priority_tier'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
