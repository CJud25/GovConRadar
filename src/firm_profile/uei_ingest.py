"""
uei_ingest — pull a firm's own prime awards from USAspending for a given UEI and
normalize them into the frame ``company_profile.build_profile_from_awards`` consumes.

Network is injected, never hard-wired: ``fetch_awards_for_uei(uei, client)`` calls
``client.search_awards_by_recipient(uei)``. Tests pass a fake client, so the unit path
opens no socket (AC-9). ``LiveUsaspendingRecipientClient`` is the real network edge: one
retrying ``requests.Session`` reused across pages, a loud WARNING if a pull is truncated
at ``max_pages`` (never a silent cut), and a strict client-side ``Recipient UEI`` match
so text-search near-misses never leak into a firm's profile (see the class docstring for
the precision-vs-recall honesty note). ``requests`` is a hard dependency of the
project already (``api_clients.public_data`` imports it at module top), so it is imported
at module top here too.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Profile-input columns produced here (see company_profile input contract).
PROFILE_COLUMNS = ["naics", "psc", "awarding_agency", "total_obligated_amount", "place_of_performance_state"]

# USAspending spending_by_award display fields -> our profile-input columns.
_FIELD_MAP = {
    "naics": "naics_code",
    "psc": "psc_code",
    "awarding_agency": "Awarding Agency",
    "total_obligated_amount": "Award Amount",
    "place_of_performance_state": "Place of Performance State Code",
}


class RecipientAwardsClient(Protocol):
    """The one method uei_ingest needs. Any object providing it can be injected —
    a fake in tests, ``LiveUsaspendingRecipientClient`` in production."""

    def search_awards_by_recipient(self, uei: str) -> list[dict[str, Any]]: ...


def _award_records_to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Map raw USAspending award records to the profile-input frame. Order-preserving
    (=> deterministic). Missing fields become None; the profile builder tolerates them."""
    rows = [{col: rec.get(src) for col, src in _FIELD_MAP.items()} for rec in records]
    return pd.DataFrame(rows, columns=PROFILE_COLUMNS)


def fetch_awards_for_uei(uei: str, client: RecipientAwardsClient) -> pd.DataFrame:
    """Pull ``uei``'s awards via the injected client and normalize them. Deterministic
    for a given client response; opens no socket of its own (the client owns any I/O)."""
    records = client.search_awards_by_recipient(uei)
    return _award_records_to_frame(records)


class LiveUsaspendingRecipientClient:
    """Network edge: queries USAspending's spending_by_award endpoint filtered to one
    recipient (UEI). NOT unit-tested against the live API (that needs a real socket); the
    injectable Protocol above is what tests exercise.

    Hardened for the real network: one ``requests.Session`` reused across pages
    (connection pooling), with retry + exponential backoff on transient failures
    (connection errors and 5xx). If the pull stops because it hit ``max_pages`` while the
    API still reported another page, a WARNING is logged — never a silent truncation.
    A pre-built ``session`` can be injected (tests use a fake; no socket).

    Strict-UEI post-filter: the server-side ``recipient_search_text`` filter is a FUZZY
    text match over recipient name and UEI, so awards from similarly-named recipients can
    come back. Each record's ``Recipient UEI`` is therefore checked client-side against
    the requested UEI (``.strip().upper()`` on both sides); non-matching records — and
    records with a missing or blank ``Recipient UEI``, which cannot be attributed — are
    dropped and counted, with one summary WARNING per pull when anything was dropped.
    Honesty limit: this fixes PRECISION only, not RECALL — the server-side text search
    may still MISS awards that genuinely belong to the UEI, and no client-side filter
    can recover records the API never returned.
    """

    SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
    FIELDS = [
        "Award ID",
        "Recipient UEI",
        "naics_code",
        "psc_code",
        "Awarding Agency",
        "Award Amount",
        "Place of Performance State Code",
    ]
    # Transient server-side statuses worth retrying; 4xx are caller errors and fail fast.
    RETRY_STATUSES = (500, 502, 503, 504)

    def __init__(
        self,
        max_pages: int = 50,
        page_size: int = 100,
        timeout: int = 60,
        session: requests.Session | None = None,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self.max_pages = max_pages
        self.page_size = page_size
        self.timeout = timeout
        self._session = session if session is not None else self._build_session(max_retries, backoff_factor)

    @classmethod
    def _build_session(cls, max_retries: int, backoff_factor: float) -> requests.Session:
        """A Session retrying connection errors and transient 5xx with exponential backoff
        (urllib3 ships with requests — no new dependency). POST must be allowed explicitly:
        urllib3 excludes it by default because POST is not idempotent in general, but this
        endpoint is a read-only search, so retrying it is safe."""
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=cls.RETRY_STATUSES,
            allowed_methods=frozenset({"POST"}),
        )
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def search_awards_by_recipient(self, uei: str) -> list[dict[str, Any]]:
        wanted_uei = uei.strip().upper()
        results: list[dict[str, Any]] = []
        dropped = 0
        filters: dict[str, Any] = {
            "award_type_codes": ["A", "B", "C", "D"],
            "recipient_search_text": [uei],
        }
        for page in range(1, self.max_pages + 1):
            payload: dict[str, Any] = {
                "filters": filters,
                "fields": self.FIELDS,
                "page": page,
                "limit": self.page_size,
                "sort": "Award Amount",
                "order": "desc",
            }
            resp = self._session.post(self.SEARCH_URL, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("results", [])
            if not batch:
                break
            # Strict post-filter: keep only records whose Recipient UEI equals the
            # requested UEI (normalized both sides). Missing/blank UEIs cannot be
            # attributed to the firm, so they are dropped and counted — never kept.
            for rec in batch:
                raw_uei = rec.get("Recipient UEI")
                normalized = raw_uei.strip().upper() if isinstance(raw_uei, str) else ""
                if normalized and normalized == wanted_uei:
                    results.append(rec)
                else:
                    dropped += 1
            if not data.get("page_metadata", {}).get("hasNext", False):
                break
        else:
            # for/else: only reached when every page up to max_pages had results AND
            # reported hasNext — i.e. the pull stopped because of the cap, not the data.
            # Honesty rule: never truncate silently, and name the bias the cut introduces.
            logger.warning(
                "USAspending pull for UEI %s truncated at max_pages=%d with more pages remaining "
                "(%d awards kept). Results are sorted by Award Amount descending, so truncation "
                "systematically drops the smallest awards and biases the profile's "
                "max_comfortable_contract_value percentile upward.",
                uei,
                self.max_pages,
                len(results),
            )
        if dropped:
            # Honesty rule: never exclude silently, and name the bias the filter removes.
            logger.warning(
                "USAspending pull for UEI %s: recipient_search_text is a text match and can "
                "include similarly-named recipients; %d non-matching/unattributable records "
                "excluded (%d exact-UEI matches kept). Note this fixes precision only — the "
                "text search may still miss awards that belong to the UEI.",
                uei,
                dropped,
                len(results),
            )
        return results
