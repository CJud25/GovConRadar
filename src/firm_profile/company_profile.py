"""
company_profile — synthesize a vendor profile from a firm's own USAspending awards.

Pure and deterministic: same awards + same params => identical profile (all orderings
are total). The output matches the shape the deterministic scorer consumes
(``preferred_naics``, ``preferred_psc``, ``agencies_with_past_performance``,
``max_comfortable_contract_value``, ``states_served``), stamped with the real UEI.

Input contract — a DataFrame of the firm's own prime awards with these columns
(missing columns degrade gracefully to empty/zero, never an error):
    naics, psc, awarding_agency, total_obligated_amount, place_of_performance_state
"""

from __future__ import annotations

from typing import TypedDict

import pandas as pd

from utils.coerce import clean_code, nan_str


class VendorProfile(TypedDict):
    """The scorer-ready vendor profile shape (the contract between build_profile_from_awards
    and the scorer's vendor-aware components). ``data_source`` is always ``UEI:<uei>``."""

    data_source: str
    uei: str
    preferred_naics: list[str]
    preferred_psc: list[str]
    agencies_with_past_performance: list[str]
    max_comfortable_contract_value: int
    states_served: list[str]
    capabilities: list[str]


COL_NAICS = "naics"
COL_PSC = "psc"
COL_AGENCY = "awarding_agency"
COL_VALUE = "total_obligated_amount"
COL_STATE = "place_of_performance_state"

# Plausible defaults (see specs/radar_truth.md §11 open item — not yet human-confirmed).
DEFAULT_TOP_K = 5
DEFAULT_VALUE_PERCENTILE = 0.9


def _ranked_codes(df: pd.DataFrame, code_col: str, value_col: str, top_k: int) -> list[str]:
    """Top-k codes ranked by award count desc, then total dollars desc, then code asc
    (a total order => deterministic)."""
    if code_col not in df.columns or df.empty:
        return []
    codes = df[code_col].map(clean_code)
    if value_col in df.columns:
        dollars = pd.to_numeric(df[value_col], errors="coerce").fillna(0.0)
    else:
        dollars = pd.Series([0.0] * len(df), index=df.index)
    work = pd.DataFrame({"code": codes, "dollars": dollars})
    work = work[work["code"] != ""]
    if work.empty:
        return []
    grouped = work.groupby("code", as_index=False).agg(count=("code", "size"), dollars=("dollars", "sum"))
    grouped = grouped.sort_values(by=["count", "dollars", "code"], ascending=[False, False, True])
    return [str(c) for c in grouped["code"].tolist()[:top_k]]


def _distinct_by_frequency(df: pd.DataFrame, col: str) -> list[str]:
    """Distinct non-empty labels ordered by frequency desc, then label asc (total order)."""
    if col not in df.columns or df.empty:
        return []
    labels = df[col].map(nan_str)
    labels = labels[labels != ""]
    if labels.empty:
        return []
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts, key=lambda k: (-counts[k], k))


def _value_band(df: pd.DataFrame, value_col: str, percentile: float) -> int:
    """A high percentile of the firm's positive award values, as an int (0 if none)."""
    if value_col not in df.columns or df.empty:
        return 0
    vals = pd.to_numeric(df[value_col], errors="coerce").dropna()
    vals = vals[vals > 0]
    if vals.empty:
        return 0
    return int(round(float(vals.quantile(percentile))))


def build_profile_from_awards(
    awards_df: pd.DataFrame,
    uei: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    value_percentile: float = DEFAULT_VALUE_PERCENTILE,
) -> VendorProfile:
    """Build a scorer-ready vendor profile from a firm's own awards.

    ``data_source`` is always ``"UEI:<uei>"`` — never SYNTHETIC. ``capabilities`` is empty:
    capability narratives are not present in award data, so we do not invent them (the
    NAICS/PSC/agency/value/state signal is what awards can honestly support).
    """
    return {
        "data_source": f"UEI:{uei}",
        "uei": uei,
        "preferred_naics": _ranked_codes(awards_df, COL_NAICS, COL_VALUE, top_k),
        "preferred_psc": _ranked_codes(awards_df, COL_PSC, COL_VALUE, top_k),
        "agencies_with_past_performance": _distinct_by_frequency(awards_df, COL_AGENCY),
        "max_comfortable_contract_value": _value_band(awards_df, COL_VALUE, value_percentile),
        "states_served": _distinct_by_frequency(awards_df, COL_STATE),
        "capabilities": [],
    }
