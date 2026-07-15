"""
extract_awards.py — Orchestrates the raw pull: two USAspending searches
(always-relevant NAICS with no keyword filter, conditional NAICS with the full
keyword taxonomy), award-detail hydration for candidates clearing the value
threshold, and a best-effort SAM.gov opportunities pull.
"""

import logging
import os
from datetime import date, timedelta
from pathlib import Path

from api_clients.public_data import (
    fetch_sam_opportunities,
    fetch_usaspending_award_detail,
    fetch_usaspending_awards,
    save_raw_pull,
)
from api_clients.sam_bulk import load_sam_opportunities_from_csv
from api_clients.usaspending_bulk import discover_bulk_csvs, load_usaspending_awards_from_csv
from utils.config import (
    KEYWORD_TAXONOMY,
    NAICS_ALWAYS_RELEVANT,
    NAICS_CONDITIONAL,
    SAM_BULK_CSV,
    SAM_BULK_DOD_ONLY,
    SEARCH_CONFIG,
    USASPENDING_BULK_GLOB,
)

logger = logging.getLogger(__name__)


def _extract_awards_from_bulk(bulk_csvs, data_raw_dir):
    """Awards via the USAspending bulk export: no API, no hydration step. Returns
    (search_raw_path, detail_raw_path, transactions_digest_path, search_results,
    detail_records)."""
    loaded = load_usaspending_awards_from_csv(bulk_csvs, naics_codes=SEARCH_CONFIG["naics_codes"])
    search_results = loaded["search_records"]
    detail_records = loaded["detail_records"]
    transaction_digests = loaded["transaction_digests"]
    search_raw_path = save_raw_pull(
        records=search_results, source_system="USAspending (bulk export)",
        source_endpoint="Contracts_Full_CSV",
        params={"source_files": [str(p) for p in bulk_csvs], "naics_codes": SEARCH_CONFIG["naics_codes"]},
        out_dir=data_raw_dir,
    )
    detail_raw_path = save_raw_pull(
        records=detail_records, source_system="USAspending (bulk export)",
        source_endpoint="Contracts_Full_CSV_detail",
        params={"hydrated_count": len(detail_records), "note": "PoP potential end date sourced inline from bulk CSV; no detail API calls"},
        out_dir=data_raw_dir,
    )
    # Persist the per-award transaction-history digests — the mods signal's only input.
    # Pass the LIST of digest VALUES (each digest carries its own award_id); save_raw_pull
    # types records as a list, so the dict itself must never be passed.
    transactions_digest_path = save_raw_pull(
        records=list(transaction_digests.values()), source_system="USAspending (bulk export)",
        source_endpoint="Contracts_Full_CSV_transactions",
        params={"digest_count": len(transaction_digests),
                "note": "per-award transaction digests for the mods signal (bulk-only)"},
        out_dir=data_raw_dir,
    )
    return search_raw_path, detail_raw_path, transactions_digest_path, search_results, detail_records


def _extract_awards_from_api(data_raw_dir, today):
    """Awards via the live spending_by_award API + per-candidate detail hydration
    (the fallback path when no bulk export is present)."""
    modified_since = today - timedelta(days=365 * SEARCH_CONFIG["lookback_years"])
    flat_keywords = KEYWORD_TAXONOMY["cybersecurity"] + KEYWORD_TAXONOMY["it_services"]

    always_relevant_results = fetch_usaspending_awards(
        naics_codes=sorted(NAICS_ALWAYS_RELEVANT),
        dod_toptier_agency=SEARCH_CONFIG["dod_toptier_agency"],
        keywords=[],
        modified_since=modified_since, modified_before=today,
        max_pages=SEARCH_CONFIG["max_search_pages"],
    )
    conditional_results = fetch_usaspending_awards(
        naics_codes=sorted(NAICS_CONDITIONAL),
        dod_toptier_agency=SEARCH_CONFIG["dod_toptier_agency"],
        keywords=flat_keywords,
        modified_since=modified_since, modified_before=today,
        max_pages=SEARCH_CONFIG["max_search_pages"],
    )

    merged = {}
    for record in always_relevant_results + conditional_results:
        gid = record.get("generated_internal_id")
        if gid:
            merged[gid] = record
    search_results = list(merged.values())

    search_raw_path = save_raw_pull(
        records=search_results, source_system="USAspending", source_endpoint="spending_by_award",
        params={
            "naics_always_relevant": sorted(NAICS_ALWAYS_RELEVANT),
            "naics_conditional": sorted(NAICS_CONDITIONAL),
            "keywords": flat_keywords,
            "modified_since": modified_since.isoformat(), "modified_before": today.isoformat(),
        },
        out_dir=data_raw_dir,
    )

    candidates = [
        r for r in search_results
        if (r.get("Award Amount") or 0) >= SEARCH_CONFIG["min_award_value"] and r.get("End Date")
    ][:SEARCH_CONFIG["max_detail_hydrations"]]

    detail_records = []
    for record in candidates:
        gid = record["generated_internal_id"]
        try:
            detail_records.append(fetch_usaspending_award_detail(gid))
        except Exception as e:
            logger.warning(f"Detail hydration failed for {gid}: {e}")

    detail_raw_path = save_raw_pull(
        records=detail_records, source_system="USAspending", source_endpoint="awards_detail",
        params={"hydrated_count": len(candidates)}, out_dir=data_raw_dir,
    )
    # the live API has no cheap multi-transaction history; mods are a bulk-only signal
    # (transactions_digest_path is None on this path).
    return search_raw_path, detail_raw_path, None, search_results, detail_records


def run_extraction(data_raw_dir: Path, today: date = None) -> dict:
    today = today or date.today()

    # Awards: prefer the USAspending bulk export (no flaky API, no hydration step)
    # when any file matches the configured glob; otherwise fall back to the API.
    bulk_csvs = discover_bulk_csvs(USASPENDING_BULK_GLOB) if USASPENDING_BULK_GLOB else []
    if bulk_csvs:
        logger.info(f"Awards source: USAspending bulk export ({len(bulk_csvs)} file(s)).")
        search_raw_path, detail_raw_path, transactions_digest_path, search_results, detail_records = (
            _extract_awards_from_bulk(bulk_csvs, data_raw_dir)
        )
    else:
        logger.info("Awards source: live USAspending API (no bulk export found).")
        search_raw_path, detail_raw_path, transactions_digest_path, search_results, detail_records = (
            _extract_awards_from_api(data_raw_dir, today)
        )

    # Opportunity notices: prefer the local bulk export (no API key, no rate
    # limits) when present; fall back to the live API only if the file is absent.
    if SAM_BULK_CSV and SAM_BULK_CSV.exists():
        sam_results = load_sam_opportunities_from_csv(
            csv_path=SAM_BULK_CSV, naics_codes=SEARCH_CONFIG["naics_codes"],
            dod_only=SAM_BULK_DOD_ONLY,
        )
        sam_source_system = "SAM.gov (bulk export)"
        sam_source_endpoint = "ContractOpportunitiesFullCSV"
        sam_params = {
            "source_file": str(SAM_BULK_CSV), "naics_codes": SEARCH_CONFIG["naics_codes"],
            "dod_only": SAM_BULK_DOD_ONLY,
        }
    else:
        sam_results = fetch_sam_opportunities(
            api_key=os.environ.get("SAM_GOV_API_KEY", ""),
            naics_codes=SEARCH_CONFIG["naics_codes"],
            posted_from=today - timedelta(days=365), posted_to=today,
        )
        sam_source_system = "SAM.gov"
        sam_source_endpoint = "opportunities/v2/search"
        sam_params = {"naics_codes": SEARCH_CONFIG["naics_codes"]}

    sam_raw_path = save_raw_pull(
        records=sam_results, source_system=sam_source_system, source_endpoint=sam_source_endpoint,
        params=sam_params, out_dir=data_raw_dir,
    )

    return {
        "search_raw_path": search_raw_path, "detail_raw_path": detail_raw_path, "sam_raw_path": sam_raw_path,
        "transactions_digest_path": transactions_digest_path,
        "search_count": len(search_results), "detail_count": len(detail_records), "sam_count": len(sam_results),
    }
