"""
successor_proxy — a pure "is a follow-on already visible?" label for recently-lapsed
recompete candidates (no I/O, no clock).

This applies FORWARD the conservative successor predicate that ships DORMANT in
``scoring.ptw_backtest``: a "successor" is a later award in the same
NAICS × 2-char-PSC-class × subagency cell signed after the predecessor's end date.
For each candidate we ask the same question about its own ``selected_expiration_date``:
is any same-cell award visible that was signed after it?

CRITICAL DOMAIN RULE (the one place this DIVERGES from ptw_backtest): two awards under
the same parent IDV are a **task-order sequence**, NOT a recompete. ptw_backtest's proxy
deliberately INCLUDES same-IDV sequences (its docstring says so, because it is measuring
range coverage, not lineage); here we must EXCLUDE them — a later same-cell award only
counts as a visible successor when it is NOT under the candidate's own parent IDV. A blank
parent on either side is treated as "no shared vehicle", so the exclusion fires only when
BOTH sides carry the same non-blank ``referenced_idv_piid``.

Two candidate columns are ASSIGNED (idempotent, like ``scoring.burn_pressure``):
  * ``successor_visible``       — object: True / False / None
  * ``successor_visible_basis`` — str: "observed" / "none_visible" / "insufficient_cell"

The columns are a LABEL, never a row filter: the frame is returned as a COPY with the two
columns added — no row is dropped, reordered, or otherwise mutated. Priors live in
``config/recompete.yaml``; this module never imports ``utils.config`` (only the reverse
edge exists). Deterministic: ``today`` is injected for interface/determinism parity with
the other signals — the predicate itself reads only recorded facts (award signed dates),
never the wall clock.

HONEST DATA CEILING (documented, shared with ptw_backtest): on the bulk path
``date_signed`` is derived from the award's LATEST transaction's action date (the loader's
latest-txn dedupe), not the original signing date — so "signed after" really means "had
FPDS activity after". The predicate therefore (a) excludes the candidate's own award
(a post-expiration closeout mod is not a follow-on) exactly as ptw_backtest's
``award_id != pred`` clause does, and (b) remains a directional LABEL whose ``observed``
can be triggered by another long-running award's recent modification — which is why the
app copy says "successor visible" and never "recompete awarded".
"""

from __future__ import annotations

import math
from datetime import date
from typing import Mapping

import pandas as pd

SUCCESSOR_COLUMNS: tuple[str, ...] = ("successor_visible", "successor_visible_basis")
SUCCESSOR_BASES: tuple[str, ...] = ("observed", "none_visible", "insufficient_cell")

# A cell key: (naics, 2-char PSC class, awarding subagency), all stripped strings.
CellKey = tuple[str, str, str]


def _cfg_int(raw: Mapping[str, object], key: str) -> int:
    """Coerce a config value to int, rejecting bool and non-int (typed error for the stranger)."""
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"successor config: {key!r} must be an int, got {v!r}")
    return int(v)


def _norm_str(v: object) -> str:
    """Stripped string; '' for None / NaN / blank. A blank cell component makes the cell
    unknowable; a blank ``referenced_idv_piid`` means 'no parent vehicle' (never excluded)."""
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):  # np.float64 subclasses float -> covered
        return ""
    return str(v).strip()


def _build_cell_index(
    classified_awards: pd.DataFrame,
) -> tuple[dict[CellKey, int], dict[CellKey, dict[str, list[tuple[pd.Timestamp, str]]]]]:
    """Index the award pool ONCE (avoids an O(candidates × awards) scan):

    - ``counts``: total awards per cell (the readability gate counts EVERY row in the cell,
      including ones with an unparseable signed date).
    - ``parent_latest``: per cell, the TWO latest ``(signed_date, award_id)`` pairs per
      distinct (stripped) parent IDV. Two, not one, because the sanctioned ptw_backtest
      predicate also excludes the predecessor ITSELF (``award_id != pred["award_id"]``):
      on the bulk path ``date_signed`` is the award's LATEST transaction date, so a
      candidate's own post-expiration closeout mod would otherwise make it its own
      "successor" — keeping a runner-up lets the lookup skip the self-award and still
      answer from the bucket's next-latest.

    Signed dates are parsed with ``pd.to_datetime(errors="coerce")`` — one real column carries
    a trailing ' 00:00:00', so a strict parse is never safe. NaT-signed awards still count
    toward ``counts`` but never contribute a visible successor date.
    """
    counts: dict[CellKey, int] = {}
    parent_latest: dict[CellKey, dict[str, list[tuple[pd.Timestamp, str]]]] = {}
    if classified_awards.empty:
        return counts, parent_latest

    signed = pd.to_datetime(classified_awards["date_signed"], errors="coerce")
    for naics_v, psc_v, sub_v, parent_v, award_v, signed_ts in zip(
        classified_awards["naics"],
        classified_awards["psc"],
        classified_awards["awarding_subagency_clean"],
        classified_awards["referenced_idv_piid"],
        classified_awards["award_id"],
        signed,
    ):
        key = (_norm_str(naics_v), _norm_str(psc_v)[:2], _norm_str(sub_v))
        counts[key] = counts.get(key, 0) + 1
        if pd.isna(signed_ts):
            continue
        par = _norm_str(parent_v)
        bucket = parent_latest.setdefault(key, {}).setdefault(par, [])
        bucket.append((signed_ts, _norm_str(award_v)))
        bucket.sort(key=lambda t: t[0], reverse=True)  # tiny (<= 3 items) sort
        del bucket[2:]
    return counts, parent_latest


def _successor_for(
    naics_v: object,
    psc_v: object,
    sub_v: object,
    parent_v: object,
    own_award_id: str,
    exp_ts: pd.Timestamp,
    counts: Mapping[CellKey, int],
    parent_latest: Mapping[CellKey, Mapping[str, list[tuple[pd.Timestamp, str]]]],
    min_cell_awards: int,
    signed_after_days: int,
) -> tuple[bool | None, str]:
    """The pure per-candidate predicate. First failing gate wins → insufficient/None."""
    naics = _norm_str(naics_v)
    psc2 = _norm_str(psc_v)[:2]
    sub = _norm_str(sub_v)
    if not naics or not psc2 or not sub:  # a missing/blank component makes the cell unknowable
        return None, "insufficient_cell"
    key: CellKey = (naics, psc2, sub)
    if counts.get(key, 0) < min_cell_awards:  # too thin a cell to read a follow-on from
        return None, "insufficient_cell"
    if pd.isna(exp_ts):  # no anchor date to look "after"
        return None, "insufficient_cell"

    threshold = exp_ts + pd.Timedelta(days=signed_after_days)
    parent = _norm_str(parent_v)
    best: pd.Timestamp | None = None
    for par, pairs in parent_latest.get(key, {}).items():
        if parent and par == parent:
            continue  # same non-blank parent IDV -> task-order sequence, NOT a recompete
        for ts, aid in pairs:  # sorted desc: the first non-self entry is the bucket's max
            if own_award_id and aid == own_award_id:
                continue  # a candidate is never its own successor (ptw_backtest predicate)
            if best is None or ts > best:
                best = ts
            break
    if best is not None and best > threshold:
        return True, "observed"
    return False, "none_visible"


def annotate_successor_visible(
    candidates: pd.DataFrame,
    classified_awards: pd.DataFrame,
    cfg: Mapping[str, object],
    today: date,
) -> pd.DataFrame:
    """Return a COPY of the candidate frame with ``successor_visible`` (object True/False/None)
    and ``successor_visible_basis`` (str) ASSIGNED — additive and idempotent (a re-annotate
    overwrites, never appends duplicates). Preserves index, row order, and every existing
    column. No clock, no RNG; ``today`` is accepted for interface/determinism parity but the
    predicate reads only recorded award facts (see the module docstring).
    """
    min_cell_awards = _cfg_int(cfg, "successor_min_cell_awards")
    signed_after_days = _cfg_int(cfg, "successor_signed_after_days")
    if min_cell_awards < 2:
        raise ValueError("successor: successor_min_cell_awards must be >= 2")
    if signed_after_days < 0:
        raise ValueError("successor: successor_signed_after_days must be >= 0")

    out = candidates.copy()
    counts, parent_latest = _build_cell_index(classified_awards)
    exp_parsed = pd.to_datetime(out["selected_expiration_date"], errors="coerce")

    values: list[bool | None] = []
    bases: list[str] = []
    for naics_v, psc_v, sub_v, parent_v, own_award_v, exp_ts in zip(
        out["naics"],
        out["psc"],
        out["subagency"],
        out["referenced_idv_piid"],
        out["source_award_id"],
        exp_parsed,
    ):
        visible, basis = _successor_for(
            naics_v,
            psc_v,
            sub_v,
            parent_v,
            _norm_str(own_award_v),
            exp_ts,
            counts,
            parent_latest,
            min_cell_awards,
            signed_after_days,
        )
        values.append(visible)
        bases.append(basis)

    out["successor_visible"] = pd.Series(values, index=out.index, dtype="object")
    out["successor_visible_basis"] = pd.Series(bases, index=out.index, dtype="object")
    return out
