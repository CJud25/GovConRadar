"""
price_to_win.py — the Competitive Price Range engine.

WHAT THIS IS: for each expiring contract, a defensible RANGE of what comparable
work has historically been *won* for, expressed as an annual run-rate, with the
evidence and biases attached. It is built only from real USAspending award data.

WHAT THIS IS NOT: a price-to-win. Competitor bid prices never appear in public
data, so this is the interquartile range of comparable *winning* awards, not a
prediction of what it takes to win. The UI must say so.

Methodology (hardened per a statistical red-team — the corrections ARE the point):
  * Run-rate basis kills the obligated-vs-ceiling truncation bias: every award,
    active or completed, is annualized on ELAPSED years (obligated ÷ elapsed), so
    a contract one year into a five-year term is not mistaken for a small award.
    Ceilings are never used to build the distribution.
  * Comparable selection is a recorded match-tier ladder over the 2-char PSC class
    (4-char PSC is too sparse), not hard bins. Each rung relaxation is tagged and
    forces a data-strength downgrade. Parent IDVs are excluded.
  * Percentiles are winsorized (heavy-tailed dollars); the median carries a
    bootstrap confidence interval that drives data strength.
  * The incumbent's own run-rate is a SEPARATE reference line — never blended in.
    Its divergence from the market median is surfaced as capture intelligence.
  * Escalation and competition-intensity are DISCLOSED, off-by-default toggles in
    the app, never baked into this baseline range.
  * Below a comparables floor the engine REFUSES: basis="insufficient", no range.

THE one engine: streamlit_app/components/price_to_win.py imports the math helpers
and config from here / utils.config for its live recompute (the former inlined
mirror was collapsed 2026-07-07 — Option D, same as the scorer and quality).
"""

from datetime import date

import numpy as np
import pandas as pd

DAYS_PER_YEAR = 365.25
VALUE_BASIS = "obligated_run_rate"

# Long-format audit table: the exact comparables behind each candidate's range.
PTW_COMPARABLES_COLUMNS = [
    "candidate_id", "comp_award_id", "comp_piid", "comp_agency", "comp_naics",
    "comp_psc", "comp_psc_class", "comp_value_basis", "comp_obligated",
    "comp_elapsed_years", "comp_run_rate", "comp_in_progress", "comp_offers",
    "comp_pricing_code", "comp_pop_start", "comp_pop_end", "comp_date_signed",
    "match_tier",
]

# ptw_* columns appended to fact_recompete_candidates.
PTW_CANDIDATE_COLUMNS = [
    "ptw_low", "ptw_market_median", "ptw_high", "ptw_ci_low", "ptw_ci_high",
    "ptw_data_strength", "ptw_n_comparables", "ptw_match_tier", "ptw_pct_in_progress",
    "ptw_pricing_mix", "ptw_basis", "ptw_incumbent_runrate", "ptw_incumbent_divergence",
    "ptw_incumbent_outside_iqr", "ptw_noncompetitive_incumbent",
]

_TIER_ORDER = ["A", "B", "C", "D"]


# ─── pure numeric helpers (unit-tested in isolation) ──────────────────────────
def elapsed_years(start, end, as_of, min_years, max_years):
    """Years from PoP start to min(end, as_of) — the ELAPSED term — clamped to
    [min_years, max_years]. Returns None when dates are missing/degenerate."""
    s = pd.to_datetime(start, errors="coerce")
    e = pd.to_datetime(end, errors="coerce")
    a = pd.Timestamp(as_of)
    if pd.isna(s):
        return None
    eff_end = min(e, a) if not pd.isna(e) else a
    days = (eff_end - s).days
    if days <= 0:
        return None
    return float(min(max(days / DAYS_PER_YEAR, min_years), max_years))


def run_rate(obligated, start, end, as_of, min_years, max_years):
    """Annualized run-rate = obligated ÷ elapsed years. None if not computable."""
    if obligated is None or pd.isna(obligated) or obligated <= 0:
        return None
    yrs = elapsed_years(start, end, as_of, min_years, max_years)
    if not yrs:
        return None
    return float(obligated) / yrs


def winsorize(values, lo_q, hi_q):
    """Clip an array to its [lo_q, hi_q] quantiles (tames the heavy right tail)."""
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return arr
    lo, hi = np.quantile(arr, [lo_q, hi_q])
    return np.clip(arr, lo, hi)


def bootstrap_median_ci(values, iterations, ci, seed):
    """(low, high) bootstrap CI on the median. Deterministic given seed so the
    pipeline is reproducible."""
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return (float("nan"), float("nan"))
    if arr.size == 1:
        return (float(arr[0]), float(arr[0]))
    rng = np.random.default_rng(seed)
    meds = np.median(rng.choice(arr, size=(iterations, arr.size), replace=True), axis=1)
    tail = (1.0 - ci) / 2.0
    return tuple(float(x) for x in np.quantile(meds, [tail, 1.0 - tail]))


def _seed_from(text):
    """Small stable integer seed derived from a candidate_id (no hashlib needed;
    keeps bootstrap reproducible without depending on process hash randomization)."""
    h = 2166136261
    for ch in str(text):
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return int(h)


def _pricing_family(code):
    """FPDS type_of_contract_pricing_code -> coarse family for homogeneity checks."""
    c = ("" if code is None else str(code)).strip().upper()
    if c in {"J", "K", "L", "M", "A"}:
        return "FFP"          # fixed-price family
    if c in {"R", "S", "T", "U", "V"}:
        return "COST"         # cost-reimbursement family
    if c in {"Y", "Z"}:
        return "TM"           # time-and-materials / labor-hours
    return "OTHER"


def _data_strength(n, ci_ratio, pct_in_progress, match_tier, pricing_homogeneous, cfg):
    """Rules-based Strong/Moderate/Weak. Any single disqualifier forces a
    downgrade — a tight, recent, well-matched but all-truncated set is NOT strong."""
    ds = cfg["data_strength"]
    strong, mod = ds["strong"], ds["moderate"]
    if (n >= strong["min_n"] and ci_ratio <= strong["max_ci_ratio"]
            and pct_in_progress <= strong["max_pct_in_progress"]):
        level = "Strong"
    elif (n >= mod["min_n"] and ci_ratio <= mod["max_ci_ratio"]
            and pct_in_progress <= mod["max_pct_in_progress"]):
        level = "Moderate"
    else:
        level = "Weak"
    # Forced downgrades (disqualifiers). Tier D drops the size-band mask entirely, so
    # its comparables can sit orders of magnitude off the incumbent's own run-rate
    # (incumbent frequently outside the reported IQR) — never trustworthy, so force it
    # to Weak rather than merely one notch down.
    if match_tier == "D":
        level = "Weak"
    if not pricing_homogeneous and level == "Strong":
        level = "Moderate"
    return level


def _insufficient_result(incumbent_rr, noncompetitive):
    return {
        "ptw_low": None, "ptw_market_median": None, "ptw_high": None,
        "ptw_ci_low": None, "ptw_ci_high": None, "ptw_data_strength": "Insufficient",
        "ptw_n_comparables": 0, "ptw_match_tier": None, "ptw_pct_in_progress": None,
        "ptw_pricing_mix": None, "ptw_basis": "insufficient",
        "ptw_incumbent_runrate": incumbent_rr,
        "ptw_incumbent_divergence": None, "ptw_incumbent_outside_iqr": None,
        "ptw_noncompetitive_incumbent": noncompetitive,
    }, []


# ─── the engine ───────────────────────────────────────────────────────────────
def attach_ptw(recompete_candidates: pd.DataFrame, classified_awards: pd.DataFrame,
               cfg: dict, today: date = None):
    """Returns (candidates_with_ptw_columns, fact_ptw_comparables_long_df).

    Reads the ~26k-row award pool once, scores every candidate against it, and
    emits both the baked ptw_* columns and the auditable comparables table."""
    today = today or date.today()
    cands = recompete_candidates.copy()
    if cands.empty:
        for c in PTW_CANDIDATE_COLUMNS:
            cands[c] = pd.Series(dtype="object")
        return cands, pd.DataFrame(columns=PTW_COMPARABLES_COLUMNS)

    sel = cfg["comparable_selection"]
    ann = cfg["annualization"]
    wlo, whi = cfg["winsorize_quantiles"]
    p_lo, p_mid, p_hi = cfg["percentiles"]
    boot = cfg["bootstrap"]
    noncomp_codes = {c.upper() for c in cfg["noncompetitive_extent_codes"]}
    min_years, max_years = ann["min_pop_years"], ann["max_pop_years"]
    psc_len, naics_len = sel["psc_class_len"], sel["naics_family_len"]

    # ---- precompute the pool once (vectorized) ----
    pool = classified_awards
    ps = pd.to_datetime(pool.get("pop_start_date"), errors="coerce")
    pe = pd.to_datetime(pool.get("pop_current_end_date"), errors="coerce")
    ds_signed = pd.to_datetime(pool.get("date_signed"), errors="coerce")
    asof = pd.Timestamp(today)
    eff_end = pe.where(pe < asof, asof)                 # min(end, as_of)
    elapsed_days = (eff_end - ps).dt.days
    yrs = (elapsed_days / DAYS_PER_YEAR).clip(lower=min_years, upper=max_years)
    obligated = pd.to_numeric(pool.get("total_obligated_amount"), errors="coerce")
    rr = obligated / yrs
    award_type = pool.get("award_type").astype("string").str.upper() if "award_type" in pool else pd.Series([pd.NA] * len(pool))
    allowed = {t.upper() for t in sel["allowed_award_types"]}

    valid = (
        np.isfinite(rr.to_numpy(dtype=float, na_value=np.nan))
        & (elapsed_days.to_numpy(dtype=float, na_value=-1) > 0)
        & (obligated.to_numpy(dtype=float, na_value=-1) > 0)
        & award_type.isin(allowed).to_numpy()
    )
    P = {
        "award_id": pool.get("award_id").astype("string").to_numpy()[valid],
        "piid": pool.get("piid").astype("string").to_numpy()[valid],
        # Peer-agency match is on the DoD **component** (subagency); `awarding_agency_clean`
        # is a constant ("DEPARTMENT OF DEFENSE") so it would make the Tier-A agency test a
        # no-op (Tier A == Tier B). Using subagency makes Tier A a true same-component peer.
        "agency": pool.get("awarding_subagency_clean").astype("string").str.upper().to_numpy()[valid],
        "naics": pool.get("naics").astype("string").to_numpy()[valid],
        "psc": pool.get("psc").astype("string").to_numpy()[valid],
        "offers": pd.to_numeric(pool.get("number_of_offers_received"), errors="coerce").to_numpy()[valid],
        "pricing": pool.get("type_of_contract_pricing_code").astype("string").to_numpy()[valid],
        "run_rate": rr.to_numpy(dtype=float)[valid],
        "obligated": obligated.to_numpy(dtype=float)[valid],
        "elapsed": yrs.to_numpy(dtype=float)[valid],
        "in_progress": (pe > asof).to_numpy()[valid],
        "date_signed": ds_signed.to_numpy()[valid],
        "pop_start": ps.dt.strftime("%Y-%m-%d").to_numpy()[valid],
        "pop_end": pe.dt.strftime("%Y-%m-%d").to_numpy()[valid],
    }
    p_naics = P["naics"].astype("U6")
    p_naics_fam = np.array([s[:naics_len] for s in np.where(p_naics == "nan", "", p_naics)], dtype="U6")
    p_psc_class = np.array([("" if s in ("nan", "<NA>") else str(s))[:psc_len] for s in P["psc"]], dtype="U6")
    recency_cutoff = np.datetime64(pd.Timestamp(today) - pd.DateOffset(years=sel["recency_years"]))
    p_recent = np.where(np.isnat(P["date_signed"]), False, P["date_signed"] >= recency_cutoff)
    size_lo, size_hi = sel["size_band"]
    target, min_n = sel["target_comparables"], sel["min_comparables"]

    ptw_rows = []          # per-candidate ptw_* dicts, aligned to cands order
    comp_records = []      # long fact_ptw_comparables rows

    for _, c in cands.iterrows():
        cid = c["candidate_id"]
        incumbent_rr = run_rate(c.get("total_obligated_amount"), c.get("pop_start_date"),
                                c.get("current_end_date"), today, min_years, max_years)
        ec = ("" if c.get("extent_competed_code") is None else str(c.get("extent_competed_code"))).strip().upper()
        noncompetitive = bool(ec in noncomp_codes)

        c_naics = "" if pd.isna(c.get("naics")) else str(c.get("naics"))
        c_naics_fam = c_naics[:naics_len]
        c_psc_class = ("" if pd.isna(c.get("psc")) else str(c.get("psc")))[:psc_len]
        c_agency = ("" if pd.isna(c.get("subagency")) else str(c.get("subagency"))).upper()

        not_self = P["award_id"] != ("" if pd.isna(c.get("source_award_id")) else str(c.get("source_award_id")))
        naics_exact = (p_naics == c_naics) & (c_naics != "")
        naics_fam = (p_naics_fam == c_naics_fam) & (c_naics_fam != "")
        psc_ok = (p_psc_class == c_psc_class) & (c_psc_class != "")
        agency_ok = (P["agency"] == c_agency) & (c_agency != "")
        if incumbent_rr:
            size_ok = (P["run_rate"] >= size_lo * incumbent_rr) & (P["run_rate"] <= size_hi * incumbent_rr)
        else:
            size_ok = np.ones(len(not_self), dtype=bool)

        # Nested match-tier ladder (A tightest ⊆ B ⊆ C ⊆ D loosest).
        tierA = not_self & naics_exact & psc_ok & agency_ok & size_ok & p_recent
        tierB = not_self & naics_exact & psc_ok & size_ok & p_recent
        tierC = not_self & naics_fam & psc_ok & size_ok
        tierD = not_self & naics_fam & psc_ok
        masks = {"A": tierA, "B": tierB, "C": tierC, "D": tierD}

        # Use the TIGHTEST tier that clears the comparables floor (quality first);
        # widen only when a tighter tier can't supply enough comparables. The set
        # is capped at `target`, so a huge loose pool never dilutes a tight match.
        chosen = None
        for t in _TIER_ORDER:
            if masks[t].sum() >= min_n:
                chosen = t
                break

        if chosen is None:
            res, _ = _insufficient_result(incumbent_rr, noncompetitive)
            ptw_rows.append(res)
            continue

        idx = np.where(masks[chosen])[0]
        # tightest tier each selected comparable satisfies (for per-row labeling)
        row_tier = np.full(len(P["award_id"]), "", dtype="U1")
        for t in reversed(_TIER_ORDER):      # assign loosest first, overwrite with tighter
            row_tier[np.where(masks[t])[0]] = t
        # cap to target: prefer tighter tier, then more recent
        if idx.size > target:
            rank = np.array([_TIER_ORDER.index(row_tier[i]) for i in idx])
            recency = np.array([0 if np.isnat(P["date_signed"][i]) else P["date_signed"][i].astype("datetime64[D]").astype(int) for i in idx])
            order = np.lexsort((-recency, rank))    # tier rank asc, recency desc
            idx = idx[order[:target]]

        rates = P["run_rate"][idx]
        wr = winsorize(rates, wlo, whi)
        p25, p50, p75 = (float(x) for x in np.quantile(wr, [p_lo / 100, p_mid / 100, p_hi / 100]))
        ci_low, ci_high = bootstrap_median_ci(wr, boot["iterations"], boot["ci"], _seed_from(cid))
        ci_ratio = (ci_high - ci_low) / p50 if p50 else float("inf")
        pct_in_progress = float(np.mean(P["in_progress"][idx]))
        fams = {_pricing_family(P["pricing"][i]) for i in idx if _pricing_family(P["pricing"][i]) != "OTHER"}
        pricing_homogeneous = len(fams) <= 1
        pricing_mix = ",".join(sorted(fams)) if fams else "unknown"

        strength = _data_strength(len(idx), ci_ratio, pct_in_progress, chosen, pricing_homogeneous, cfg)
        divergence = (incumbent_rr - p50) / p50 if (incumbent_rr and p50) else None
        outside_iqr = bool(incumbent_rr and (incumbent_rr < p25 or incumbent_rr > p75)) if incumbent_rr else None

        ptw_rows.append({
            "ptw_low": round(p25, 2), "ptw_market_median": round(p50, 2), "ptw_high": round(p75, 2),
            "ptw_ci_low": round(ci_low, 2), "ptw_ci_high": round(ci_high, 2),
            "ptw_data_strength": strength, "ptw_n_comparables": int(len(idx)),
            "ptw_match_tier": chosen, "ptw_pct_in_progress": round(pct_in_progress, 3),
            "ptw_pricing_mix": pricing_mix, "ptw_basis": "comparables",
            "ptw_incumbent_runrate": round(incumbent_rr, 2) if incumbent_rr else None,
            "ptw_incumbent_divergence": round(divergence, 3) if divergence is not None else None,
            "ptw_incumbent_outside_iqr": outside_iqr,
            "ptw_noncompetitive_incumbent": noncompetitive,
        })
        for i in idx:
            comp_records.append({
                "candidate_id": cid, "comp_award_id": P["award_id"][i], "comp_piid": P["piid"][i],
                "comp_agency": P["agency"][i], "comp_naics": P["naics"][i], "comp_psc": P["psc"][i],
                "comp_psc_class": p_psc_class[i], "comp_value_basis": VALUE_BASIS,
                "comp_obligated": round(float(P["obligated"][i]), 2),
                "comp_elapsed_years": round(float(P["elapsed"][i]), 2),
                "comp_run_rate": round(float(P["run_rate"][i]), 2),
                "comp_in_progress": bool(P["in_progress"][i]),
                "comp_offers": None if np.isnan(P["offers"][i]) else int(P["offers"][i]),
                "comp_pricing_code": None if P["pricing"][i] in (None, "nan", "<NA>") else str(P["pricing"][i]),
                "comp_pop_start": None if P["pop_start"][i] in (None, "NaT") else P["pop_start"][i],
                "comp_pop_end": None if P["pop_end"][i] in (None, "NaT") else P["pop_end"][i],
                "comp_date_signed": None if np.isnat(P["date_signed"][i]) else np.datetime_as_string(P["date_signed"][i], unit="D"),
                "match_tier": row_tier[i],
            })

    ptw_df = pd.DataFrame(ptw_rows, index=cands.index)
    for col in PTW_CANDIDATE_COLUMNS:
        cands[col] = ptw_df[col]
    comparables = pd.DataFrame(comp_records, columns=PTW_COMPARABLES_COLUMNS)
    return cands, comparables
