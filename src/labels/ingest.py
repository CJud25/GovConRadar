"""Fail-loud loaders for the hand-labeled worksheet CSVs (data/labels/).

Broken label data must never ride quietly into a published metric: a malformed
worksheet raises ValueError naming the offending row and column; only an ABSENT
file or a header-only file (the committed empty state a fresh clone carries)
degrades to an empty, exactly-schemaed frame. Blank-label rows are legitimate —
they are worksheet rows awaiting the labeler — and are returned as-is (callers
count them as unlabeled); every validation below applies to FILLED values only.
"""

from pathlib import Path

import pandas as pd

from labels.taxonomy import (
    INCUMBENT_RETAINED_VALUES,
    LABEL_CONFIDENCE_GRADES,
    LINK_LABEL_COLUMNS,
    LINK_LABEL_VALUES,
    OUTCOME_LABEL_COLUMNS,
    OUTCOME_LABELS,
    SAMPLE_SETS,
    UNDETERMINABLE_REASONS,
)

_YN = ("Y", "N")


def _read(path: Path, columns: tuple[str, ...]) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame({c: pd.Series(dtype=str) for c in columns})
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    if list(df.columns) != list(columns):
        missing = [c for c in columns if c not in df.columns]
        extra = [c for c in df.columns if c not in columns]
        raise ValueError(
            f"{Path(path).name}: column set/order must be exactly the taxonomy schema "
            f"(missing: {missing}, unexpected: {extra}, order drift otherwise)"
        )
    return df


def _row_name(df: pd.DataFrame, idx: int) -> str:
    case = str(df.at[idx, "case_id"]).strip()
    return f"row {idx + 2} (case_id={case or '<blank>'})"  # +2: header + 1-indexing


def _require_enum(df: pd.DataFrame, name: str, column: str, allowed: tuple[str, ...]) -> None:
    values = df[column].astype(str).str.strip()
    bad = df.index[(values != "") & (~values.isin(allowed))]
    if len(bad):
        raise ValueError(
            f"{name}: {_row_name(df, int(bad[0]))} column '{column}' = {values.at[bad[0]]!r} not in {allowed}"
        )


def _require_dates_parse(df: pd.DataFrame, name: str, column: str) -> None:
    values = df[column].astype(str).str.strip()
    filled = values != ""
    parsed = pd.to_datetime(values.where(filled), errors="coerce")
    bad = df.index[filled & parsed.isna()]
    if len(bad):
        raise ValueError(
            f"{name}: {_row_name(df, int(bad[0]))} column '{column}' = {values.at[bad[0]]!r} is not a parseable date"
        )


def _require_unique_case_id(df: pd.DataFrame, name: str) -> None:
    ids = df["case_id"].astype(str).str.strip()
    if (ids == "").any():
        raise ValueError(f"{name}: {_row_name(df, int(df.index[ids == ''][0]))} column 'case_id' is blank")
    dupes = ids[ids.duplicated()]
    if len(dupes):
        raise ValueError(f"{name}: duplicate case_id {dupes.iloc[0]!r} — case ids must be unique")


def load_link_labels(path: Path) -> pd.DataFrame:
    """data/labels/link_labels.csv -> DataFrame with exactly LINK_LABEL_COLUMNS."""
    name = Path(path).name
    df = _read(Path(path), LINK_LABEL_COLUMNS)
    if df.empty:
        return df
    _require_unique_case_id(df, name)
    _require_enum(df, name, "label", LINK_LABEL_VALUES)
    for col in ("sampled_snapshot_date", "notice_posted_date", "labeled_date"):
        _require_dates_parse(df, name, col)
    return df


def load_outcome_labels(path: Path) -> pd.DataFrame:
    """data/labels/outcome_labels.csv -> DataFrame with exactly OUTCOME_LABEL_COLUMNS."""
    name = Path(path).name
    df = _read(Path(path), OUTCOME_LABEL_COLUMNS)
    if df.empty:
        return df
    _require_unique_case_id(df, name)
    _require_enum(df, name, "sample_set", SAMPLE_SETS)
    _require_enum(df, name, "outcome_label", OUTCOME_LABELS)
    _require_enum(df, name, "undeterminable_reason", UNDETERMINABLE_REASONS)
    _require_enum(df, name, "label_confidence", LABEL_CONFIDENCE_GRADES)
    _require_enum(df, name, "notice_anchored", _YN)
    _require_enum(df, name, "unmask_performed", _YN)
    _require_enum(df, name, "label_changed_after_unmask", _YN)
    _require_enum(df, name, "incumbent_retained_observed", INCUMBENT_RETAINED_VALUES)
    for col in ("sampled_snapshot_date", "potential_end_date", "unmask_date", "labeled_date"):
        _require_dates_parse(df, name, col)

    outcome = df["outcome_label"].astype(str).str.strip()
    reason = df["undeterminable_reason"].astype(str).str.strip()
    conf = df["label_confidence"].astype(str).str.strip()
    anchored = df["notice_anchored"].astype(str).str.strip()
    unmasked = df["unmask_performed"].astype(str).str.strip()
    unmask_date = df["unmask_date"].astype(str).str.strip()
    retained = df["incumbent_retained_observed"].astype(str).str.strip()
    changed = df["label_changed_after_unmask"].astype(str).str.strip()
    notes = df["labeler_notes"].astype(str).str.strip()

    bad = df.index[(outcome == "undeterminable") & (reason == "")]
    if len(bad):
        raise ValueError(
            f"{name}: {_row_name(df, int(bad[0]))} column 'undeterminable_reason' is "
            "required when outcome_label == 'undeterminable'"
        )
    bad = df.index[(outcome != "undeterminable") & (reason != "")]
    if len(bad):
        raise ValueError(
            f"{name}: {_row_name(df, int(bad[0]))} column 'undeterminable_reason' must be "
            "blank unless outcome_label == 'undeterminable'"
        )
    bad = df.index[(anchored == "N") & (conf == "high")]
    if len(bad):
        raise ValueError(
            f"{name}: {_row_name(df, int(bad[0]))} column 'label_confidence' cannot be "
            "'high' when notice_anchored == 'N' (FPDS-only judgments cap at medium)"
        )
    bad = df.index[(unmasked == "Y") & ((unmask_date == "") | (retained == ""))]
    if len(bad):
        raise ValueError(
            f"{name}: {_row_name(df, int(bad[0]))} columns 'unmask_date' and "
            "'incumbent_retained_observed' are required when unmask_performed == 'Y'"
        )
    bad = df.index[(unmasked != "Y") & ((unmask_date != "") | (retained != ""))]
    if len(bad):
        raise ValueError(
            f"{name}: {_row_name(df, int(bad[0]))} columns 'unmask_date' / "
            "'incumbent_retained_observed' must be blank unless unmask_performed == 'Y'"
        )
    bad = df.index[(changed == "Y") & (notes == "")]
    if len(bad):
        raise ValueError(
            f"{name}: {_row_name(df, int(bad[0]))} column 'labeler_notes' must explain a "
            "label_changed_after_unmask == 'Y' (the contamination audit trail)"
        )
    return df
