"""
radar_handoff.py — pure builder for the `radar-handoff/v1` JSON snapshot GovConRadar
hands the analyst for the ReconOps Opportunity Packet's Origin intake (a separate,
private repo). ReconOps' parser there (`src/tens_hq/radar_handoff.py`) is fail-closed —
a file it rejects is a broken handoff — so every guard below mirrors that parser's
rules exactly (restated contract, vendored below; ReconOps cannot be imported here).

PURE module: no Streamlit, no clock/file/network access. `snapshot_date` is caller-
supplied (the app's own as-of, never `date.today()` in here) so the builder stays
deterministic. The VIEW (views/detail.py) decides what to render; this module only
decides what is safe to claim.

The handoff carries FACT CLAIMS ONLY — no score, tier, pursuit_score, or
recommendation (ReconOps is packet-not-score; a leaked score would be laundered
through its UI). `contract_title` is never emitted either (Radar titles can be
garbled FPDS text — see flag_garbled_title).
"""

import json
import math
import re
from datetime import date
from typing import Any, Mapping

import pandas as pd

SCHEMA_VERSION = "radar-handoff/v1"

# The frozen `claims` key list, vendored from ReconOps `src/tens_hq/radar_handoff.py`
# (a separate, private repo — restated here as LAW, not imported). Every key is always
# emitted, explicit null standing in for "unknown" (absent key and explicit null are
# equivalent to that parser, but emitting the full set mirrors ReconOps' own sample
# generator and keeps this producer's output self-documenting).
CLAIMS_KEYS = (
    "referenced_idv_piid",
    "recipient_name",
    "recipient_uei",
    "awarding_subagency",
    "naics_code",
    "psc_code",
    "type_of_set_aside_code",
    "extent_competed_code",
    "pop_start_date",
    "pop_current_end_date",
    "pop_potential_end_date",
    "number_of_offers_received",
    "total_obligation",
    "base_and_all_options",
    "place_of_performance",
)

_MAX_STR_LEN = 500  # any string field over this length is REJECTED whole-file by the
# ReconOps parser — never truncate (a truncated claim is an altered claim); emit null.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_na(v: Any) -> bool:
    """pd.isna guarded for values it can't take a scalar opinion on (never raises)."""
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _clean_str(v: Any) -> str | None:
    """str-or-null for a claims field: NaN/None -> null, blank-after-strip -> null,
    >500 chars -> null (never truncated)."""
    if v is None or _is_na(v):
        return None
    s = str(v).strip()
    if not s or len(s) > _MAX_STR_LEN:
        return None
    return s


def _naics_str(v: Any) -> str | None:
    """NAICS as a plain code string. Radar's `naics` column round-trips as int64 (clean
    data) or float64 (once any row in the column is NaN) — either way an int-like value
    must render "541511", NEVER "541511.0"."""
    if v is None or _is_na(v) or pd.api.types.is_bool(v):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        s = str(v).strip()
    else:
        s = str(int(f)) if math.isfinite(f) and f.is_integer() else str(v).strip()
    if not s or len(s) > _MAX_STR_LEN:
        return None
    return s


def _finite_number(v: Any) -> float | None:
    """float-or-null: NaN/None/bool -> null (the ReconOps `_optional_number` guard
    excludes bool on both money fields — JSON true/false must never coerce), non-finite
    (inf/-inf) -> null. A negative value is kept (a true snapshot fact for obligations).
    pd.api.types.is_bool also catches numpy.bool_, which `isinstance(v, bool)` misses —
    a CSV column of literal True/False parses to numpy-bool scalars."""
    if v is None or pd.api.types.is_bool(v) or _is_na(v):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _offers_int(v: Any) -> int | None:
    """int-or-null for number_of_offers_received: NaN -> null, integral float -> int,
    negative or non-integral -> null, bool NEVER emitted (even though int(True) == 1)."""
    f = _finite_number(v)
    if f is None or not f.is_integer():
        return None
    n = int(f)
    return n if n >= 0 else None


def _base_and_all_options(v: Any) -> float | None:
    """base_and_all_options <- `potential_value` (the FPDS ceiling), finite AND > 0.
    NEVER `base_and_all_options_value` — at this export's transaction grain that column
    is <=0 on ~62% of rows (mods can make it negative; see src/scoring/mods_signal.py),
    while `potential_value` is <=0 on well under 1%. A zero/negative "ceiling" claim
    would render as a visibly false dollar line in the ReconOps packet — the >0 guard
    mirrors src/scoring/burn_pressure.py treating base <= 0 as unusable."""
    f = _finite_number(v)
    return f if f is not None and f > 0 else None


def _state_2char(v: Any) -> str | None:
    """place_of_performance.state: strip().upper() must be EXACTLY 2 characters or the
    ReconOps parser rejects the whole file — all-whitespace collapses to absent (null).
    Alpha-only on top of the parser's len rule: every real value in this export is a
    2-letter USPS code (incl. AP/GU/MH/PR), so a non-alpha value (e.g. a future
    FIPS-coded "48") is a column-semantics change, not a state claim — null it."""
    if v is None or _is_na(v):
        return None
    s = str(v).strip().upper()
    return s if len(s) == 2 and s.isalpha() else None


def _valid_snapshot_date(as_of: Any) -> bool:
    """`snapshot_date` must be a string matching YYYY-MM-DD AND a real calendar date
    (date.fromisoformat rejects e.g. "2026-13-40" once the regex shape has matched)."""
    if not isinstance(as_of, str) or not _DATE_RE.match(as_of):
        return False
    try:
        date.fromisoformat(as_of)
    except ValueError:
        return False
    return True


def _source_label(mode: str, legacy_synthetic: bool) -> tuple[str, bool] | None:
    """(radar_source_label, synthetic_sample) for the resolved data mode, or None for an
    unrecognized mode (fail closed — no handoff beats a mislabeled one). `mode` is
    the label from components.data.resolve_data_dir() ("custom" | "live" | "sample");
    `legacy_synthetic` distinguishes data/sample/ (real USAspending subsample) from the
    legacy streamlit_app/assets/sample_data/ bundle (both resolve to mode == "sample") —
    the caller determines that by comparing the resolved dir against data.SAMPLE_DIR."""
    if mode == "custom":
        # "custom" only means "$RADAR_DATA_DIR override" — NOT "this is the bundled
        # synthetic demo data" (that's what synthetic_sample means to ReconOps), so the
        # flag stays False here; the label alone carries the disclosure.
        return "GovConRadar — custom data directory (analyst-supplied)", False
    if mode == "live":
        return "GovConRadar — public USAspending snapshot (live)", False
    if mode == "sample":
        if legacy_synthetic:
            return "GovConRadar — SYNTHETIC demo bundle", True
        return "GovConRadar — public USAspending snapshot (sample)", False
    # A mode this function doesn't know must be labeled here explicitly before a
    # handoff can carry it — falling through to any existing label would lie.
    return None


def build_radar_handoff(
    row: Mapping[str, Any], *, as_of: str, mode: str, legacy_synthetic: bool
) -> dict[str, Any] | None:
    """Build one `radar-handoff/v1` payload from a fact_recompete_candidates row.
    Fail-closed: returns None (no handoff) when `piid` is blank or `as_of` isn't a
    real YYYY-MM-DD date — the caller (the view) renders an explanatory caption
    instead of a button in that case."""
    piid = _clean_str(row.get("piid"))
    if piid is None or not _valid_snapshot_date(as_of):
        return None

    source = _source_label(mode, legacy_synthetic)
    if source is None:
        return None
    label, synthetic = source
    state = _state_2char(row.get("place_of_performance_state"))
    claims = {
        "referenced_idv_piid": _clean_str(row.get("referenced_idv_piid")),
        "recipient_name": _clean_str(row.get("incumbent_vendor")),
        "recipient_uei": _clean_str(row.get("incumbent_uei")),
        "awarding_subagency": _clean_str(row.get("subagency")),
        "naics_code": _naics_str(row.get("naics")),
        "psc_code": _clean_str(row.get("psc")),
        "type_of_set_aside_code": _clean_str(row.get("type_of_set_aside_code")),
        "extent_competed_code": _clean_str(row.get("extent_competed_code")),
        "pop_start_date": _clean_str(row.get("pop_start_date")),
        "pop_current_end_date": _clean_str(row.get("current_end_date")),
        "pop_potential_end_date": _clean_str(row.get("potential_end_date")),
        "number_of_offers_received": _offers_int(row.get("number_of_offers_received")),
        "total_obligation": _finite_number(row.get("total_obligated_amount")),
        "base_and_all_options": _base_and_all_options(row.get("potential_value")),
        "place_of_performance": {"city": None, "county": None, "state": state} if state else None,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": as_of,
        "piid": piid,
        "radar_source_label": label,
        "synthetic_sample": synthetic,
        "claims": claims,
    }


def radar_handoff_json_bytes(payload: dict[str, Any]) -> bytes:
    """UTF-8 bytes for the download button: json.dumps(indent=2) + a trailing newline.
    Every value in `payload` is already a native str/int/float/bool/None/dict (cast in
    build_radar_handoff), so this never trips json.dumps on a numpy int64/float64."""
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")
