"""
sam_bulk.py — Load SAM.gov Contract Opportunities from the bulk CSV export
(ContractOpportunitiesFullCSV.csv) instead of the live API.

The public SAM.gov "Contract Opportunities" full export is the same underlying
data as the api.sam.gov Opportunities API but requires no API key, has no rate
limits, and no transient outages. This loader reads it, scopes it to the DoD +
cyber/IT-NAICS slice the rest of the pipeline cares about, and normalizes it to
the snake_case record shape build_opportunities_clean and the
fact_opportunity_notices table expect.

Records are returned with normalized keys (not the raw CSV headers) so the
downstream transform layer is agnostic to whether the notices came from the API
or the bulk file.
"""

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Raise the field-size limit: some Description cells are very large.
csv.field_size_limit(10_000_000)

# CSV header -> normalized field. Order/names of the normalized keys line up with
# powerbi_export.EMPTY_SCHEMAS["fact_opportunity_notices"] (minus pull_timestamp_utc,
# which is stamped by the caller at extraction time).
_COLUMN_MAP = {
    "NoticeId": "notice_id",
    "Sol#": "solicitation_number",
    "Title": "title",
    "Type": "notice_type",
    "PostedDate": "posted_date",
    "ResponseDeadLine": "response_deadline",
    "ArchiveDate": "archive_date",
    "Department/Ind.Agency": "agency",
    "Sub-Tier": "subagency",
    "Office": "office",
    "NaicsCode": "naics",
    "ClassificationCode": "psc",
    "SetASide": "set_aside",          # export header is "SetASide" — a "SetAside" key never matched it
    "SetASideCode": "set_aside_code",  # machine code ("SBA", "8A", "SDVOSBC", ...)
    "PopState": "place_of_performance_state",
    "Link": "source_url",
}


def _iso_date(value: str):
    """Best-effort normalize the assorted SAM date formats to a bare YYYY-MM-DD.

    The export mixes 'YYYY-MM-DD HH:MM:SS.fff-04', 'YYYY-MM-DDThh:mm:ss-07:00',
    and plain 'YYYY-MM-DD'. All three start with the ISO calendar date, so the
    leading 10 characters are the date; anything that doesn't parse becomes ''.
    """
    if not value:
        return ""
    text = str(value).strip()
    head = text[:10]
    if len(head) == 10 and head[4] == "-" and head[7] == "-":
        return head
    return ""


def load_sam_opportunities_from_csv(
    csv_path, naics_codes, dod_only: bool = True, dod_agency_marker: str = "DEFENSE",
) -> list:
    """Read the bulk Contract Opportunities export and return normalized notice
    records scoped to the given NAICS codes (and, by default, DoD only).

    Returns [] and logs a warning if the file is absent, so the pipeline degrades
    gracefully exactly like the API client does.
    """
    path = Path(csv_path)
    if not path.exists():
        logger.warning(f"SAM.gov bulk export not found at {path} — skipping (pipeline continues with zero opportunity records).")
        return []

    naics_set = {str(c) for c in naics_codes}
    records = []
    scanned = 0
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scanned += 1
            naics = (row.get("NaicsCode") or "").strip()
            if naics not in naics_set:
                continue
            if dod_only and dod_agency_marker not in (row.get("Department/Ind.Agency") or "").upper():
                continue
            rec = {norm: (row.get(src) or "").strip() for src, norm in _COLUMN_MAP.items()}
            rec["posted_date"] = _iso_date(rec["posted_date"])
            rec["response_deadline"] = _iso_date(rec["response_deadline"])
            rec["archive_date"] = _iso_date(rec["archive_date"])
            records.append(rec)

    logger.info(
        f"SAM.gov bulk export: {len(records)} notices kept "
        f"({'DoD-only, ' if dod_only else ''}{len(naics_set)} NAICS) from {scanned} scanned rows."
    )
    return records
