"""
market_concentration — descriptive incumbent dollar-share on the recompete pipeline (pure).

For each DoD component (``subagency``) in the reportable recompete set this answers one
descriptive question: *what share of the component's expiring obligated dollars does its single
largest incumbent hold, and across how many incumbents?* It is a ratio of two sums of published
``total_obligated_amount`` facts — never a market-share, market-power, or contestability claim
(Corrections v2, Option A). Two honesty gates refuse to score a market that is too thin (fewer
than ``min_market_ueis`` incumbents) or too poorly attributed (more than ``max_unknown_uei_share``
of dollars lack an incumbent UEI): such a market renders ``Unknown`` (``top_share is None``), never
an imputed number.

NO Herfindahl-Hirschman Index and NO DOJ/FTC bands are computed — Corrections v2 dropped both;
``top_share`` + ``n_ueis`` express concentration floor-free and more legibly than an HHI dominated
by its ``10000/n`` floor. Config priors are injected as a ``Mapping`` (callers pass
``utils.config.HHI_CONCENTRATION_CONFIG``); this module imports no first-party code, so registering
it under strict mypy cannot drag untyped code into the gate. Pure and deterministic: no I/O, no
clock, no RNG. Mirrors the house idiom of ``src/scoring/eligibility.py``.

Two consumers, one rule: the Incumbent Landscape view computes the read live, and
:func:`annotate_agency_concentration` (F4) bakes the same read per-component onto
``dim_agency`` (the ``CONCENTRATION_COLUMNS``) so the capture brief's Office section can
show the buying office's concentration — double-gated Unknown riding through unchanged.

Grep removal token: ``hhi_concentration``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

_INPUT_COLUMNS: tuple[str, ...] = ("subagency", "incumbent_uei", "total_obligated_amount")

# The per-component columns annotate_agency_concentration bakes onto dim_agency (F4) —
# the same market-grain read, joined to the grain the capture brief's Office section
# actually consumes. Baked-grain equivalence (unforgeable Unknown, pinned by tests and
# the validator): ``basis == "observed" <=> top_share present <=> reason empty``;
# ``concentration_n_ueis`` is ALWAYS published (a coverage fact, the
# displacement_signals_read pattern).
CONCENTRATION_COLUMNS: tuple[str, ...] = (
    "concentration_top_share",
    "concentration_n_ueis",
    "concentration_basis",
    "concentration_reason",
)
CONCENTRATION_BASES: tuple[str, ...] = ("observed", "insufficient")
# The refusal reason for a component with no reportable candidate rows at all — such a
# component never reaches compute_hhi_concentration (there is no market to assess).
NO_REPORTABLE_REASON = "no reportable candidate rows for this component"


@dataclass(frozen=True)
class MarketConcentration:
    market: str
    top_share: float | None  # None <=> not assessable (Unknown); otherwise in (0, 1]
    n_ueis: int
    coverage: float  # fraction of market dollars attributed to a positive-net incumbent UEI
    market_net: float
    assessable: bool
    reason: str


def _blank(series: pd.Series) -> pd.Series:
    """Boolean mask, True where the value is null / empty / a stringified NaN — a row we
    cannot attribute to a UEI or a market. Vectorized; no per-row Python."""
    s = series.astype(str).str.strip()
    return series.isna() | s.eq("") | s.str.lower().eq("nan")


def _unknown(market: str, n_ueis: int, coverage: float, market_net: float, reason: str) -> MarketConcentration:
    return MarketConcentration(
        market=market,
        top_share=None,
        n_ueis=n_ueis,
        coverage=coverage,
        market_net=market_net,
        assessable=False,
        reason=reason,
    )


def _assess_market(market: str, grp: pd.DataFrame, min_ueis: float, max_unknown_share: float) -> MarketConcentration:
    """Assess one DoD component. Both honesty gates fire BEFORE any share division (Corrections
    C3.2), and an explicit non-positive ``market_net`` guard protects the coverage denominator —
    so an all-unattributed or all-zero-dollar market returns Unknown, never a ZeroDivisionError.
    The coverage ratio itself is guarded inline (``market_net > 0``) so it is always storable."""
    market_net = float(grp["_net"].sum())
    attributed = grp[~_blank(grp["incumbent_uei"])]
    per_uei = attributed.groupby(attributed["incumbent_uei"].astype(str).str.strip())["_net"].sum()
    per_uei = per_uei[per_uei > 0.0]  # defensively drop non-positive-net UEIs (inert on candidates)
    n_ueis = int(per_uei.size)
    attributed_net = float(per_uei.sum())
    coverage = attributed_net / market_net if market_net > 0.0 else 0.0

    # Gate 1 — thin market (evaluated before any share division; a lone/absent incumbent is Unknown).
    if n_ueis < min_ueis:
        return _unknown(market, n_ueis, coverage, market_net, "too few vendors to assess concentration")
    # Guard — degenerate non-positive market dollars (before the coverage denominator is trusted).
    if market_net <= 0.0:
        return _unknown(market, n_ueis, coverage, market_net, "no positive obligated dollars to assess")
    # Gate 2 — UEI coverage.
    if (1.0 - coverage) > max_unknown_share:
        return _unknown(market, n_ueis, coverage, market_net, "insufficient UEI coverage")

    top_share = float(per_uei.max()) / attributed_net
    return MarketConcentration(
        market=market,
        top_share=top_share,
        n_ueis=n_ueis,
        coverage=coverage,
        market_net=market_net,
        assessable=True,
        reason="",
    )


def compute_hhi_concentration(reportable: pd.DataFrame, cfg: Mapping[str, float]) -> list[MarketConcentration]:
    """Return one :class:`MarketConcentration` per DoD component in ``reportable`` (the
    Data-Gap-excluded recompete set), ordered by component name. Rows with a null/blank
    ``subagency`` cannot be attributed to a market and are dropped. Empty or column-short
    input returns ``[]``. Deterministic — no clock, no RNG, no snapshot argument."""
    if reportable.empty or not set(_INPUT_COLUMNS).issubset(reportable.columns):
        return []
    df = reportable.loc[:, list(_INPUT_COLUMNS)].copy()
    df["_net"] = pd.to_numeric(df["total_obligated_amount"], errors="coerce").fillna(0.0)
    df = df[~_blank(df["subagency"])]
    if df.empty:
        return []
    min_ueis = cfg["min_market_ueis"]
    max_unknown_share = cfg["max_unknown_uei_share"]
    return [
        _assess_market(str(market), grp, min_ueis, max_unknown_share)
        for market, grp in df.groupby("subagency", sort=True)
    ]


def annotate_agency_concentration(
    dim_agency: pd.DataFrame, reportable: pd.DataFrame, cfg: Mapping[str, float]
) -> pd.DataFrame:
    """Return a COPY of ``dim_agency`` with the four :data:`CONCENTRATION_COLUMNS`
    ASSIGNED (overwritten, not appended) — drop-then-rederive idempotent by
    construction, the ``annotate_displacement`` pattern. Joins
    :func:`compute_hhi_concentration` (computed over the caller's REPORTABLE candidate
    frame — Data Gap excluded, the same pool the Incumbent Landscape view reads) onto
    each component row by ``subagency``. Both honesty gates ride through unchanged; a
    component with no reportable rows (or a missing/blank ``subagency``) is
    ``insufficient`` with :data:`NO_REPORTABLE_REASON` — never an imputed number. The
    observed row's empty ``concentration_reason`` round-trips CSV as NaN; readers must
    treat blank-or-NaN as "no refusal", never as a forged refusal (the validator's
    equivalence does). Preserves index, row order, and every existing column; drops and
    filters nothing. Deterministic: no clock, no RNG, no I/O."""
    markets = {m.market: m for m in compute_hhi_concentration(reportable, cfg)}
    out = dim_agency.copy()
    if "subagency" in out.columns:
        subs = [str(v) for v in out["subagency"]]
    else:
        subs = [""] * len(out)
    shares: list[float | None] = []
    n_ueis: list[int] = []
    bases: list[str] = []
    reasons: list[str] = []
    for sub in subs:
        m = markets.get(sub)
        if m is None:
            shares.append(None)
            n_ueis.append(0)
            bases.append("insufficient")
            reasons.append(NO_REPORTABLE_REASON)
        elif m.assessable:
            shares.append(m.top_share)
            n_ueis.append(m.n_ueis)
            bases.append("observed")
            reasons.append("")
        else:
            shares.append(None)
            n_ueis.append(m.n_ueis)
            bases.append("insufficient")
            reasons.append(m.reason)
    out["concentration_top_share"] = pd.Series(shares, index=out.index, dtype="float64")
    out["concentration_n_ueis"] = pd.Series(n_ueis, index=out.index, dtype="Int64")
    out["concentration_basis"] = pd.Series(bases, index=out.index, dtype="object")
    out["concentration_reason"] = pd.Series(reasons, index=out.index, dtype="object")
    return out
