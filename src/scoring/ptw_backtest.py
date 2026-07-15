"""
ptw_backtest.py — the credibility moat for the Competitive Price Range, shipped
DORMANT.

The one honest way to earn the word "confidence" for a price range is to test it
against reality: for past recompetes where we can see BOTH the predecessor contract
and its successor award, did our predecessor-derived range actually contain the
successor's run-rate? That coverage rate ("range contained the actual follow-on in
X% of N historical recompetes") is something no comparable public-data tool
publishes — and it converts the Data-strength labels from asserted to measured.

It ships DORMANT — NOT wired into run_pipeline, the app, or Power BI — and touches
no published number. On the current pool (essentially one fiscal year + a delta) the
proxy definition below DOES yield predecessor→successor pairs, but they are dominated
by same-IDV task-order sequences rather than genuine cross-IDV recompetes, so the
resulting coverage rate is not yet trustworthy enough to publish. Once the owner loads
several fiscal years of USAspending bulk for the seed NAICS — enough for true recompetes
to dominate the proxy pairs — call run_backtest(classified_awards, ...) and surface the
coverage stat. No re-architecture required.

A "successor" is conservatively defined as a later award in the same
NAICS × 2-char-PSC-class × agency cell signed after the predecessor's end date —
a proxy for a recompete, not a certified follow-on linkage.
"""

from datetime import date

import numpy as np
import pandas as pd

from scoring.price_to_win import run_rate, winsorize


def _prep(awards: pd.DataFrame, today: date, min_years, max_years) -> pd.DataFrame:
    """Per-award run-rate + match keys, restricted to rows with a usable run-rate."""
    df = pd.DataFrame({
        "award_id": awards.get("award_id").astype("string"),
        "naics": awards.get("naics").astype("string"),
        "psc_class": awards.get("psc").astype("string").str.slice(0, 2),
        # peer on the DoD component (subagency); awarding_agency_clean is a constant
        "agency": awards.get("awarding_subagency_clean").astype("string").str.upper(),
        "date_signed": pd.to_datetime(awards.get("date_signed"), errors="coerce"),
        "pop_start": pd.to_datetime(awards.get("pop_start_date"), errors="coerce"),
        "pop_end": pd.to_datetime(awards.get("pop_current_end_date"), errors="coerce"),
        "obligated": pd.to_numeric(awards.get("total_obligated_amount"), errors="coerce"),
    })
    df["run_rate"] = [
        run_rate(o, s, e, today, min_years, max_years)
        for o, s, e in zip(df["obligated"], df["pop_start"], df["pop_end"])
    ]
    return df[df["run_rate"].notna()].reset_index(drop=True)


def run_backtest(classified_awards: pd.DataFrame, cfg: dict, today: date = None) -> dict:
    """Coverage of predecessor-derived ranges over actual successor run-rates.

    Returns {"n_pairs", "n_contained", "coverage_rate", "note"}. coverage_rate is
    None when no predecessor→successor pairs exist (the dormant / thin-data case)."""
    today = today or date.today()
    sel = cfg["comparable_selection"]
    ann = cfg["annualization"]
    wlo, whi = cfg["winsorize_quantiles"]
    min_n = sel["min_comparables"]
    lo_band, hi_band = sel["size_band"]
    asof = pd.Timestamp(today)

    df = _prep(classified_awards, today, ann["min_pop_years"], ann["max_pop_years"])
    if df.empty:
        return {"n_pairs": 0, "n_contained": 0, "coverage_rate": None,
                "note": "no awards with a usable run-rate"}

    completed = df[df["pop_end"] < asof]
    n_pairs = n_contained = 0
    for _, pred in completed.iterrows():
        cell = (df["naics"] == pred["naics"]) & (df["psc_class"] == pred["psc_class"]) & (df["agency"] == pred["agency"])
        succ = df[cell & (df["date_signed"] > pred["pop_end"]) & (df["award_id"] != pred["award_id"])]
        if succ.empty:
            continue
        successor = succ.sort_values("date_signed").iloc[0]

        # Comparable set for the predecessor: same naics + psc class, excluding the
        # predecessor and successor themselves, within the incumbent's size band.
        # Only awards SIGNED BY the predecessor's end date count — an out-of-sample
        # test must not build the range from awards that did not yet exist when the
        # recompete would have been priced (no lookahead).
        comps = df[(df["naics"] == pred["naics"]) & (df["psc_class"] == pred["psc_class"])
                   & ~df["award_id"].isin([pred["award_id"], successor["award_id"]])
                   & (df["date_signed"] <= pred["pop_end"])
                   & (df["run_rate"] >= lo_band * pred["run_rate"])
                   & (df["run_rate"] <= hi_band * pred["run_rate"])]
        if len(comps) < min_n:
            continue
        wr = winsorize(comps["run_rate"].to_numpy(), wlo, whi)
        p25, p75 = (float(x) for x in np.quantile(wr, [0.25, 0.75]))
        n_pairs += 1
        if p25 <= successor["run_rate"] <= p75:
            n_contained += 1

    coverage = (n_contained / n_pairs) if n_pairs else None
    note = (f"range contained the actual follow-on run-rate in {n_contained} of {n_pairs} historical recompetes "
            "(proxy pairs: later same-NAICS × PSC-class × component awards, including same-IDV task orders — "
            "directional only until multi-fiscal-year data lets true recompetes dominate)"
            if n_pairs else "no predecessor→successor pairs found — load more fiscal-year data to activate")
    return {"n_pairs": n_pairs, "n_contained": n_contained, "coverage_rate": coverage, "note": note}
