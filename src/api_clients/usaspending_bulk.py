"""
usaspending_bulk.py — Load DoD contract awards from USAspending "Custom Award
Data" bulk CSV exports (e.g. FY2026_097_Contracts_Full_*.csv) instead of the
live spending_by_award API.

The bulk export is the same underlying data as the API but has two decisive
advantages for this pipeline:
  * it never returns the intermittent HTTP 503 that the search API throws under
    load (which killed several live runs on the conditional-NAICS search), and
  * every row already carries period_of_performance_potential_end_date, so the
    slow per-award detail-hydration step (up to thousands of extra API calls) is
    unnecessary.

Each CSV row is a prime-award *transaction* (one modification), so many rows can
share an award identity. Dedupe policy: **latest transaction, deterministic
tie-break** — per contract_award_unique_key we keep the row with max action_date
(100% ISO YYYY-MM-DD in these exports, so lexical max is safe), and among rows
tied on action_date the one with max contract_transaction_unique_key, so output
never depends on file order. The key is contract_award_unique_key (USAspending's
generated_unique_award_id), NOT the bare award_id_piid: bare PIIDs are not award
identities — the same PIID (e.g. "0001") recurs across different vendors and
parent IDVs, and keying on it silently merges distinct awards. We fall back to
the PIID only when a file lacks the column entirely (schema drift, logged loudly)
or a row's key cell is blank (counted per file, one loud warning when any occur).
Records are emitted in the exact shape transform.cleaning.build_awards_clean
already consumes:
  * search_records: dicts keyed by the API's display field names ("Award ID",
    "Recipient Name", ...), with generated_internal_id set to the unique key and
    "Award ID" kept as the human-facing PIID.
  * detail_records: dicts keyed by generated_unique_award_id == the unique key,
    carrying the period_of_performance / base_and_all_options / extent_competed /
    parent award fields build_awards_clean reads out of the detail payload.
A happy side effect of real CONT_AWD_/CONT_IDV_ keys: transform.recompete
build_source_url now emits resolving usaspending.gov/award/<id> links on the
bulk path instead of None.

So the rest of the pipeline is agnostic to whether awards came from the API or
the bulk file.
"""

import csv
import glob
import logging
from pathlib import Path

from scoring.mods_signal import fold_transaction

logger = logging.getLogger(__name__)

# Some transaction_description / justification cells are very large.
csv.field_size_limit(50_000_000)


def _num(value):
    """Parse a bulk-CSV numeric cell to float, or None when blank/non-numeric.
    build_awards_clean re-parses too, but returning clean values here keeps the
    saved raw envelope tidy."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(value):
    """Parse a count cell (number_of_offers_received) to int, or None when
    blank/non-numeric. Kept nullable — offers-received is blank on ~80% of DoD
    rows, and a real 0 would mean something different from 'unreported'."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _code(value):
    """Normalize a short FPDS code cell to a stripped string or None."""
    text = str(value or "").strip()
    return text or None


def _map_row(row: dict):
    """Map one bulk-CSV row to (gid, search_record, detail_record). gid is the
    award identity: contract_award_unique_key, falling back to the bare PIID when
    the column is missing (the caller warns once per drifted file) or the cell is
    blank (the caller counts blank-cell fallbacks and warns once per file)."""
    piid = (row.get("award_id_piid") or "").strip()
    gid = (row.get("contract_award_unique_key") or "").strip() or piid
    if not gid:
        return None
    search_record = {
        "generated_internal_id": gid,
        "Award ID": piid,
        "Contract Award Type": row.get("award_type"),
        "Recipient Name": row.get("recipient_name"),
        "Recipient UEI": row.get("recipient_uei"),
        "Awarding Agency": row.get("awarding_agency_name"),
        "Awarding Sub Agency": row.get("awarding_sub_agency_name"),
        "Funding Agency": row.get("funding_agency_name"),
        "Base Obligation Date": row.get("action_date"),
        "Award Amount": _num(row.get("total_dollars_obligated")),
        "Start Date": row.get("period_of_performance_start_date"),
        "End Date": row.get("period_of_performance_current_end_date"),
        "naics_code": (row.get("naics_code") or "").strip(),
        "psc_code": (row.get("product_or_service_code") or "").strip(),
        "Description": row.get("prime_award_base_transaction_description") or row.get("transaction_description"),
        "Place of Performance State Code": row.get("primary_place_of_performance_state_code"),
    }
    detail_record = {
        "generated_unique_award_id": gid,
        "period_of_performance": {
            "start_date": row.get("period_of_performance_start_date"),
            "end_date": row.get("period_of_performance_current_end_date"),
            "potential_end_date": row.get("period_of_performance_potential_end_date"),
        },
        # Existing ceiling proxy (potential_total_value_of_award) is deliberately
        # left unchanged so `potential_value` semantics don't shift under the
        # scorer; base_and_all_options_value is the NEW true ceiling for PTW.
        "base_and_all_options": _num(row.get("potential_total_value_of_award")),
        "base_and_all_options_value": _num(row.get("base_and_all_options_value")),
        # CODE column ("S"/"O"), never the text column (both text values contain the
        # substring "SMALL", so a text compare mis-fires) — codes-over-text convention,
        # mirroring the sibling extent_competed_code / type_of_set_aside_code reads.
        "business_size_determination_code": _code(row.get("contracting_officers_determination_of_business_size_code")),
        "latest_transaction_contract_data": {
            "extent_competed": row.get("extent_competed"),
            "extent_competed_code": _code(row.get("extent_competed_code")),
            "type_of_contract_pricing": row.get("type_of_contract_pricing"),
            "type_of_contract_pricing_code": _code(row.get("type_of_contract_pricing_code")),
            "type_of_set_aside": row.get("type_of_set_aside"),
            "type_of_set_aside_code": _code(row.get("type_of_set_aside_code")),
            "number_of_offers_received": _int(row.get("number_of_offers_received")),
        },
        "parent_award": {"piid": (row.get("parent_award_id_piid") or "").strip() or None},
    }
    return gid, search_record, detail_record


def load_usaspending_awards_from_csv(csv_paths, naics_codes) -> dict:
    """Stream the bulk contract CSV(s), keep rows whose naics_code is in
    naics_codes, dedupe to the latest transaction per contract_award_unique_key
    (tie-break: max contract_transaction_unique_key, so output is file-order
    independent), and return {"search_records": [...], "detail_records": [...],
    "transaction_digests": {gid: digest}}.

    transaction_digests is a PARALLEL accumulation (scoring.mods_signal.fold_transaction)
    of the per-award modification history the award-level dedupe discards — the award
    `best`-dict logic is untouched by it. Unlike the award dedupe (immune to re-listed
    transactions by the strict > tie-break), the digest fold is duplicate-sensitive, so
    it folds each distinct contract_transaction_unique_key ONCE (seen_txn_keys): the
    FY24/FY25 Delta member re-lists archive transactions (measured 100% overlap) and
    would otherwise silently double-count mod_count / duplicate evidence rows.
    Keep-first-occurrence, never file-skip — a genuinely new delta transaction still folds.

    Returns empty lists (and logs a warning) if no input file exists, so the
    caller can fall back to the live API exactly like the SAM loader does.
    """
    paths = [Path(p) for p in csv_paths if Path(p).exists()]
    if not paths:
        logger.warning(f"No USAspending bulk export found in {csv_paths} — falling back to live API.")
        return {"search_records": [], "detail_records": [], "transaction_digests": {}}

    naics_set = {str(c) for c in naics_codes}
    # Latest transaction per award: {gid: (action_date_str, txn_key, search_rec, detail_rec)}
    best = {}
    digests = {}  # gid -> mods_signal.TransactionDigest (parallel accumulation)
    seen_txn_keys = set()  # cross-file transaction dedupe for the digest fold only
    scanned = 0
    for path in paths:
        blank_key_fallbacks = 0
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            has_key_column = (reader.fieldnames is not None
                              and "contract_award_unique_key" in reader.fieldnames)
            if reader.fieldnames is not None and not has_key_column:
                logger.warning(
                    f"SCHEMA DRIFT: {path.name} has no contract_award_unique_key column — "
                    f"falling back to award_id_piid as the dedupe key. Bare PIIDs are NOT "
                    f"award identities (the same PIID recurs across vendors/parent IDVs), "
                    f"so distinct awards may silently merge for this file."
                )
            for row in reader:
                scanned += 1
                if (row.get("naics_code") or "").strip() not in naics_set:
                    continue
                mapped = _map_row(row)
                if mapped is None:
                    continue
                if has_key_column and not (row.get("contract_award_unique_key") or "").strip():
                    blank_key_fallbacks += 1
                gid, search_rec, detail_rec = mapped
                action_date = (row.get("action_date") or "").strip()
                txn_key = (row.get("contract_transaction_unique_key") or "").strip()
                prior = best.get(gid)
                # Latest action_date wins (ISO dates sort lexically); same-date ties go to
                # the max contract_transaction_unique_key — never to file/row order.
                # KNOWN ASYMMETRY (adversarial review 2026-07-13, documented not fixed —
                # docs/ROADMAP.md): delete-marked rows (correction_delete_ind == "D",
                # delta files only, measured 100% blank today) still compete for the award
                # record here while the digest fold below skips them. An award whose ONLY
                # rows are retracted would become a digest-less candidate and fail the
                # validator's mod_count >= 1 invariant LOUDLY — the honest failure mode
                # until the award-level delete policy gets its own reviewed change.
                if prior is None or (action_date, txn_key) > (prior[0], prior[1]):
                    best[gid] = (action_date, txn_key, search_rec, detail_rec)
                # ── mods digest fold (parallel; never touches the award logic above).
                # Each distinct transaction folds once; a blank txn key folds
                # unconditionally (rare — mirrors the award-level blank-key fallback).
                if not txn_key or txn_key not in seen_txn_keys:
                    if txn_key:
                        seen_txn_keys.add(txn_key)
                    folded = fold_transaction(digests.get(gid), {
                        "award_id": gid,
                        "action_date": action_date,
                        "action_type_code": _code(row.get("action_type_code")),
                        "federal_action_obligation": _num(row.get("federal_action_obligation")),
                        "base_and_all_options_value": _num(row.get("base_and_all_options_value")),
                        "potential_total_value_of_award": _num(row.get("potential_total_value_of_award")),
                        "current_end_date": (row.get("period_of_performance_current_end_date") or "").strip() or None,
                        "potential_end_date": (row.get("period_of_performance_potential_end_date") or "").strip() or None,
                        "extent_competed_code": _code(row.get("extent_competed_code")),
                        "modification_number": (row.get("modification_number") or "").strip() or None,
                        "transaction_description": (row.get("transaction_description") or "").strip() or None,
                        "correction_delete_ind": _code(row.get("correction_delete_ind")),
                    })
                    if folded is not None:
                        digests[gid] = folded
        if blank_key_fallbacks:
            logger.warning(
                f"{path.name}: {blank_key_fallbacks} row(s) have the contract_award_unique_key "
                f"column but a blank value — kept under the bare-PIID fallback key, so distinct "
                f"awards may silently merge for those rows."
            )
        logger.info(f"USAspending bulk: scanned {scanned} rows so far ({len(best)} unique in-scope awards).")

    search_records = [v[2] for v in best.values()]
    detail_records = [v[3] for v in best.values()]
    logger.info(
        f"USAspending bulk export: {len(search_records)} unique awards kept "
        f"({len(naics_set)} NAICS) from {scanned} scanned transaction rows "
        f"({len(seen_txn_keys)} distinct transactions folded into {len(digests)} digests)."
    )
    return {"search_records": search_records, "detail_records": detail_records,
            "transaction_digests": digests}


def discover_bulk_csvs(bulk_globs) -> list:
    """Expand one glob or a list of globs to a sorted, de-duplicated list of CSVs.
    Accepts a single string or an iterable of strings."""
    if bulk_globs is None:
        return []
    if isinstance(bulk_globs, str):
        bulk_globs = [bulk_globs]
    found = set()
    for g in bulk_globs:
        found.update(glob.glob(str(g)))
    return sorted(found)
