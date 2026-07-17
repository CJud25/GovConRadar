"""
incumbent_displacement — the categorical "Displacement signals: k of n read" lane (pure).

Combines the strongest already-baked forward signals on one candidate — signals the pipeline
COMPUTES but lets drive nothing — into one decision-table lane a capture reader can act on.
The lane is a LABEL, never a score component: it is NEVER blended into ``pursuit_score``,
``priority_tier``, or any weight (govcon honesty rule #9 — the scorer-parity firewall stays
byte-identical). Signals are combined CATEGORICALLY (a count over a fixed taxonomy), never
numerically — blending correlated entrenchment proxies is how false precision compounds.

The six signals, in canonical order, each read from named baked columns:
  * ``bridge``              — ``bridge_flag`` gated by ``bridge_basis`` (scoring.mods_signal):
                              a non-competed extension pushed the end past the original plan.
  * ``termination``         — ``terminated`` + ``termination_basis`` (scoring.mods_signal):
                              a termination reason-for-modification code was observed.
  * ``deobligation``        — ``has_deobligation`` (scoring.mods_signal): a large negative
                              obligation before the planned end. The mods layer's conservative
                              False (unverifiable timing folds to False) is inherited, not
                              re-litigated here.
  * ``lapsed_no_successor`` — ``candidate_status == "expired_grace"`` with
                              ``successor_visible_basis == "none_visible"``
                              (scoring.successor_proxy): recently lapsed and no follow-on is
                              visible in public data yet.
  * ``sole_offer``          — ``number_of_offers_received == 1`` on a competed award
                              (extent A/D/F) — the de-facto incumbent lock, mirroring
                              ``scoring.reason_codes._h_incumbent_lock`` (its COMPETED_CODES /
                              SOLE_SOURCE_OFFERS constants are imported, never restated).
  * ``size_shift``          — ``dim_vendor.size_standard_shift`` joined by ``incumbent_uei``
                              (scoring.incumbent_eligibility): the incumbent's CO size
                              determinations moved S -> O. Read ONLY when the caller supplies
                              the vendor frame; otherwise gated Unknown, never guessed.

Every signal resolves to fired / quiet / unknown — a missing fact is unknown, never
fabricated. A Data-Gap-quarantined stale row (``candidate_status == "expired_stale"``) reads
EVERY signal unknown: its record is months-to-years old, so an observed "quiet" cannot be
claimed from it, and a stale row must never show an observed band beside its Data Gap tier
(2026-07 adversarial review). Fewer than ``min_signals_read`` readable signals (which the
stale quarantine forces) coverage-gates the whole lane to
``insufficient`` (band ``not_applicable``, count None): Unknown is unforgeable —
``basis == "observed"  <=>  count is not None  <=>  signals is not None  <=>  band is real``.
``displacement_signals`` / ``displacement_unread`` use the token ``"none"`` (never ``""``)
for the empty set, so a CSV round-trip cannot forge a NaN out of an observed empty read.
Priors live in ``config/incumbent_displacement.yaml`` (+ the offers sentinels shared from
``config/reason_codes.yaml``); this module never imports ``utils.config`` (only the reverse
edge exists). Deterministic: no clock, no RNG, no I/O. Mirrors the house idiom of
``src/scoring/burn_pressure.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, TypedDict

import pandas as pd

from scoring.reason_codes import COMPETED_CODES, SOLE_SOURCE_OFFERS

DISPLACEMENT_SIGNALS: tuple[str, ...] = (
    "bridge",
    "termination",
    "deobligation",
    "lapsed_no_successor",
    "sole_offer",
    "size_shift",
)
DISPLACEMENT_COLUMNS: tuple[str, ...] = (
    "displacement_signal_count",
    "displacement_signals_read",
    "displacement_signals",
    "displacement_unread",
    "displacement_band",
    "displacement_basis",
)
DISPLACEMENT_BANDS: tuple[str, ...] = ("multiple_signals", "single_signal", "none_observed")
DISPLACEMENT_BAND_NA: str = "not_applicable"
DISPLACEMENT_BASES: tuple[str, ...] = ("observed", "insufficient")
# The CSV-stable empty-set token for the signals / unread columns (see module docstring).
NO_SIGNALS_TOKEN: str = "none"
# The joined-slug separator ("bridge+termination"); slugs contain no "+" by construction.
SIGNAL_SEPARATOR: str = "+"

# Per-signal statuses (internal vocabulary; the columns publish the reductions).
_FIRED, _QUIET, _UNKNOWN = "fired", "quiet", "unknown"

# Sentinel: "no vendor size-shift read is available" — distinct from a joined None/NaN
# (vendor present but its own basis was insufficient). Both resolve to unknown.
_UNAVAILABLE = object()

# candidate_status vocabulary (scoring.quality_flags.derive_status): an active row is an
# OBSERVED non-lapse; an expired_stale row is a quarantined Data Gap — displacement_row
# stale-gates the WHOLE lane for it (every signal unknown), so a stale record can never
# read as an observed quiet nor earn an observed band beside its Data Gap tier.
_NON_LAPSED_STATUSES: frozenset[str] = frozenset({"active"})
_LAPSED_STATUS: str = "expired_grace"
_STALE_STATUS: str = "expired_stale"


class DisplacementResult(TypedDict):
    displacement_signal_count: int | None  # k fired; None <=> insufficient
    displacement_signals_read: int  # n readable of the 6 — always published (a coverage fact)
    displacement_signals: str | None  # "+"-joined fired slugs / "none"; None <=> insufficient
    displacement_unread: str  # "+"-joined unknown slugs / "none" — always published
    displacement_band: str  # one of DISPLACEMENT_BANDS or DISPLACEMENT_BAND_NA — never ""
    displacement_basis: str  # one of DISPLACEMENT_BASES


@dataclass(frozen=True)
class DisplacementConfig:
    min_signals_read: int  # 3 — readable signals below this gate the lane to insufficient
    offers_sentinels: frozenset[int]  # {117, 253} — shared verbatim with config/reason_codes.yaml
    offers_max_plausible: int  # 100 — shared verbatim with config/reason_codes.yaml


# ── config coercion helpers (isinstance-narrowing → mypy-strict clean; typed errors for the stranger) ──
def _cfg_int(raw: Mapping[str, object], key: str) -> int:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"displacement config: {key!r} must be an int, got {v!r}")
    return int(v)


def _cfg_int_set(raw: Mapping[str, object], key: str) -> frozenset[int]:
    v = raw[key]
    if not isinstance(v, (list, tuple)):
        raise ValueError(f"displacement config: {key!r} must be a list/tuple, got {v!r}")
    out: set[int] = set()
    for x in v:
        if isinstance(x, bool) or not isinstance(x, int):
            raise ValueError(f"displacement config: {key!r} entries must be ints, got {x!r}")
        out.add(int(x))
    return frozenset(out)


def load_displacement_config(raw: Mapping[str, object]) -> DisplacementConfig:
    """Coerce + VALIDATE the config mapping into a frozen DisplacementConfig. Raises
    ValueError on a wrong-typed or incoherent prior. This is the 'config as a Mapping'
    boundary that keeps the module self-contained (callers pass
    utils.config.INCUMBENT_DISPLACEMENT)."""
    cfg = DisplacementConfig(
        min_signals_read=_cfg_int(raw, "min_signals_read"),
        offers_sentinels=_cfg_int_set(raw, "offers_sentinels"),
        offers_max_plausible=_cfg_int(raw, "offers_max_plausible"),
    )
    if not (1 <= cfg.min_signals_read <= len(DISPLACEMENT_SIGNALS)):
        raise ValueError(
            f"displacement: 1 <= min_signals_read <= {len(DISPLACEMENT_SIGNALS)} required, got {cfg.min_signals_read}"
        )
    if cfg.offers_max_plausible < 1:
        raise ValueError("displacement: offers_max_plausible must be >= 1")
    return cfg


# ── typed value helpers (mirror the burn_pressure / mods_signal precedent) ──
def _to_flag(v: object) -> bool | None:
    """Baked boolean columns round-trip CSV as real bools OR the strings "True"/"False"
    (NaN/blank when missing) — never trust bool() (bool("False") is truthy). Mirrors the
    app's house truthiness for the MOD_COLUMNS."""
    if isinstance(v, bool):
        return v
    s = _norm_str(v)
    if s == "True":
        return True
    if s == "False":
        return False
    return None


def _to_int(v: object) -> int | None:
    """None on None/NaN/inf/unparseable; a rounded int otherwise."""
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return int(round(f))


def _norm_str(v: object) -> str:
    """str -> strip; None / NaN / pandas-missing sentinels -> "" (never the literal 'nan')."""
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):  # np.float64 subclasses float -> covered
        return ""
    s = str(v).strip()
    return "" if s in ("nan", "NaN", "NaT", "<NA>", "None") else s


def _norm_code(v: object) -> str:
    """str -> strip -> UPPER -> drop a single trailing '.0' (CSV round-trip float-looking code)."""
    s = _norm_str(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s.upper()


# ── per-signal evaluators (each returns fired / quiet / unknown; missing fact -> unknown) ──
def _sig_bridge(row: Mapping[str, object]) -> str:
    if _norm_str(row.get("bridge_basis")) != "observed":
        return _UNKNOWN  # insufficient basis, or a pre-mods bundle without the columns
    flag = _to_flag(row.get("bridge_flag"))
    if flag is None:
        return _UNKNOWN  # defensive: observed basis should always carry a flag
    return _FIRED if flag else _QUIET


def _sig_termination(row: Mapping[str, object]) -> str:
    flag = _to_flag(row.get("terminated"))
    if flag is True:
        return _FIRED  # any termination kind — complete or partial — is displacement-relevant
    if flag is False and _norm_str(row.get("termination_basis")) == "none":
        return _QUIET  # the mods layer's observed absence of a termination event
    return _UNKNOWN


def _sig_deobligation(row: Mapping[str, object]) -> str:
    flag = _to_flag(row.get("has_deobligation"))
    if flag is None:
        return _UNKNOWN
    return _FIRED if flag else _QUIET


def _sig_lapsed_no_successor(row: Mapping[str, object]) -> str:
    status = _norm_str(row.get("candidate_status"))
    if status in _NON_LAPSED_STATUSES:
        return _QUIET  # not recently lapsed — an observed non-lapse
    if status != _LAPSED_STATUS:
        return _UNKNOWN  # stale (quarantined) / blank / unrecognized status — never guessed
    svb = _norm_str(row.get("successor_visible_basis"))
    if svb == "none_visible":
        return _FIRED  # lapsed, and no follow-on visible in public data yet
    if svb == "observed":
        return _QUIET  # a successor is already visible — the window looks closed
    return _UNKNOWN  # insufficient_cell / blank — the cell was unreadable


def _sig_sole_offer(row: Mapping[str, object], cfg: DisplacementConfig) -> str:
    offers = _to_int(row.get("number_of_offers_received"))
    if offers is None:
        return _UNKNOWN
    if offers in cfg.offers_sentinels or offers > cfg.offers_max_plausible or offers < 1:
        return _UNKNOWN  # FPDS junk count — never read as a real offer count
    if offers == SOLE_SOURCE_OFFERS:
        # The lock read requires a COMPETED award (extent A/D/F) — one offer on a
        # sole-source record is definitional, not entrenchment evidence.
        return _FIRED if _norm_code(row.get("extent_competed_code")) in COMPETED_CODES else _UNKNOWN
    return _QUIET


def _sig_size_shift(size_shift: object) -> str:
    if size_shift is _UNAVAILABLE:
        return _UNKNOWN  # no vendor read supplied — gated, noted in displacement_unread
    flag = _to_flag(size_shift)
    if flag is None:
        return _UNKNOWN  # the vendor's own read was insufficient (shift None)
    return _FIRED if flag else _QUIET


def displacement_row(
    row: Mapping[str, object], cfg: DisplacementConfig, *, size_shift: object = _UNAVAILABLE
) -> DisplacementResult:
    """Compute the lane for a single candidate row (see the module docstring's decision
    table). ``size_shift`` is the vendor's ``size_standard_shift`` value joined by the
    caller (``annotate_displacement``); leave it defaulted when no vendor read exists."""
    if _norm_str(row.get("candidate_status")) == _STALE_STATUS:
        # Data-Gap quarantine (2026-07 adversarial review): a stale row's facts are too old
        # to read as CURRENT forward signals — every signal is unknown, and the coverage
        # gate below then refuses the whole lane. A 3-years-lapsed record can never show
        # an observed "k of n" beside its Data Gap tier.
        statuses: dict[str, str] = {s: _UNKNOWN for s in DISPLACEMENT_SIGNALS}
    else:
        statuses = {
            "bridge": _sig_bridge(row),
            "termination": _sig_termination(row),
            "deobligation": _sig_deobligation(row),
            "lapsed_no_successor": _sig_lapsed_no_successor(row),
            "sole_offer": _sig_sole_offer(row, cfg),
            "size_shift": _sig_size_shift(size_shift),
        }
    fired = [s for s in DISPLACEMENT_SIGNALS if statuses[s] == _FIRED]
    unread = [s for s in DISPLACEMENT_SIGNALS if statuses[s] == _UNKNOWN]
    n_read = len(DISPLACEMENT_SIGNALS) - len(unread)
    unread_text = SIGNAL_SEPARATOR.join(unread) if unread else NO_SIGNALS_TOKEN

    # ── coverage gate → insufficient (n_read / unread still published — coverage facts) ──
    if n_read < cfg.min_signals_read:
        return {
            "displacement_signal_count": None,
            "displacement_signals_read": n_read,
            "displacement_signals": None,
            "displacement_unread": unread_text,
            "displacement_band": DISPLACEMENT_BAND_NA,
            "displacement_basis": "insufficient",
        }

    k = len(fired)
    if k >= 2:
        band = "multiple_signals"
    elif k == 1:
        band = "single_signal"
    else:
        band = "none_observed"
    return {
        "displacement_signal_count": k,
        "displacement_signals_read": n_read,
        "displacement_signals": SIGNAL_SEPARATOR.join(fired) if fired else NO_SIGNALS_TOKEN,
        "displacement_unread": unread_text,
        "displacement_band": band,
        "displacement_basis": "observed",
    }


def _size_shift_map(vendor_size_shift: pd.DataFrame | None) -> dict[str, object] | None:
    """UEI -> size_standard_shift lookup from a dim_vendor-shaped frame, or None when the
    read is not cleanly available (no frame, or the join/shift columns are absent) — every
    candidate's size_shift signal then gates Unknown, never a guess. Blank UEIs never key."""
    if vendor_size_shift is None:
        return None
    if not {"incumbent_uei", "size_standard_shift"}.issubset(vendor_size_shift.columns):
        return None
    out: dict[str, object] = {}
    for uei, shift in zip(vendor_size_shift["incumbent_uei"], vendor_size_shift["size_standard_shift"]):
        key = _norm_str(uei)
        if key:
            out[key] = shift
    return out


def annotate_displacement(
    df: pd.DataFrame, cfg: DisplacementConfig, vendor_size_shift: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Return a COPY of the candidate frame with the six DISPLACEMENT_COLUMNS ASSIGNED
    (overwritten, not appended) — so a re-bake is drop-then-rederive idempotent by
    construction, exactly like ``annotate_burn_pressure``. Count columns are pinned to
    Int64 (missing-capable), the four text columns to object. Preserves index, row order,
    and every existing column; drops/filters nothing. ``vendor_size_shift`` is the
    dim_vendor frame carrying ``incumbent_uei`` + ``size_standard_shift`` — pass it only
    when cleanly available; omitted, the size_shift signal reads Unknown. No clock, no RNG."""
    size_map = _size_shift_map(vendor_size_shift)
    out = df.copy()
    results: list[DisplacementResult] = []
    for row in out.to_dict("records"):
        shift: object = _UNAVAILABLE
        if size_map is not None:
            uei = _norm_str(row.get("incumbent_uei"))
            if uei and uei in size_map:
                shift = size_map[uei]
        results.append(displacement_row(row, cfg, size_shift=shift))
    out["displacement_signal_count"] = pd.Series(
        [r["displacement_signal_count"] for r in results], index=out.index, dtype="Int64"
    )
    out["displacement_signals_read"] = pd.Series(
        [r["displacement_signals_read"] for r in results], index=out.index, dtype="Int64"
    )
    for col in ("displacement_signals", "displacement_unread", "displacement_band", "displacement_basis"):
        out[col] = pd.Series([r[col] for r in results], index=out.index, dtype="object")
    return out
