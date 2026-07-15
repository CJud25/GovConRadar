"""
eligibility_lane — the categorical prime-path lane over the set-aside gate (pure).

Maps one candidate (+ optionally its live linked notice's set-aside code) and the firm's
attested certifications to a single ``LaneVerdict``: **gate / warn / unknown / clear**. The
lane is a LABEL, never a score component — it is never blended into ``pursuit_score`` or any
tier, and the renderer says so. Gate logic is reused byte-for-byte from the dormant
``scoring.eligibility.eligibility()`` (which stays untouched); this module only decides which
evidence path applies and translates the gate's verdict into surface language.

Honesty posture:
  * Blank ≠ NONE. A blank FPDS set-aside code means "not reported" (unknown), while the NONE
    family is an affirmative full-and-open record (clear). Most order-level records simply
    don't report a set-aside.
  * A live solicitation's NON-OPEN set-aside (notice path) outranks the expiring contract's
    historical code — the CO's published restriction is the real one. A notice reporting the
    NONE family falls through to the historical path (an affirmed open competition restricts
    nobody, so there is nothing to gate). Historical codes only ever *warn*.
  * No profile ⇒ unknown, never a guess. Certifications are self-attested and never verified.
  * Deterministic: injected ``today`` (8(a) exit dates), no I/O, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from api_clients.sam_entity import EntityCerts
from scoring.eligibility import eligibility
from utils.coerce import nan_str

LANE_STATES = ("gate", "warn", "unknown", "clear")

# The verdict's evidence basis (which path produced it).
LANE_BASES = ("notice_confirmed", "historical_fpds", "not_reported", "no_profile")

# Restated from scoring.eligibility._OPEN_CODES (mirror test pins agreement): the non-blank
# members are the NONE family — an AFFIRMED absence of a set-aside, distinct from blank.
NONE_FAMILY = frozenset({"NONE", "NO SET ASIDE USED", "N/A", "NA"})

TEAMING_REFRAME = (
    "Ineligible as prime is not a dead end — a subcontract, joint-venture, or SBA "
    "mentor-protégé path stays open. Consider teaming with a certified prime."
)
BLANK_NOT_NONE = (
    "Blank does not mean unrestricted — most order-level records simply don't report a "
    "set-aside. The real decision is made, and published, at the recompete solicitation."
)
ATTESTED_NOTE = "Based on the certifications you attested in your profile — the radar never verifies certifications."

_CONFIRM_SENTENCE = "Size, affiliation, and program status still apply — confirm at the solicitation."
_CO_SENTENCE = (
    "The contracting officer sets the recompete's set-aside strategy fresh at solicitation — "
    "a historical code is a caution, never a verdict."
)
_ADD_CERTS = "Add your certifications on the Your Company page to check whether you can prime this."

# Reason fragments the gate emits when it INCLUDED a firm without confirming eligibility
# (unverified size / unrecognized code) — those verdicts stay in the unknown lane.
_UNCONFIRMED_FRAGMENTS = ("not verified", "not recognized")


@dataclass(frozen=True)
class LaneVerdict:
    """One categorical prime-path verdict. Raw text throughout — the app adapter
    (the render boundary) html.escapes every token."""

    state: str  # one of LANE_STATES
    headline: str
    detail: str  # evidence sentence: code, source, and the gate's reason
    teaming: str | None  # ALWAYS set when state == "gate"; set on a failing "warn"
    basis: str  # one of LANE_BASES
    source_code: str  # the set-aside code examined ("" when blank)


def _label(code: str, text: object) -> str:
    """Human set-aside label: the record's descriptive text when present, else the code."""
    t = nan_str(text)
    return t if t else code


def _unconfirmed(reason: str) -> bool:
    return any(f in reason for f in _UNCONFIRMED_FRAGMENTS)


def lane_verdict(
    candidate: Mapping[str, object],
    entity: EntityCerts | None,
    today: date,
    *,
    notice_code: str | None = None,
    notice_label: str = "",
) -> LaneVerdict:
    """Decide the lane for one candidate. ``notice_code`` (when the caller found a live,
    High-confidence linked notice reporting a set-aside) takes the notice path — the CO's
    published decision; otherwise the historical FPDS code only ever warns."""
    # ---- notice path (rows 1-4): a live solicitation with a non-open set-aside ----
    n_code = nan_str(notice_code).upper() if notice_code is not None else ""
    if n_code and n_code not in NONE_FAMILY:
        label = _label(n_code, notice_label)
        source = f"Source: live linked SAM.gov notice, set-aside code {n_code}."
        if entity is None:  # row 4
            return LaneVerdict(
                state="unknown",
                headline=f"{label} set-aside on the live solicitation — eligibility not checked",
                detail=f"{source} {_ADD_CERTS}",
                teaming=None,
                basis="no_profile",
                source_code=n_code,
            )
        verdict = eligibility({"type_of_set_aside_code": notice_code, "naics": candidate.get("naics")}, entity, today)
        reason = verdict["reason"]
        if not verdict["eligible"]:  # row 1
            return LaneVerdict(
                state="gate",
                headline=f"Ineligible as prime — {label} set-aside on the live solicitation",
                detail=f"{source} {reason}.",
                teaming=TEAMING_REFRAME,
                basis="notice_confirmed",
                source_code=n_code,
            )
        if _unconfirmed(reason):  # row 2
            return LaneVerdict(
                state="unknown",
                headline=f"Eligibility unknown — {label} set-aside on the live solicitation",
                detail=f"{source} {reason}.",
                teaming=None,
                basis="notice_confirmed",
                source_code=n_code,
            )
        return LaneVerdict(  # row 3
            state="clear",
            headline=f"Eligible to prime — your attested certifications match the {label} set-aside on the live solicitation",
            detail=f"{source} {reason}. {_CONFIRM_SENTENCE}",
            teaming=None,
            basis="notice_confirmed",
            source_code=n_code,
        )

    # ---- historical path (rows 5-10): the expiring contract's FPDS code ----
    code = nan_str(candidate.get("type_of_set_aside_code")).upper()
    if code == "":  # row 5 — blank is NOT the NONE family
        return LaneVerdict(
            state="unknown",
            headline="Set-aside not reported on the expiring contract",
            detail=BLANK_NOT_NONE,
            teaming=None,
            basis="not_reported",
            source_code="",
        )
    label = _label(code, candidate.get("type_of_set_aside"))
    source = f"Source: historical FPDS record, set-aside code {code}."
    if code in NONE_FAMILY:  # row 6 — affirmed full-and-open
        return LaneVerdict(
            state="clear",
            headline="No set-aside on the expiring contract — competed full-and-open",
            detail=f"{source} The record affirms no set-aside was used. {_CONFIRM_SENTENCE}",
            teaming=None,
            basis="historical_fpds",
            source_code=code,
        )
    if entity is None:  # row 7
        return LaneVerdict(
            state="unknown",
            headline=f"{label} set-aside on the expiring contract — eligibility not checked",
            detail=f"{source} {_ADD_CERTS}",
            teaming=None,
            basis="no_profile",
            source_code=code,
        )
    verdict = eligibility(dict(candidate), entity, today)
    reason = verdict["reason"]
    if not verdict["eligible"]:  # row 8 — failing warn carries the teaming path
        return LaneVerdict(
            state="warn",
            headline=f"Set-aside caution — the expiring contract was {label}",
            detail=f"{source} {reason}. {_CO_SENTENCE}",
            teaming=TEAMING_REFRAME,
            basis="historical_fpds",
            source_code=code,
        )
    if _unconfirmed(reason):  # row 9
        return LaneVerdict(
            state="unknown",
            headline=f"Eligibility unknown — the expiring contract was {label}",
            detail=f"{source} {reason}.",
            teaming=None,
            basis="historical_fpds",
            source_code=code,
        )
    return LaneVerdict(  # row 10
        state="clear",
        headline=f"Eligible to prime — your attested certifications match the {label} set-aside on the expiring contract",
        detail=f"{source} {reason}. {_CONFIRM_SENTENCE}",
        teaming=None,
        basis="historical_fpds",
        source_code=code,
    )
