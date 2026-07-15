"""
burn_pressure — profile-independent, baked-only, honesty-gated contract signal (pure).

Answers one capture question at bake time: *are this order's obligations running ahead of
or behind its period-of-performance clock?* It is a subtraction of two published facts —
``ceiling_burn_ratio`` (obligated / order ceiling) minus ``time_elapsed_ratio`` (PoP elapsed /
PoP planned) — and it **refuses** (``insufficient`` / no band) whenever either input is
missing, degenerate, out-of-window, a parent vehicle, or a net deobligation. Priors live in
``config/burn_pressure.yaml``; this module never imports ``utils.config`` (only the reverse
edge exists). Deterministic: the snapshot date is injected — no clock, no RNG. Mirrors the
house idiom of ``src/scoring/eligibility.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Mapping, TypedDict

import pandas as pd

BURN_COLUMNS: tuple[str, ...] = ("ceiling_burn_ratio", "burn_pressure", "burn_band", "burn_basis")
BURN_BANDS: tuple[str, ...] = ("burning_hot", "on_pace", "underutilized")
BURN_BASES: tuple[str, ...] = ("measured", "fully_funded", "ceiling_exceeded", "insufficient")
BURN_BAND_NA: str = "not_applicable"


class BurnResult(TypedDict):
    ceiling_burn_ratio: float | None
    burn_pressure: float | None
    burn_band: str  # one of BURN_BANDS or BURN_BAND_NA — never ""
    burn_basis: str  # one of BURN_BASES


@dataclass(frozen=True)
class BurnConfig:
    hot_threshold: float  # 0.20
    cold_threshold: float  # -0.20
    fully_funded_ratio: float  # 0.98
    ceiling_exceeded_ratio: float  # 1.05
    min_planned_days: int  # 30
    max_planned_days: int  # 10950
    idv_award_types: frozenset[str]  # {"IDC","IDIQ","GWAC","FSS","BOA","BPA","IDV"} (EXACT match, normalized)


# ── config coercion helpers (isinstance-narrowing → mypy-strict clean; typed errors for the stranger) ──
def _cfg_num(raw: Mapping[str, object], key: str) -> float:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"burn config: {key!r} must be a real number, got {v!r}")
    return float(v)


def _cfg_int(raw: Mapping[str, object], key: str) -> int:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"burn config: {key!r} must be an int, got {v!r}")
    return int(v)


def _cfg_str_set(raw: Mapping[str, object], key: str) -> frozenset[str]:
    v = raw[key]
    if not isinstance(v, (list, tuple)):
        raise ValueError(f"burn config: {key!r} must be a list/tuple, got {v!r}")
    return frozenset(str(x).strip().upper() for x in v)


def load_burn_config(raw: Mapping[str, object]) -> BurnConfig:
    """Coerce + VALIDATE the config mapping into a frozen BurnConfig. Raises
    ValueError on a wrong-typed or incoherent prior. This is the 'config as a Mapping'
    boundary that keeps the module self-contained (callers pass utils.config.BURN_PRESSURE)."""
    cfg = BurnConfig(
        hot_threshold=_cfg_num(raw, "hot_threshold"),
        cold_threshold=_cfg_num(raw, "cold_threshold"),
        fully_funded_ratio=_cfg_num(raw, "fully_funded_ratio"),
        ceiling_exceeded_ratio=_cfg_num(raw, "ceiling_exceeded_ratio"),
        min_planned_days=_cfg_int(raw, "min_planned_days"),
        max_planned_days=_cfg_int(raw, "max_planned_days"),
        idv_award_types=_cfg_str_set(raw, "idv_award_types"),
    )
    if not (cfg.cold_threshold < 0.0 < cfg.hot_threshold):
        raise ValueError("burn: cold_threshold < 0 < hot_threshold required")
    if not (0.0 < cfg.fully_funded_ratio <= cfg.ceiling_exceeded_ratio):
        raise ValueError("burn: 0 < fully_funded_ratio <= ceiling_exceeded_ratio required")
    if not (1 <= cfg.min_planned_days <= cfg.max_planned_days):
        raise ValueError("burn: 1 <= min_planned_days <= max_planned_days required")
    return cfg


def _to_float(v: object) -> float | None:
    """None on None/NaN/inf/unparseable; a finite float otherwise. May be negative."""
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _to_date(v: object) -> date | None:
    """Accepts pandas Timestamp / ISO str / date / None / NaT -> date | None."""
    ts = pd.to_datetime(v, errors="coerce")
    if ts is None or pd.isna(ts):
        return None
    return date(int(ts.year), int(ts.month), int(ts.day))


def _insufficient(cbr: float | None) -> BurnResult:
    return {"ceiling_burn_ratio": cbr, "burn_pressure": None, "burn_band": BURN_BAND_NA, "burn_basis": "insufficient"}


def _result(cbr: float | None, bp: float | None, band: str, basis: str) -> BurnResult:
    return {"ceiling_burn_ratio": cbr, "burn_pressure": bp, "burn_band": band, "burn_basis": basis}


def burn_pressure_row(row: Mapping[str, object], snapshot_date: date, cfg: BurnConfig) -> BurnResult:
    """Compute the burn signal for a single candidate row (see the §4 decision table).

    Early-return form so mypy narrows ``base``/``obl`` to ``float`` before the division and
    ``pop``/``pend`` to ``date`` before the window arithmetic. First matching gate wins."""
    award_type = str(row.get("award_type") or "").strip().upper()
    is_idv = award_type in cfg.idv_award_types  # EXACT membership: "BPA CALL"/"DELIVERY ORDER" are NOT IDVs
    base = _to_float(row.get("base_and_all_options_value"))
    obl = _to_float(row.get("total_obligated_amount"))

    # ── denominator / coverage gate → insufficient, cbr None (den_ok fails) ───────────
    #    den_ok  <=>  (not is_idv) and base>0 and obl present and obl>=0
    if is_idv or base is None or base <= 0.0 or obl is None or obl < 0.0:
        return _insufficient(None)  # mypy: base/obl narrowed to float below
    cbr = round(obl / base, 4)  # FACT column; published for every den_ok row

    # ── den_ok held: PoP-window coverage gate → insufficient, cbr PUBLISHED ───────────
    pop = _to_date(row.get("pop_start_date"))
    pend = _to_date(row.get("potential_end_date"))
    if pop is None or pend is None:
        return _insufficient(cbr)
    planned = (pend - pop).days
    if planned < cfg.min_planned_days or planned > cfg.max_planned_days:
        return _insufficient(cbr)
    elapsed = (snapshot_date - pop).days
    if elapsed < 0 or elapsed > planned:  # not started / already ended
        return _insufficient(cbr)

    # ── den_ok AND in-window: classify by cbr (PURE-RATIO fully_funded; no pricing code) ──
    if cbr > cfg.ceiling_exceeded_ratio:
        return _result(cbr, None, BURN_BAND_NA, "ceiling_exceeded")
    if cbr >= cfg.fully_funded_ratio:
        return _result(cbr, None, BURN_BAND_NA, "fully_funded")
    ter = elapsed / planned  # in [0, 1]
    bp = round(cbr - ter, 4)  # in [-1, 0.98) for measured
    if bp > cfg.hot_threshold:
        band = "burning_hot"
    elif bp < cfg.cold_threshold:
        band = "underutilized"
    else:
        band = "on_pace"  # strict >/<; ±0.20 boundary => on_pace
    return _result(cbr, bp, band, "measured")


def annotate_burn_pressure(df: pd.DataFrame, snapshot_date: date, cfg: BurnConfig) -> pd.DataFrame:
    """Return a COPY of the candidate frame with the four burn columns ASSIGNED (overwritten,
    not appended) — so a re-bake is drop-then-rederive idempotent by construction. Numeric
    columns are pinned to float64 (deterministic even for an all-non-measured / empty bundle);
    the two enum columns are object. Preserves index and row order. No clock, no RNG."""
    out = df.copy()
    results = [burn_pressure_row(row, snapshot_date, cfg) for row in out.to_dict("records")]
    out["ceiling_burn_ratio"] = pd.Series([r["ceiling_burn_ratio"] for r in results], index=out.index, dtype="float64")
    out["burn_pressure"] = pd.Series([r["burn_pressure"] for r in results], index=out.index, dtype="float64")
    out["burn_band"] = pd.Series([r["burn_band"] for r in results], index=out.index, dtype="object")
    out["burn_basis"] = pd.Series([r["burn_basis"] for r in results], index=out.index, dtype="object")
    return out
