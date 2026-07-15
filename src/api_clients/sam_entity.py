"""
sam_entity — a firm's SAM.gov certifications (8(a), SDVOSB/VOSB, WOSB/EDWOSB, HUBZone,
size standards), normalized into ``EntityCerts`` for the eligibility gate.

Honesty posture (product rule: facts vs estimates):
  * Network is injected. ``fetch_entity(uei, client)`` calls a client that returns already
    NORMALIZED cert fields, so this module never depends on SAM's raw JSON field names.
    Tests inject a fake client => no socket (AC-11).
  * ``LiveSamEntityClient`` is the real edge — it calls entity-information/v3 with a
    ``SAM_GOV_API_KEY`` and normalizes the response via the pure ``parse_entity_certs``
    (field mapping verified against the live v3 API 2026-07-06). No key => raises cleanly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

import requests

SAM_ENTITY_URL = "https://api.sam.gov/entity-information/v3/entities"


class SamEntityUnavailable(RuntimeError):
    """The live SAM Entity API cannot be used yet: no API key, or the v3 cert field
    mapping is not human-confirmed. Callers should surface this, never fabricate certs."""


@dataclass(frozen=True)
class EntityCerts:
    """Normalized set-aside certifications for one firm (the eligibility gate's input)."""

    uei: str
    is_8a: bool = False
    program_exit_date_8a: date | None = None
    is_sdvosb: bool = False
    is_vosb: bool = False
    is_wosb: bool = False
    is_edwosb: bool = False
    is_hubzone: bool = False
    size_standard_by_naics: dict[str, str] = field(default_factory=dict)


class SamEntityClient(Protocol):
    """Returns NORMALIZED cert fields for a UEI (SAM's raw-JSON parsing is the client's
    job, so this module stays field-name-agnostic). A fake satisfies it in tests."""

    def get_entity_certs(self, uei: str) -> dict[str, Any]: ...


def _coerce_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def fetch_entity(uei: str, client: SamEntityClient) -> EntityCerts:
    """Fetch + normalize a firm's certifications via the injected client. Opens no socket
    of its own; deterministic for a given client response."""
    raw = client.get_entity_certs(uei)
    sizes = raw.get("size_standard_by_naics") or {}
    return EntityCerts(
        uei=uei,
        is_8a=bool(raw.get("is_8a", False)),
        program_exit_date_8a=_coerce_date(raw.get("program_exit_date_8a")),
        is_sdvosb=bool(raw.get("is_sdvosb", False)),
        is_vosb=bool(raw.get("is_vosb", False)),
        is_wosb=bool(raw.get("is_wosb", False)),
        is_edwosb=bool(raw.get("is_edwosb", False)),
        is_hubzone=bool(raw.get("is_hubzone", False)),
        size_standard_by_naics={str(k): str(v) for k, v in sizes.items()},
    )


def parse_entity_certs(business_types: dict[str, Any]) -> dict[str, Any]:
    """Map a SAM ``coreData.businessTypes`` block to normalized cert fields.

    Matches by DESCRIPTION substring (stable across code changes) over both the
    SBA-certified list (authoritative; carries the 8(a) ``certificationExitDate``) and the
    self-attested list. Verified against the live entity-information/v3 API on 2026-07-06:
    ``sbaBusinessTypeList`` entries look like {sbaBusinessTypeCode, sbaBusinessTypeDesc,
    certificationEntryDate, certificationExitDate}, e.g. A6='SBA Certified 8(a) Program
    Participant', XX='SBA Certified HUBZone Firm'. Joint-venture entries are ignored (a JV is
    a distinct entity, not the firm's own certification).
    """
    certs: dict[str, Any] = {
        "is_8a": False,
        "program_exit_date_8a": None,
        "is_sdvosb": False,
        "is_vosb": False,
        "is_wosb": False,
        "is_edwosb": False,
        "is_hubzone": False,
        "size_standard_by_naics": {},  # lives in the `assertions` section — out of scope for v0
    }
    sba = business_types.get("sbaBusinessTypeList") or []
    self_attested = business_types.get("businessTypeList") or []

    for entry in [*sba, *self_attested]:
        desc = str(entry.get("sbaBusinessTypeDesc") or entry.get("businessTypeDesc") or "").lower()
        if "joint venture" in desc:
            continue
        if "8(a)" in desc:
            certs["is_8a"] = True
            exit_date = _coerce_date(entry.get("certificationExitDate"))
            if exit_date is not None:
                certs["program_exit_date_8a"] = exit_date
        if "hubzone" in desc:
            certs["is_hubzone"] = True
        if "women" in desc:
            certs["is_wosb"] = True
            if "economically disadvantaged" in desc:
                certs["is_edwosb"] = True
        if "service-disabled veteran" in desc or "service disabled veteran" in desc:
            certs["is_sdvosb"] = True
        elif "veteran-owned" in desc or "veteran owned" in desc:
            certs["is_vosb"] = True
    return certs


class LiveSamEntityClient:
    """Network edge for SAM.gov Entity Management (``entity-information/v3``).

    Queries the firm's entity by UEI and normalizes its certifications via
    ``parse_entity_certs``. Requires ``SAM_GOV_API_KEY`` (env or constructor); with no key it
    raises ``SamEntityUnavailable`` rather than pretend. The field mapping was verified
    against the live v3 API (see ``parse_entity_certs``). Not exercised in the unit suite —
    the pure parser is tested offline with a captured response; this class is the thin edge.
    """

    def __init__(self, api_key: str | None = None, timeout: int = 30) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("SAM_GOV_API_KEY", "")
        self.timeout = timeout

    def get_entity_certs(self, uei: str) -> dict[str, Any]:
        if not self.api_key:
            raise SamEntityUnavailable("SAM_GOV_API_KEY not set — cannot query the SAM Entity API (see .env.example).")
        resp = requests.get(
            SAM_ENTITY_URL,
            params={"api_key": self.api_key, "ueiSAM": uei, "includeSections": "coreData"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if "entityData" not in data:
            # SAM returns a {code, message, nextAccessTime} body on rate-limit/errors, often
            # with HTTP 200 — raise loudly rather than misread a throttle as "firm has no certs".
            raise SamEntityUnavailable(f"SAM Entity API did not return entityData: {data.get('message') or data}")
        records = data.get("entityData") or []
        if not records:
            return {}  # not registered / not found -> no certs (all default to False)
        business_types = (records[0].get("coreData", {}) or {}).get("businessTypes", {}) or {}
        return parse_entity_certs(business_types)
