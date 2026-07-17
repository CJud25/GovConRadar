"""
eligibility — set-aside eligibility gate v0 (pure).

Maps an incumbent contract's FPDS ``type_of_set_aside_code`` to the firm's held
certifications (``EntityCerts``) and returns ``{eligible, reason}``. Never silently drops a
candidate: an unknown or blank incumbent set-aside is ``eligible=True`` with a stated reason,
and an unrecognized code is included pending review (transparency over false exclusion).
The 8(a) program-exit date is honored against an injected ``today`` (determinism).
"""

from __future__ import annotations

from datetime import date
from typing import TypedDict

import pandas as pd

from api_clients.sam_entity import EntityCerts
from utils.coerce import clean_code, nan_str

ELIGIBILITY_COLUMNS = ["eligible", "eligibility_reason"]


class EligibilityResult(TypedDict):
    eligible: bool
    reason: str


_OPEN_CODES = {"", "NONE", "NO SET ASIDE USED", "N/A", "NA"}


def _result(eligible: bool, reason: str) -> EligibilityResult:
    return {"eligible": eligible, "reason": reason}


def _yn(ok: bool, label: str, yes: str, no: str) -> EligibilityResult:
    return _result(ok, f"{label}; {yes if ok else no}")


def eligibility(candidate: dict[str, object], entity: EntityCerts, today: date) -> EligibilityResult:
    """Return whether ``entity`` (the firm) is eligible for the incumbent's set-aside."""
    code = nan_str(candidate.get("type_of_set_aside_code")).upper()
    if code in _OPEN_CODES:
        return _result(True, "no set-aside restriction (or not reported)")

    if code.startswith("8A"):
        if not entity.is_8a:
            return _result(False, "8(a) set-aside; firm is not 8(a)-certified")
        exit_date = entity.program_exit_date_8a
        if exit_date is not None and today > exit_date:
            return _result(False, f"8(a) set-aside; firm exited the 8(a) program on {exit_date.isoformat()}")
        return _result(True, "8(a) set-aside; firm is 8(a)-certified")

    if code.startswith("HZ") or "HUBZONE" in code:
        return _yn(entity.is_hubzone, "HUBZone set-aside", "firm is HUBZone-certified", "firm is not HUBZone-certified")

    if code.startswith("SDVOSB"):
        return _yn(entity.is_sdvosb, "SDVOSB set-aside", "firm is SDVOSB-certified", "firm is not SDVOSB-certified")

    if code.startswith("EDWOSB"):  # check before WOSB
        return _yn(entity.is_edwosb, "EDWOSB set-aside", "firm is EDWOSB-certified", "firm is not EDWOSB-certified")

    if code.startswith("WOSB"):  # an EDWOSB firm also qualifies for a WOSB set-aside
        return _yn(
            entity.is_wosb or entity.is_edwosb,
            "WOSB set-aside",
            "firm is WOSB/EDWOSB-certified",
            "firm is not WOSB-certified",
        )

    if code.startswith("VOSB") or code in {"VSA", "VSS"}:  # an SDVOSB firm also qualifies for a VOSB set-aside
        return _yn(
            entity.is_vosb or entity.is_sdvosb,
            "VOSB set-aside",
            "firm is VOSB/SDVOSB-certified",
            "firm is not VOSB-certified",
        )

    if code.startswith("SB"):  # total (SBA) / partial (SBP) small-business set-aside
        naics = clean_code(candidate.get("naics"))
        size = entity.size_standard_by_naics.get(naics)
        if size is None:
            return _result(True, f"small-business set-aside; firm size for NAICS {naics or 'unknown'} not verified")
        if size.lower() == "small":
            return _result(True, f"small-business set-aside; firm is small for NAICS {naics}")
        return _result(False, f"small-business set-aside; firm is not small for NAICS {naics}")

    return _result(True, f"set-aside code '{code}' not recognized by the gate; included pending review")


def annotate_eligibility(df: pd.DataFrame, entity: EntityCerts, today: date) -> pd.DataFrame:
    """Return a COPY of the candidate frame with two columns added — ``eligible`` (bool) and
    ``eligibility_reason`` (str), one ``eligibility()`` call per row. Never drops a row: the
    knockout is transparent and the reason travels with the candidate (§5.3)."""
    out = df.copy()
    results = [eligibility(row, entity, today) for row in out.to_dict("records")]
    out["eligible"] = [r["eligible"] for r in results]
    out["eligibility_reason"] = [r["reason"] for r in results]
    return out


def eligible_view(df: pd.DataFrame, include_ineligible: bool = False) -> pd.DataFrame:
    """Explicit filter to eligible rows (a copy). ``include_ineligible=True`` returns every
    row with the annotation intact — nothing is hidden, it is one toggle away. Requires
    ``annotate_eligibility`` to have run."""
    if include_ineligible:
        return df.copy()
    if "eligible" not in df.columns:
        raise KeyError("eligible_view requires annotate_eligibility() to have added an 'eligible' column")
    return df[df["eligible"].astype(bool)].copy()
