"""
notice_lens — pure classifier + filter for the Sources Sought / RFI early-warning lane (E1).

Answers one capture question at render time: *of the SAM.gov notices already sitting in
``fact_opportunity_notices``, which ones are still in the shapeable pre-solicitation window?*
Sources Sought and Request for Information notices are the government thinking out loud
before requirements harden; Presolicitation is DELIBERATELY excluded — it lands later in the
shaping window, after most of the capture value has already been spent by someone else.

No I/O, no clock, no live SAM API call: this module only classifies and filters rows that are
ALREADY in the baked star schema. Its output is only as fresh as the last SAM bulk-export
refresh (``SAM.gov data/ContractOpportunitiesFullCSV.csv``) that produced
``fact_opportunity_notices`` — see ``docs/data_acquisition_plan.md`` and
``docs/SOP_Recompete_Radar_v2.1.md`` for the refresh prerequisite. Mirrors the house idiom of
``src/scoring/burn_pressure.py`` / ``src/scoring/successor_proxy.py``.
"""

from __future__ import annotations

import math
import re

import pandas as pd

# The two required columns for the lens; a bundle missing either degrades to an honest
# empty frame rather than raising (Noether pin: no fabricated notices).
_REQUIRED_COLUMNS: tuple[str, ...] = ("notice_type", "posted_date")

_SOURCES_SOUGHT = "sources sought"
_RFI_PHRASE = "request for information"
_RFI_TOKEN_RE = re.compile(r"\brfi\b")


def is_sources_sought_or_rfi(notice_type: object) -> bool:
    """Case-insensitive classifier: True iff ``notice_type`` contains "sources sought" or
    "request for information", or carries the exact word "rfi" (word-boundary match, so it
    never false-positives inside an unrelated token). None / NaN / blank -> False.

    Presolicitation is DELIBERATELY excluded (see module docstring) — do not add it here."""
    if notice_type is None:
        return False
    if isinstance(notice_type, float) and math.isnan(notice_type):  # np.float64 subclasses float
        return False
    text = str(notice_type).strip()
    if not text:
        return False
    lower = text.lower()
    if _SOURCES_SOUGHT in lower:
        return True
    if _RFI_PHRASE in lower:
        return True
    return bool(_RFI_TOKEN_RE.search(lower))


def early_warning_notices(notices: pd.DataFrame) -> pd.DataFrame:
    """The in-scope (Sources Sought / RFI) rows of ``notices``, sorted by ``posted_date``
    descending (coerce-parsed; unparseable/missing dates sort last).

    Column-guarded: if ``notice_type`` or ``posted_date`` is absent, returns an EMPTY frame
    that still carries the input's columns — never raises, never invents a notice. An empty
    snapshot yields an honest empty lane, not an error. Pure: no I/O, no clock."""
    if not set(_REQUIRED_COLUMNS).issubset(notices.columns):
        return notices.iloc[0:0].copy()
    mask = notices["notice_type"].map(is_sources_sought_or_rfi)
    out = notices.loc[mask].copy()
    posted_sort = pd.to_datetime(out["posted_date"], errors="coerce")
    out = (
        out.assign(_posted_sort=posted_sort)
        .sort_values("_posted_sort", ascending=False, na_position="last")
        .drop(columns="_posted_sort")
        .reset_index(drop=True)
    )
    return out
