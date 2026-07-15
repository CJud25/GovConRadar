"""
fetch_public_data.py — Pull live data from USAspending.gov (primary, no key
required) and SAM.gov (client built, but api.sam.gov is unreachable from this
build sandbox — see documentation/data_acquisition_plan.md).
"""

import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def _request_with_retry(method: str, url: str, max_retries: int = 5, timeout: int = 60,
                        max_backoff: int = 30, **kwargs):
    """Retries on connection errors/timeouts and 5xx responses with exponential
    backoff (capped at max_backoff). Raises immediately on 4xx — those are config
    errors, not transient. USAspending intermittently returns 503 under load, so
    the default retry count is generous enough that a momentary blip on one of the
    several searches per run doesn't discard the whole extraction."""
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt == max_retries:
                logger.error(f"{method} {url} failed after {max_retries} attempts: {e}")
                raise
            wait = min(2 ** attempt, max_backoff)
            logger.warning(f"{method} {url} attempt {attempt} failed ({e}); retrying in {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code >= 500:
            if attempt == max_retries:
                logger.error(f"{method} {url} failed after {max_retries} attempts: HTTP {resp.status_code}")
                resp.raise_for_status()
            wait = min(2 ** attempt, max_backoff)
            logger.warning(f"{method} {url} attempt {attempt} got {resp.status_code}; retrying in {wait}s")
            time.sleep(wait)
            continue

        resp.raise_for_status()  # raises immediately for 4xx, no retry
        return resp
    raise last_exc


def save_raw_pull(records: list, source_system: str, source_endpoint: str,
                   params: dict, out_dir: Path) -> Path:
    """Wraps raw API results in a source-metadata envelope and writes a
    date/time-stamped JSON file. Never overwrites a prior pull."""
    pulled_at = datetime.now(timezone.utc)
    envelope = {
        "source_system": source_system,
        "source_endpoint": source_endpoint,
        "api_parameters": params,
        "pull_timestamp_utc": pulled_at.isoformat(),
        "record_count": len(records),
        "records": records,
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    system_slug = source_system.lower().replace(" ", "_").replace(".", "")
    endpoint_slug = source_endpoint.lower().replace(" ", "_").replace("/", "_").replace(".", "")
    filename = f"{system_slug}_{endpoint_slug}_{pulled_at.strftime('%Y-%m-%d_%H%M%S_%f')}.json"
    out_path = out_dir / filename
    with open(out_path, "w") as f:
        json.dump(envelope, f, indent=2, default=str)
    return out_path


# ─── USASPENDING.GOV ──────────────────────────────────────────────────────────

USASPENDING_SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
USASPENDING_DETAIL_URL = "https://api.usaspending.gov/api/v2/awards/{}/"
USASPENDING_RATE_DELAY = 0.3

# Verified working field names as of 2026-07-01 — see Global Constraints in the plan.
USASPENDING_SEARCH_FIELDS = [
    "Award ID", "Recipient Name", "recipient_id", "Recipient UEI",
    "Start Date", "End Date", "Award Amount", "Total Outlays",
    "Awarding Agency", "Awarding Sub Agency", "Funding Agency", "Funding Sub Agency",
    "Contract Award Type", "naics_code", "naics_description", "psc_code",
    "Description", "Last Modified Date", "Base Obligation Date",
    "Place of Performance State Code", "generated_internal_id", "def_codes",
]


def fetch_usaspending_awards(
    naics_codes: list, dod_toptier_agency: str, keywords: list,
    modified_since: date, modified_before: date,
    max_pages: int = 50, page_size: int = 100,
) -> list:
    """
    Searches USAspending's spending_by_award endpoint for DoD contract awards.

    NOTE: USAspending's time_period filter has no date_type for period-of-performance
    end dates — only action_date / date_signed / last_modified_date (confirmed:
    passing an unsupported date_type returns HTTP 500). This function filters on
    last_modified_date, which means contracts with no obligation/modification
    activity in [modified_since, modified_before] will NOT appear even if still
    active. This is a documented, deliberate scope limitation.
    """
    all_results = []
    seen_ids = set()
    filters = {
        "award_type_codes": ["A", "B", "C", "D"],
        "agencies": [{"type": "awarding", "tier": "toptier", "name": dod_toptier_agency}],
        "naics_codes": naics_codes,
        "time_period": [{
            "start_date": modified_since.strftime("%Y-%m-%d"),
            "end_date": modified_before.strftime("%Y-%m-%d"),
            "date_type": "last_modified_date",
        }],
    }
    # USAspending's keyword filter rejects terms shorter than 3 characters with
    # HTTP 422 (e.g. "IA"), so drop sub-3-char keywords before sending. They are
    # still used for local keyword classification; this only scopes the API search.
    api_keywords = [k for k in keywords if len(k) >= 3]
    if api_keywords:
        filters["keywords"] = api_keywords

    for page in range(1, max_pages + 1):
        payload = {
            "filters": filters, "fields": USASPENDING_SEARCH_FIELDS,
            "page": page, "limit": page_size, "sort": "Award Amount", "order": "desc",
        }
        resp = _request_with_retry("POST", USASPENDING_SEARCH_URL, json=payload, timeout=60)
        data = resp.json()
        batch = data.get("results", [])
        if not batch:
            break
        for record in batch:
            gid = record.get("generated_internal_id")
            if gid and gid not in seen_ids:
                seen_ids.add(gid)
                all_results.append(record)
        logger.info(f"USAspending page {page}: {len(batch)} records ({len(all_results)} unique so far)")
        if not data.get("page_metadata", {}).get("hasNext", False):
            break
        time.sleep(USASPENDING_RATE_DELAY)

    logger.info(f"USAspending search complete: {len(all_results)} unique awards.")
    return all_results


def fetch_usaspending_award_detail(generated_internal_id: str) -> dict:
    """Hydrates one award with full period_of_performance (incl. potential_end_date),
    extent_competed, and parent award info via the award detail endpoint."""
    url = USASPENDING_DETAIL_URL.format(generated_internal_id)
    resp = _request_with_retry("GET", url, timeout=30)
    return resp.json()


# ─── SAM.GOV (client built; unreachable from this build sandbox) ────────────

SAM_BASE_URL = "https://api.sam.gov/opportunities/v2/search"
SAM_BATCH_SIZE = 100
SAM_RATE_DELAY = 0.5


def _split_into_calendar_years(start: date, end: date) -> list:
    """SAM.gov v2 requires postedFrom/postedTo within the same calendar year."""
    windows = []
    current = start
    while current <= end:
        year_end = date(current.year, 12, 31)
        window_end = min(year_end, end)
        windows.append((current, window_end))
        current = date(current.year + 1, 1, 1)
    return windows


def fetch_sam_opportunities(
    api_key: str, naics_codes: list, posted_from: date, posted_to: date,
    max_records: int = 500,
) -> list:
    """
    Fetches SAM.gov contract opportunity notices. Returns [] and logs rather than
    raising if api_key is empty OR the connection fails — this client must degrade
    gracefully so the rest of the pipeline runs correctly with zero SAM.gov data.
    """
    if not api_key:
        logger.warning("SAM_GOV_API_KEY not set — skipping SAM.gov fetch (pipeline continues with zero opportunity records).")
        return []

    all_results = []
    for window_from, window_to in _split_into_calendar_years(posted_from, posted_to):
        if len(all_results) >= max_records:
            break
        params = {
            "api_key": api_key, "limit": SAM_BATCH_SIZE, "offset": 0,
            "naicsCode": ",".join(naics_codes),
            "postedFrom": window_from.strftime("%m/%d/%Y"),
            "postedTo": window_to.strftime("%m/%d/%Y"),
        }
        offset = 0
        while len(all_results) < max_records:
            params["offset"] = offset
            try:
                resp = _request_with_retry("GET", SAM_BASE_URL, params=params, timeout=120)
            except requests.exceptions.RequestException as e:
                logger.error(f"SAM.gov unreachable ({e}) — pipeline continues with zero opportunity records.")
                return all_results
            data = resp.json()
            batch = data.get("opportunitiesData", [])
            if not batch:
                break
            all_results.extend(batch)
            total_records = data.get("totalRecords", 0)
            if offset + SAM_BATCH_SIZE >= min(total_records, max_records):
                break
            offset += SAM_BATCH_SIZE
            time.sleep(SAM_RATE_DELAY)

    logger.info(f"SAM.gov fetch complete: {len(all_results)} opportunities.")
    return all_results[:max_records]
