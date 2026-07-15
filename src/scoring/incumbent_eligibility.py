"""
incumbent_eligibility — incumbent STRUCTURAL-eligibility directional flag (pure).

Answers one capture question at bake time, as a DIRECTIONAL flag with a named basis —
*never a verdict, never a score*: has a vendor's per-procurement size determination for a
given NAICS moved SMALL -> OTHER-THAN-SMALL over time? A same-NAICS award history whose
determinations shift S -> O looks like a vendor that outgrew the size standard (or was
acquired and recertified), so it may no longer be eligible for that NAICS's small-business
set-asides. It labels; it never filters, and it MUST NOT touch incumbent_vulnerability_score
or any score.

Codes-over-text convention (see set_aside_fit / agency_fit / burn_pressure precedents): the
signal reads the FPDS *code* column ``business_size_determination_code`` — values "S" / "O",
never the text column ``contracting_officers_determination_of_business_size`` (whose values
"SMALL BUSINESS" / "OTHER THAN SMALL BUSINESS" both contain the substring "SMALL", so any
text compare mis-fires or ships flat). Unknown is unforgeable: a vendor with no readable
NAICS cell (every cell below ``min_determinations`` non-blank codes) is None with the
insufficient basis — a shift is None <=> its basis is the insufficient one (triple
equivalence). Deterministic: no clock, no RNG. This module never imports ``utils.config``.
"""

from __future__ import annotations

import pandas as pd

SIZE_SMALL_CODE: str = "S"
SIZE_OTHER_THAN_SMALL_CODE: str = "O"

_SIZE_LABELS: dict[str, str] = {
    SIZE_SMALL_CODE: "SMALL BUSINESS",
    SIZE_OTHER_THAN_SMALL_CODE: "OTHER THAN SMALL BUSINESS",
}

# The two ASSIGNED label columns and the vendor-UEI join key on dim_vendor.
SIZE_SHIFT_COLUMNS: tuple[str, str] = ("size_standard_shift", "size_standard_basis")
VENDOR_UEI_COLUMN: str = "incumbent_uei"  # verified: transform.incumbent_agency INCUMBENT_SCHEMA

# The single None-case basis. shift is None <=> basis == BASIS_INSUFFICIENT (triple equivalence).
BASIS_INSUFFICIENT: str = "insufficient determinations (<3 per NAICS cell)"

# Columns read off classified_awards (awardee_uei is the vendor key there; recompete.py renames
# it to incumbent_uei only downstream).
_UEI_COLUMN = "awardee_uei"
_NAICS_COLUMN = "naics"
_DATE_COLUMN = "date_signed"
_CODE_COLUMN = "business_size_determination_code"


def _norm_code(value: object) -> str | None:
    """Normalize a size-determination cell to "S"/"O", or None when blank/unreadable.
    Only the two recognized CODE values are readable; anything else (blank, NaN, the text
    column's full strings, an unexpected token) is None so it can never be miscounted."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if text == SIZE_SMALL_CODE:
        return SIZE_SMALL_CODE
    if text == SIZE_OTHER_THAN_SMALL_CODE:
        return SIZE_OTHER_THAN_SMALL_CODE
    return None


def _flagged_basis(naics_value: object, n_small: int, n_other: int, m_total: int) -> str:
    """Names the ACTUAL flag evidence: some S strictly before the latest determination,
    which is itself O. (Review catch 2026-07-13: the earlier template said "earliest was
    <earliest row's code>", which on a mid-history-S vendor read "earliest was OTHER THAN
    SMALL" — an evidence sentence contradicting the flag it explains.)"""
    other_label = _SIZE_LABELS[SIZE_OTHER_THAN_SMALL_CODE]
    small_label = _SIZE_LABELS[SIZE_SMALL_CODE]
    return (
        f"latest of {m_total} NAICS {naics_value} determinations is "
        f"'{other_label}' (code {SIZE_OTHER_THAN_SMALL_CODE}, {n_other} total), with "
        f"{n_small} earlier '{small_label}' (code {SIZE_SMALL_CODE}) determination(s) "
        f"— per-procurement, directional"
    )


def _vendor_result(vendor_df: pd.DataFrame, min_determinations: int) -> tuple[bool | None, str]:
    """Directional flag for ONE vendor's readable (non-blank-code) award rows.

    Returns (shift, basis). vendor_df has normalized "_code" ("S"/"O") and "_date" (Timestamp,
    NaT for unparseable) columns. A NAICS cell is readable at >= min_determinations non-blank
    codes; a vendor is flagged True when ANY readable cell has an S dated strictly before a
    later O. No readable cell -> None + insufficient basis (triple equivalence)."""
    readable_count = 0
    flagged: tuple[object, int, int, int] | None = None
    for naics_value in sorted(vendor_df[_NAICS_COLUMN].dropna().unique(), key=str):
        cell = vendor_df[vendor_df[_NAICS_COLUMN] == naics_value]
        m_total = int(len(cell))
        if m_total < min_determinations:
            continue  # unreadable cell — does not count toward coverage
        readable_count += m_total
        small_dates = cell.loc[cell["_code"] == SIZE_SMALL_CODE, "_date"].dropna()
        other_dates = cell.loc[cell["_code"] == SIZE_OTHER_THAN_SMALL_CODE, "_date"].dropna()
        # DIRECTIONAL transition: the cell's LATEST dated determination must itself be O,
        # with some S strictly earlier. (Adversarial review, 2026-07-13: the original
        # "earliest S before latest O" also fired on non-monotonic histories like
        # S->O->S whose latest determination is SMALL — a "risk" badge on a vendor whose
        # most recent CO determination says small overclaims the one-way shift.)
        dated = cell.dropna(subset=["_date"])
        latest_is_other = bool(len(other_dates) and len(dated) and other_dates.max() == dated["_date"].max())
        transition = bool(latest_is_other and len(small_dates) and small_dates.min() < other_dates.max())
        if transition and flagged is None:
            n_other = int((cell["_code"] == SIZE_OTHER_THAN_SMALL_CODE).sum())
            # Only S determinations dated STRICTLY BEFORE the latest O count as flag
            # evidence — the basis must name exactly what fired the rule.
            n_small_before = int((small_dates < other_dates.max()).sum())
            flagged = (naics_value, n_small_before, n_other, m_total)

    if flagged is not None:
        naics_value, n_small_before, n_other, m_total = flagged
        return True, _flagged_basis(naics_value, n_small_before, n_other, m_total)
    if readable_count > 0:
        return (
            False,
            f"no S->O transition across {readable_count} readable determinations — per-procurement, directional",
        )
    return None, BASIS_INSUFFICIENT


def _size_shift_by_uei(
    classified_awards: pd.DataFrame, min_determinations: int
) -> dict[object, tuple[bool | None, str]]:
    """Per-UEI directional result over classified_awards, keyed by awardee_uei. Rows without a
    readable "S"/"O" code are dropped before grouping (they never count toward coverage). Returns
    {} when the required columns are absent or no readable code exists — every vendor then defaults
    to None + insufficient (an honest 'we could not read it', which the bake-time receipt catches)."""
    required = {_UEI_COLUMN, _NAICS_COLUMN, _DATE_COLUMN, _CODE_COLUMN}
    if classified_awards.empty or not required.issubset(classified_awards.columns):
        return {}
    frame = classified_awards[[_UEI_COLUMN, _NAICS_COLUMN, _DATE_COLUMN, _CODE_COLUMN]].copy()
    frame["_code"] = frame[_CODE_COLUMN].map(_norm_code)
    frame = frame[frame["_code"].notna()]
    if frame.empty:
        return {}
    # The known " 00:00:00"-suffix column is never strict-parsed — coerce (NaT for junk).
    frame["_date"] = pd.to_datetime(frame[_DATE_COLUMN], errors="coerce")
    results: dict[object, tuple[bool | None, str]] = {}
    for uei, vendor_df in frame.groupby(_UEI_COLUMN):
        results[uei] = _vendor_result(vendor_df, min_determinations)
    return results


def annotate_size_shift(
    dim_vendor: pd.DataFrame, classified_awards: pd.DataFrame, min_determinations: int = 3
) -> pd.DataFrame:
    """Return a COPY of dim_vendor with two ASSIGNED directional-label columns —
    ``size_standard_shift`` (object True/False/None) and ``size_standard_basis`` (str) —
    joined per vendor UEI. Never mutates an existing column (label, never a filter) and MUST
    NOT touch incumbent_vulnerability_score. A dim_vendor row whose UEI is absent from the
    per-UEI result gets None + the insufficient basis (shift None <=> insufficient basis)."""
    if VENDOR_UEI_COLUMN not in dim_vendor.columns:
        raise ValueError(
            f"annotate_size_shift: dim_vendor has no {VENDOR_UEI_COLUMN!r} column to join on — "
            "cannot attach the size-shift flag. (Verified name; a schema drift here must STOP the "
            "bake rather than improvise a join key.)"
        )
    out = dim_vendor.copy()
    per_uei = _size_shift_by_uei(classified_awards, min_determinations)
    shift_values: list[bool | None] = []
    basis_values: list[str] = []
    for uei in out[VENDOR_UEI_COLUMN]:
        shift, basis = per_uei.get(uei, (None, BASIS_INSUFFICIENT))
        shift_values.append(shift)
        basis_values.append(basis)
    out["size_standard_shift"] = pd.Series(shift_values, index=out.index, dtype="object")
    out["size_standard_basis"] = pd.Series(basis_values, index=out.index, dtype="object")
    return out
