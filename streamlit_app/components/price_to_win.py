"""
price_to_win.py (app) — live recompute for the Competitive Price Range panel.

The pipeline (src/scoring/price_to_win.py) bakes the baseline range AND the exact
comparables used (fact_ptw_comparables). This module lets the user tweak the
estimate LIVE — exclude a comparable they know is a bad fit, project across an
expected term, or assume more/less competition — by recomputing from those baked
comparable run-rates. It never re-selects from the full pool (the pipeline already
did that expensive, recorded work), so a tweak can't silently widen the match.

The math (winsorize → percentiles → bootstrap CI → data-strength) and the config
are IMPORTED from the one engine + config/price_to_win.yaml via utils.config
(app.py puts src/ on sys.path — the same Option D pattern as the scorer and the
quality module). The former inlined PTW_CONFIG mirror and duplicated helpers were
collapsed 2026-07-07; what remains here is app-only glue — the live-tweak
recompute and its disclosed, off-by-default scenario adjustments.

Design rule this enforces in the UI: every adjustment is an explicit, off-by-
default toggle whose delta from the baseline is shown. Facts and estimates never
touch; below the comparables floor we refuse to emit a range.
"""
import numpy as np
import pandas as pd

from scoring.price_to_win import (
    _data_strength,
    _pricing_family,
    _seed_from,
    bootstrap_median_ci,
    winsorize,
)
from utils.config import PRICE_TO_WIN as PTW_CONFIG

COMPETITION_CHOICES = ["As competed historically", "Assume more competition", "Assume less competition"]


def competition_multiplier(mode):
    """Disclosed, user-chosen scenario multiplier (NOT a measured elasticity)."""
    comp = PTW_CONFIG["competition"]
    if mode == "Assume more competition":
        return 1.0 - comp["high_offers_discount"]
    if mode == "Assume less competition":
        return 1.0 + comp["low_offers_uplift"]
    return 1.0


def project_over_term(annual_value, term_years):
    """Total contract value across `term_years`, escalating each out-year at the
    labeled ECI rate. year 1 at the annual value. Returns the annual value when
    term is None/1 (no projection)."""
    if not term_years or term_years <= 1:
        return annual_value
    rate = PTW_CONFIG["escalation"]["annual_rate"]
    return float(annual_value * sum((1.0 + rate) ** y for y in range(int(round(term_years)))))


def recompute(comps: pd.DataFrame, excluded_ids=(), competition="As competed historically",
              term_years=None, match_tier=None, incumbent_runrate=None):
    """Recompute the range from the baked comparables (optionally excluding some).
    Returns a dict of annualized + projected figures, or a refusal when too few
    comparables survive. All values are annual run-rates unless term_years projects."""
    min_n = PTW_CONFIG["comparable_selection"]["min_comparables"]
    if comps is None or comps.empty:
        return {"basis": "insufficient", "n": 0}
    keep = comps[~comps["comp_award_id"].astype(str).isin({str(x) for x in excluded_ids})]
    rates = pd.to_numeric(keep["comp_run_rate"], errors="coerce").dropna().to_numpy()
    n = int(rates.size)
    if n < min_n:
        return {"basis": "insufficient", "n": n}

    wlo, whi = PTW_CONFIG["winsorize_quantiles"]
    wr = winsorize(rates, wlo, whi)
    p_lo, p_mid, p_hi = PTW_CONFIG["percentiles"]  # read from config, don't hardcode
    p25, p50, p75 = (float(x) for x in np.quantile(wr, [p_lo / 100, p_mid / 100, p_hi / 100]))
    boot = PTW_CONFIG["bootstrap"]
    # Seed exactly like the engine (from the candidate_id) so the zero-tweak
    # baseline CI reproduces the baked ptw_ci_* — and stays stable across tweaks.
    if "candidate_id" in keep.columns and len(keep):
        seed = _seed_from(str(keep["candidate_id"].iloc[0]))
    else:
        seed = int(abs(p50)) % (2**32)
    ci_low, ci_high = bootstrap_median_ci(wr, boot["iterations"], boot["ci"], seed)
    ci_ratio = (ci_high - ci_low) / p50 if p50 else float("inf")

    mult = competition_multiplier(competition)
    p25, p50, p75, ci_low, ci_high = (v * mult for v in (p25, p50, p75, ci_low, ci_high))

    in_prog = keep["comp_in_progress"].fillna(False).astype(bool).to_numpy() if "comp_in_progress" in keep else np.zeros(n, bool)
    pct_in_progress = float(np.mean(in_prog)) if in_prog.size else 0.0
    fams = {_pricing_family(c) for c in keep.get("comp_pricing_code", pd.Series(dtype=object)) if _pricing_family(c) != "OTHER"}
    pricing_homogeneous = len(fams) <= 1
    strength = _data_strength(n, ci_ratio, pct_in_progress, match_tier, pricing_homogeneous, PTW_CONFIG)

    out = {
        "basis": "comparables", "n": n,
        "annual_low": p25, "annual_median": p50, "annual_high": p75,
        "ci_low": ci_low, "ci_high": ci_high, "ci_ratio": ci_ratio,
        "data_strength": strength, "pct_in_progress": pct_in_progress,
        "pricing_mix": ",".join(sorted(fams)) if fams else "unknown",
        "competition": competition, "multiplier": mult,
    }
    out["low"] = project_over_term(p25, term_years)
    out["median"] = project_over_term(p50, term_years)
    out["high"] = project_over_term(p75, term_years)
    out["term_years"] = term_years
    if incumbent_runrate and p50:
        out["incumbent_divergence"] = (incumbent_runrate - p50) / p50
        out["incumbent_outside_iqr"] = bool(incumbent_runrate < p25 or incumbent_runrate > p75)
    return out
