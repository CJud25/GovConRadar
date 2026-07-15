"""
mods_signal — per-award transaction-history digest and (A2) the termination / ceiling /
velocity / bridge signal built from it (pure).

The bulk loader collapses each award to its LATEST transaction for the award pipeline;
this module is the parallel accumulation that keeps what that dedupe discards: the
modification history. ``fold_transaction`` is a pure reducer the loader calls once per
in-scope, non-duplicate transaction row; the digest it builds is compact (counts, date
reductions, and a handful of signal-bearing events — never full rows).

Every signal derived from a digest is an ESTIMATE with a named basis and a coverage gate
to Unknown (A2). Deterministic: no clock, no RNG, no I/O; config enters as a ``Mapping``
(A2), never via ``utils.config`` (only the reverse edge exists). Mirrors the house idiom
of ``src/scoring/burn_pressure.py``.

Measured field semantics this module is built on (Phase 0, 2026-07-12, full intake):
  * ``action_type_code`` is the reason-for-modification code; terminations are
    ``{E, F, X, N}`` and ``K`` (Close Out) is a guarded false-positive, never a termination.
  * ``base_and_all_options_value`` on these transaction-level exports is the mod's OWN
    change to the ceiling (a delta; 0 for most mods), NOT the cumulative award ceiling —
    the cumulative base-and-all-options ceiling is ``potential_total_value_of_award``,
    which is what the ceiling first/last reduction reads (positive values only).
  * ``period_of_performance_potential_end_date`` carries a ``" 00:00:00"`` suffix in the
    real exports (every other date column is bare ISO). The digest stores date strings
    RAW; lexical comparison is used ONLY within the bare-ISO ``action_date`` column
    (min/max reductions) and within ``current_end_date``; every cross-column date
    comparison must go through a real parser (A2's ``_to_date``).
  * The FY24/FY25 Delta member re-lists archive transactions (100% txn-key overlap
    measured), so the LOADER dedupes on ``contract_transaction_unique_key`` before
    folding — the fold itself is duplicate-sensitive by design.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, TypedDict

import pandas as pd

# Reason-for-modification codes (action_type_code). E=terminate for default,
# F=terminate for convenience, X=terminate for cause, N=legal contract cancellation.
TERMINATION_CODES: frozenset[str] = frozenset({"E", "F", "X", "N"})
# K = Close Out: routine administrative closure, a guarded false-positive — NEVER a termination.
CLOSEOUT_CODE: str = "K"
# correction_delete_ind value marking a retracted transaction (delta files only): never folds.
DELETE_MARKER: str = "D"
# extent_competed_code sole-source family — the fold's bridge-candidate filter. The A2
# config's bridge_noncompeted_codes MUST equal this set (asserted in load_mods_config):
# the fold accumulates on this constant, so a divergent yaml would silently under-detect.
NONCOMPETED_EXTENT_CODES: frozenset[str] = frozenset({"B", "C", "G"})
# The fold keeps negative-obligation events at/below this structural floor (config-free
# superset); the A2 config's deobligation_floor_usd must be <= this (asserted there).
NEGATIVE_EVENT_FLOOR: float = -10_000.0
# Transaction descriptions can run to tens of KB; events keep a bounded prefix (the full
# text stays behind source_url — the digest is compact by design, R1).
_DESC_CAP: int = 500


class TxnEvent(TypedDict):
    """One signal-bearing transaction, kept verbatim-but-bounded for evidence rows."""

    action_date: str | None
    action_type_code: str | None
    modification_number: str | None
    obligation: float | None  # federal_action_obligation — the txn's obligation DELTA
    current_end_date: str | None
    extent_competed_code: str | None
    ceiling_total: float | None  # potential_total_value_of_award — CUMULATIVE ceiling
    description: str | None


class TransactionDigest(TypedDict):
    """Compact per-award accumulation. All ``first_*``/``last_*`` fields are min/max
    reductions KEYED BY action_date (bare-ISO, lexical) — never encounter order: files
    fold in sorted-path order, so encounter-"first" is not chronological-first."""

    award_id: str
    mod_count: int
    first_action_date: str | None
    last_action_date: str | None
    first_ceiling_event: TxnEvent | None  # earliest txn with a POSITIVE cumulative ceiling
    last_ceiling_event: TxnEvent | None  # latest such txn
    first_potential_end_date: str | None  # potential end at the earliest txn carrying one (RAW string)
    first_potential_end_anchor: str | None  # that txn's action_date (the reduction key)
    last_current_end_date: str | None  # END-OF-RECORD: current end at the LATEST txn carrying one
    last_current_end_anchor: str | None  # that txn's action_date (the reduction key)
    termination_events: list[TxnEvent]
    closeout_events: list[TxnEvent]
    negative_events: list[TxnEvent]
    bridge_candidate: TxnEvent | None  # non-competed txn with the max current_end_date


def _opt_str(v: object) -> str | None:
    """Stripped string or None when blank/None."""
    if v is None:
        return None
    text = str(v).strip()
    return text or None


def _norm_code(v: object) -> str | None:
    """Short FPDS code cell -> stripped UPPER string or None."""
    s = _opt_str(v)
    return s.upper() if s else None


def _opt_float(v: object) -> float | None:
    """None on None/NaN/inf/unparseable; a finite float otherwise. May be negative."""
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _empty_digest(award_id: str) -> TransactionDigest:
    return {
        "award_id": award_id,
        "mod_count": 0,
        "first_action_date": None,
        "last_action_date": None,
        "first_ceiling_event": None,
        "last_ceiling_event": None,
        "first_potential_end_date": None,
        "first_potential_end_anchor": None,
        "last_current_end_date": None,
        "last_current_end_anchor": None,
        "termination_events": [],
        "closeout_events": [],
        "negative_events": [],
        "bridge_candidate": None,
    }


def _event(fields: Mapping[str, object]) -> TxnEvent:
    desc = _opt_str(fields.get("transaction_description"))
    return {
        "action_date": _opt_str(fields.get("action_date")),
        "action_type_code": _norm_code(fields.get("action_type_code")),
        "modification_number": _opt_str(fields.get("modification_number")),
        "obligation": _opt_float(fields.get("federal_action_obligation")),
        "current_end_date": _opt_str(fields.get("current_end_date")),
        "extent_competed_code": _norm_code(fields.get("extent_competed_code")),
        "ceiling_total": _opt_float(fields.get("potential_total_value_of_award")),
        "description": desc[:_DESC_CAP] if desc else None,
    }


def fold_transaction(digest: TransactionDigest | None, fields: Mapping[str, object]) -> TransactionDigest | None:
    """Fold one in-scope transaction row into the award's digest (pure reducer).

    ``fields`` carries the raw per-transaction values the loader already reads:
    ``award_id, action_date, action_type_code, federal_action_obligation,
    base_and_all_options_value, potential_total_value_of_award, current_end_date,
    potential_end_date, extent_competed_code, modification_number,
    transaction_description, correction_delete_ind``. (``base_and_all_options_value``
    is accepted for evidence completeness but the ceiling reduction reads the
    CUMULATIVE ``potential_total_value_of_award`` — see the module docstring.)

    A delete-marked row (``correction_delete_ind == "D"``, delta files only) is a
    retracted transaction and returns the digest UNCHANGED — it must never fold as
    live termination/mod evidence, which would forge a false ghost-fix. A blank or
    ``"C"`` (correction) marker folds normally.
    """
    if _norm_code(fields.get("correction_delete_ind")) == DELETE_MARKER:
        return digest
    if digest is None:
        digest = _empty_digest(_opt_str(fields.get("award_id")) or "")
    ev = _event(fields)
    digest["mod_count"] += 1

    ad = ev["action_date"]
    if ad is not None:
        # Bare-ISO action_date sorts lexically (loader docstring); same-column compares only.
        first_ad = digest["first_action_date"]
        if first_ad is None or ad < first_ad:
            digest["first_action_date"] = ad
        last_ad = digest["last_action_date"]
        if last_ad is None or ad > last_ad:
            digest["last_action_date"] = ad

        ceiling = ev["ceiling_total"]
        if ceiling is not None and ceiling > 0.0:
            fce = digest["first_ceiling_event"]
            if fce is None or fce["action_date"] is None or ad < fce["action_date"]:
                digest["first_ceiling_event"] = ev
            lce = digest["last_ceiling_event"]
            if lce is None or lce["action_date"] is None or ad > lce["action_date"]:
                digest["last_ceiling_event"] = ev

        pope = _opt_str(fields.get("potential_end_date"))
        if pope is not None:
            anchor = digest["first_potential_end_anchor"]
            if anchor is None or ad < anchor:
                digest["first_potential_end_anchor"] = ad
                digest["first_potential_end_date"] = pope

        # END-OF-RECORD reduction: the current end at the latest dated txn of ANY type —
        # the completeness anchor for the termination read (a post-termination extension
        # moves this forward, so a re-extended contract can never stay ghost-fixed).
        if ev["current_end_date"] is not None:
            end_anchor = digest["last_current_end_anchor"]
            if end_anchor is None or ad > end_anchor:
                digest["last_current_end_anchor"] = ad
                digest["last_current_end_date"] = ev["current_end_date"]

    code = ev["action_type_code"]
    if code is not None and code in TERMINATION_CODES:
        digest["termination_events"].append(ev)
    elif code == CLOSEOUT_CODE:
        digest["closeout_events"].append(ev)

    obligation = ev["obligation"]
    if obligation is not None and obligation < NEGATIVE_EVENT_FLOOR:
        digest["negative_events"].append(ev)

    if ev["extent_competed_code"] in NONCOMPETED_EXTENT_CODES and ev["current_end_date"] is not None:
        bc = digest["bridge_candidate"]
        cur_end = ev["current_end_date"]
        if bc is None or bc["current_end_date"] is None or cur_end > bc["current_end_date"]:
            digest["bridge_candidate"] = ev

    return digest


# ═══════════════════ A2 — digest summarizers (signals with named bases) ═══════════════════

MOD_COLUMNS: tuple[str, ...] = (
    "terminated",
    "termination_code",
    "termination_action_date",
    "termination_kind",
    "termination_basis",
    "mod_count",
    "mod_velocity",
    "mod_velocity_band",
    "ceiling_growth_ratio",
    "ceiling_balloon_flag",
    "ceiling_basis",
    "has_deobligation",
    "bridge_flag",
    "bridge_basis",
    "mods_basis",
)
TERMINATION_KINDS: tuple[str, ...] = ("complete_likely", "partial_or_unclear", "none")
VELOCITY_BANDS: tuple[str, ...] = ("low", "normal", "high")
VELOCITY_BAND_NA: str = "not_applicable"
TERMINATION_BASES: tuple[str, ...] = ("observed_code", "none")
CEILING_BASES: tuple[str, ...] = ("measured", "insufficient")
BRIDGE_BASES: tuple[str, ...] = ("observed", "insufficient")
MODS_BASES: tuple[str, ...] = ("measured", "single_transaction", "insufficient")


class ModResult(TypedDict):
    """One award's summarized mod signals — the 15 MOD_COLUMNS, in order."""

    terminated: bool
    termination_code: str | None
    termination_action_date: str | None  # the event's raw bare-ISO action_date, or None if undated
    termination_kind: str  # one of TERMINATION_KINDS — never ""
    termination_basis: str  # one of TERMINATION_BASES
    mod_count: int
    mod_velocity: float | None
    mod_velocity_band: str  # one of VELOCITY_BANDS or VELOCITY_BAND_NA — never ""
    ceiling_growth_ratio: float | None
    ceiling_balloon_flag: bool
    ceiling_basis: str  # one of CEILING_BASES
    has_deobligation: bool
    bridge_flag: bool
    bridge_basis: str  # one of BRIDGE_BASES
    mods_basis: str  # one of MODS_BASES


@dataclass(frozen=True)
class ModsConfig:
    termination_codes: frozenset[str]  # MUST equal TERMINATION_CODES {E,F,X,N}
    complete_grace_days: int  # 31
    min_transactions: int  # 2
    ceiling_balloon_ratio: float  # 1.50
    deobligation_floor_usd: float  # -100000 (must be <= NEGATIVE_EVENT_FLOOR)
    velocity_low: float  # 1.0
    velocity_high: float  # 10.0
    bridge_min_extension_days: int  # 30
    bridge_noncompeted_codes: frozenset[str]  # MUST equal NONCOMPETED_EXTENT_CODES {B,C,G}


# ── config coercion helpers (isinstance-narrowing → mypy-strict clean; typed errors for the stranger) ──
def _cfg_num(raw: Mapping[str, object], key: str) -> float:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"mods config: {key!r} must be a real number, got {v!r}")
    return float(v)


def _cfg_int(raw: Mapping[str, object], key: str) -> int:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"mods config: {key!r} must be an int, got {v!r}")
    return int(v)


def _cfg_str_set(raw: Mapping[str, object], key: str) -> frozenset[str]:
    v = raw[key]
    if not isinstance(v, (list, tuple)):
        raise ValueError(f"mods config: {key!r} must be a list/tuple, got {v!r}")
    return frozenset(str(x).strip().upper() for x in v)


def load_mods_config(raw: Mapping[str, object]) -> ModsConfig:
    """Coerce + VALIDATE the config mapping into a frozen ModsConfig. Raises ValueError on a
    wrong-typed or incoherent prior — including DIVERGENCE from the fold's constants: the fold
    accumulates on TERMINATION_CODES / NONCOMPETED_EXTENT_CODES / NEGATIVE_EVENT_FLOOR, so a
    yaml that disagrees would silently mislabel terminations, under-detect bridges, or set a
    deobligation floor that could never fire. This is the 'config as a Mapping' boundary that
    keeps the module self-contained (callers pass utils.config.MODS_SIGNAL)."""
    cfg = ModsConfig(
        termination_codes=_cfg_str_set(raw, "termination_codes"),
        complete_grace_days=_cfg_int(raw, "complete_grace_days"),
        min_transactions=_cfg_int(raw, "min_transactions"),
        ceiling_balloon_ratio=_cfg_num(raw, "ceiling_balloon_ratio"),
        deobligation_floor_usd=_cfg_num(raw, "deobligation_floor_usd"),
        velocity_low=_cfg_num(raw, "velocity_low"),
        velocity_high=_cfg_num(raw, "velocity_high"),
        bridge_min_extension_days=_cfg_int(raw, "bridge_min_extension_days"),
        bridge_noncompeted_codes=_cfg_str_set(raw, "bridge_noncompeted_codes"),
    )
    if cfg.termination_codes != TERMINATION_CODES:
        raise ValueError(
            f"mods: termination_codes must equal the fold's TERMINATION_CODES "
            f"{sorted(TERMINATION_CODES)} (a divergent yaml would silently mislabel), "
            f"got {sorted(cfg.termination_codes)}"
        )
    if cfg.bridge_noncompeted_codes != NONCOMPETED_EXTENT_CODES:
        raise ValueError(
            f"mods: bridge_noncompeted_codes must equal the fold's NONCOMPETED_EXTENT_CODES "
            f"{sorted(NONCOMPETED_EXTENT_CODES)} (a divergent yaml would silently under-detect), "
            f"got {sorted(cfg.bridge_noncompeted_codes)}"
        )
    if cfg.deobligation_floor_usd > NEGATIVE_EVENT_FLOOR:
        raise ValueError(
            f"mods: deobligation_floor_usd must be <= the fold's NEGATIVE_EVENT_FLOOR "
            f"({NEGATIVE_EVENT_FLOOR}) — the fold only keeps negatives at/below it, so a "
            f"higher config floor could never fire; got {cfg.deobligation_floor_usd}"
        )
    if not (0.0 < cfg.velocity_low < cfg.velocity_high):
        raise ValueError("mods: 0 < velocity_low < velocity_high required")
    if cfg.ceiling_balloon_ratio <= 1.0:
        raise ValueError("mods: ceiling_balloon_ratio must be > 1.0")
    if cfg.complete_grace_days <= 0:
        raise ValueError("mods: complete_grace_days must be > 0")
    if cfg.min_transactions < 1:
        raise ValueError("mods: min_transactions must be >= 1")
    if cfg.bridge_min_extension_days <= 0:
        raise ValueError("mods: bridge_min_extension_days must be > 0")
    return cfg


def _to_date(v: object) -> date | None:
    """Accepts pandas Timestamp / ISO str / date / None / NaT -> date | None. EVERY cross-column
    date comparison goes through this: the real exports' potential_end_date carries a
    ``" 00:00:00"`` suffix that a strict %Y-%m-%d parse would silently NaT (measured: 100%)."""
    ts = pd.to_datetime(v, errors="coerce")
    if ts is None or pd.isna(ts):
        return None
    return date(int(ts.year), int(ts.month), int(ts.day))


# ── defensive digest access: digests may arrive JSON-round-tripped (plain dicts) ──
def _events_of(digest: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    v = digest.get(key)
    if not isinstance(v, (list, tuple)):
        return []
    return [e for e in v if isinstance(e, Mapping)]


def _event_of(digest: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    v = digest.get(key)
    return v if isinstance(v, Mapping) else None


def _termination_order(ev: Mapping[str, object]) -> tuple[bool, date, str, str]:
    """Sort key for termination events: dated events order chronologically and sort BEFORE
    undated ones (so max() picks the LATEST dated event — the termination action OF RECORD);
    ties break on (raw action_date, modification_number) for determinism."""
    d = _to_date(ev.get("action_date"))
    return (
        d is not None,
        d or date.min,
        _opt_str(ev.get("action_date")) or "",
        _opt_str(ev.get("modification_number")) or "",
    )


def summarize_digest(digest: Mapping[str, object], cfg: ModsConfig) -> ModResult:
    """Summarize one award's digest into the 15 MOD_COLUMNS (see the plan's decision table —
    first-match-wins PER SIGNAL, each signal INDEPENDENT: a coverage gate on one signal never
    gates another. The min_transactions gate applies ONLY to the history-derived velocity /
    ceiling reads, never to the event-observed termination / deobligation / bridge signals."""
    # ── TERMINATION (event-observed; never count-gated) ───────────────────────────────
    term_events = _events_of(digest, "termination_events")
    terminated = bool(term_events)
    termination_code: str | None = None
    termination_action_date: str | None = None
    termination_kind = "none"
    termination_basis = "none"
    if term_events:
        # The termination OF RECORD is the LATEST dated event — the final word on the
        # contract's end state. (Adversarial review, 2026-07-13: the original earliest-event
        # rule both MASKED a later complete termination behind an early partial one — 4 live
        # rows — and let an early complete termination ghost-fix a contract whose end was
        # later RE-EXTENDED — 7 live rows.)
        of_record = max(term_events, key=_termination_order)
        termination_code = _norm_code(of_record.get("action_type_code"))
        termination_action_date = _opt_str(of_record.get("action_date"))
        termination_basis = "observed_code"
        term_date = _to_date(of_record.get("action_date"))
        # Completeness is judged against the award's END-OF-RECORD (current_end at the
        # latest dated transaction of ANY type), so a post-termination re-extension can
        # never leave the row quarantined. Older digests without the reduction fall back
        # to the of-record event's own reported end (the original behavior).
        end_of_record = _to_date(digest.get("last_current_end_date"))
        if end_of_record is None:
            end_of_record = _to_date(of_record.get("current_end_date"))
        # complete_likely IFF BOTH dates parse and the end-of-record lands within the grace
        # window; ANY missing/unparseable date -> partial_or_unclear, NEVER complete_likely
        # (protects the downstream invariant complete_likely => ghost-fix fired).
        if (
            term_date is not None
            and end_of_record is not None
            and end_of_record <= term_date + timedelta(days=cfg.complete_grace_days)
        ):
            termination_kind = "complete_likely"
        else:
            termination_kind = "partial_or_unclear"

    # ── CEILING (first/last CUMULATIVE positive-ceiling reduction) ─────────────────────
    fce = _event_of(digest, "first_ceiling_event")
    lce = _event_of(digest, "last_ceiling_event")
    ceiling_growth_ratio: float | None = None
    ceiling_balloon_flag = False
    ceiling_basis = "insufficient"
    if fce is not None and lce is not None:
        # Same physical transaction (<2 positive-ceiling txns or same-day-only data) -> no read.
        same_txn = _opt_str(fce.get("action_date")) == _opt_str(lce.get("action_date")) and _opt_str(
            fce.get("modification_number")
        ) == _opt_str(lce.get("modification_number"))
        first_total = _opt_float(fce.get("ceiling_total"))
        last_total = _opt_float(lce.get("ceiling_total"))
        # first_total > 0 is defensive (the fold already filters positives).
        if not same_txn and first_total is not None and first_total > 0.0 and last_total is not None:
            ceiling_growth_ratio = round(last_total / first_total, 4)
            ceiling_balloon_flag = ceiling_growth_ratio > cfg.ceiling_balloon_ratio
            ceiling_basis = "measured"

    # ── VELOCITY (history-derived; min_transactions-gated) ─────────────────────────────
    mc = _opt_float(digest.get("mod_count"))
    mod_count = int(mc) if mc is not None else 0
    first_ad = _to_date(digest.get("first_action_date"))
    last_ad = _to_date(digest.get("last_action_date"))
    active_years: float | None = None
    if first_ad is not None and last_ad is not None:
        active_years = (last_ad - first_ad).days / 365.25
    mod_velocity: float | None = None
    mod_velocity_band = VELOCITY_BAND_NA
    if mod_count >= cfg.min_transactions and active_years is not None and active_years > 0.0:
        mod_velocity = round(mod_count / active_years, 4)
        if mod_velocity > cfg.velocity_high:  # strict compares: the boundary is "normal"
            mod_velocity_band = "high"
        elif mod_velocity < cfg.velocity_low:
            mod_velocity_band = "low"
        else:
            mod_velocity_band = "normal"

    # ── DEOBLIGATION (weak, K-guarded, timing-gated) ───────────────────────────────────
    # negative_events CAN contain K-coded and termination-coded events — the K filter
    # happens HERE, not in the fold. Unverifiable timing (either date unparseable) -> False.
    fped = _to_date(digest.get("first_potential_end_date"))
    has_deobligation = False
    if fped is not None:
        for ev in _events_of(digest, "negative_events"):
            if _norm_code(ev.get("action_type_code")) == CLOSEOUT_CODE:
                continue
            obligation = _opt_float(ev.get("obligation"))
            if obligation is None or obligation >= cfg.deobligation_floor_usd:
                continue
            ev_date = _to_date(ev.get("action_date"))
            if ev_date is not None and ev_date < fped:
                has_deobligation = True
                break

    # ── BRIDGE (event-observed vs the ORIGINAL planned end) ────────────────────────────
    bridge_flag = False
    bridge_basis = "insufficient"
    if fped is not None:
        # A parseable planned end with no non-competed candidate is an OBSERVED non-bridge.
        bridge_basis = "observed"
        bc = _event_of(digest, "bridge_candidate")
        if bc is not None:
            bc_end = _to_date(bc.get("current_end_date"))
            if bc_end is not None and bc_end > fped + timedelta(days=cfg.bridge_min_extension_days):
                bridge_flag = True

    # ── MODS_BASIS (history-coverage label for the count/velocity/ceiling reads) ────────
    if mod_count < cfg.min_transactions:
        mods_basis = "single_transaction"
    elif first_ad is None or last_ad is None:
        mods_basis = "insufficient"
    else:
        mods_basis = "measured"

    return {
        "terminated": terminated,
        "termination_code": termination_code,
        "termination_action_date": termination_action_date,
        "termination_kind": termination_kind,
        "termination_basis": termination_basis,
        "mod_count": mod_count,
        "mod_velocity": mod_velocity,
        "mod_velocity_band": mod_velocity_band,
        "ceiling_growth_ratio": ceiling_growth_ratio,
        "ceiling_balloon_flag": ceiling_balloon_flag,
        "ceiling_basis": ceiling_basis,
        "has_deobligation": has_deobligation,
        "bridge_flag": bridge_flag,
        "bridge_basis": bridge_basis,
        "mods_basis": mods_basis,
    }


def evidence_rows(award_id: str, digest: Mapping[str, object], cfg: ModsConfig) -> list[dict[str, object]]:
    """The signal-bearing fact_transactions rows for one award: all termination + closeout
    events, negatives below the config floor, the bridge candidate iff the summarized
    bridge_flag fired, and the two ceiling endpoints iff the balloon flag fired. The same
    physical txn can appear in multiple digest lists (e.g. a termination that is also a big
    deobligation), so the union DEDUPES on (modification_number, action_date,
    action_type_code, obligation) and each txn emits ONCE. transaction_id is unique per
    award by construction (collision -> ':action_date' suffix -> ':ordinal' suffix)."""
    summary = summarize_digest(digest, cfg)
    pool: list[Mapping[str, object]] = []
    pool.extend(_events_of(digest, "termination_events"))
    pool.extend(_events_of(digest, "closeout_events"))
    for ev in _events_of(digest, "negative_events"):
        obligation = _opt_float(ev.get("obligation"))
        if obligation is not None and obligation < cfg.deobligation_floor_usd:
            pool.append(ev)
    if summary["bridge_flag"]:
        bc = _event_of(digest, "bridge_candidate")
        if bc is not None:
            pool.append(bc)
    if summary["ceiling_balloon_flag"]:
        for key in ("first_ceiling_event", "last_ceiling_event"):
            ce = _event_of(digest, key)
            if ce is not None:
                pool.append(ce)

    seen: set[tuple[str | None, str | None, str | None, float | None]] = set()
    unique: list[Mapping[str, object]] = []
    for ev in pool:
        txn_key = (
            _opt_str(ev.get("modification_number")),
            _opt_str(ev.get("action_date")),
            _norm_code(ev.get("action_type_code")),
            _opt_float(ev.get("obligation")),
        )
        if txn_key in seen:
            continue
        seen.add(txn_key)
        unique.append(ev)
    unique.sort(
        key=lambda e: (
            _opt_str(e.get("action_date")) or "9999-12-31",
            _opt_str(e.get("modification_number")) or "",
            _norm_code(e.get("action_type_code")) or "",
        )
    )

    rows: list[dict[str, object]] = []
    used_ids: set[str] = set()
    for ordinal, ev in enumerate(unique):
        mod = _opt_str(ev.get("modification_number"))
        ad = _opt_str(ev.get("action_date"))
        tid = f"{award_id}:{mod or 'NONE'}"
        if tid in used_ids:  # duplicate/missing mod numbers within the award
            tid = f"{tid}:{ad or 'ND'}"
        if tid in used_ids:  # still colliding -> the sort ordinal (deterministic)
            tid = f"{tid}:{ordinal}"
        used_ids.add(tid)
        rows.append(
            {
                "transaction_id": tid,
                "award_id": award_id,
                "modification_number": mod,
                "action_date": ad,
                "action_type_code": _norm_code(ev.get("action_type_code")),
                "action_obligation": _opt_float(ev.get("obligation")),
                "description": _opt_str(ev.get("description")),
            }
        )
    return rows


_SUMMARY_DTYPES: dict[str, str] = {
    "mod_count": "Int64",
    "mod_velocity": "float64",
    "ceiling_growth_ratio": "float64",
    "terminated": "bool",
    "ceiling_balloon_flag": "bool",
    "has_deobligation": "bool",
    "bridge_flag": "bool",
}  # everything else object
_EVIDENCE_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "award_id",
    "modification_number",
    "action_date",
    "action_type_code",
    "action_obligation",
    "description",
)


def summarize_frame(digests: Mapping[str, Mapping[str, object]], cfg: ModsConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(mod_summary, transactions_evidence) for a bundle of digests. Rows sort by award_id
    (determinism); dtypes are PINNED like annotate_burn_pressure — mod_count Int64,
    mod_velocity/ceiling_growth_ratio/action_obligation float64, the four flags bool,
    everything else object — and BOTH frames carry their full column sets even when
    ``digests`` is empty. No clock, no RNG, no I/O."""
    award_ids = sorted(digests)
    results: list[Mapping[str, object]] = [summarize_digest(digests[a], cfg) for a in award_ids]
    summary = pd.DataFrame(index=pd.RangeIndex(len(award_ids)))
    summary["award_id"] = pd.Series(award_ids, index=summary.index, dtype="object")
    for col in MOD_COLUMNS:
        summary[col] = pd.Series(
            [r[col] for r in results], index=summary.index, dtype=_SUMMARY_DTYPES.get(col, "object")
        )

    ev_rows: list[dict[str, object]] = []
    for a in award_ids:
        ev_rows.extend(evidence_rows(a, digests[a], cfg))
    evidence = pd.DataFrame(index=pd.RangeIndex(len(ev_rows)))
    for col in _EVIDENCE_COLUMNS:
        dtype = "float64" if col == "action_obligation" else "object"
        evidence[col] = pd.Series([r[col] for r in ev_rows], index=evidence.index, dtype=dtype)
    return summary, evidence
