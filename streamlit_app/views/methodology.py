"""Data Quality & Methodology — renders pipeline outputs so the app states its own limits."""
import pandas as pd
import streamlit as st

from components import reason_codes as rc
from components import rescore
from components.data import get_context, page_header

ctx = get_context()
page_header("🧭 Data Quality & Methodology", ctx)

dq = ctx["data_quality"]


@st.cache_data(show_spinner=False)
def _reason_coverage(as_of: str, scorer_version: str, _cands) -> tuple[int, float]:
    """Profile-INDEPENDENT ○-coverage over the loaded candidates, cached like _prepared so the sweep runs
    ONCE per (as_of, scorer_version) — not on every page load. Profile is NOT in the key: it uses
    reason_codes(row, None, {}, cfg), so there is no per-row rescore and no ?p= sensitivity (`_cands` is
    underscore-prefixed so Streamlit does not hash the frame)."""
    n = len(_cands)
    if n == 0:
        return 0, 0.0
    with_gap = 0
    for row in _cands.to_dict("records"):
        chips = rc.engine.reason_codes(row, None, {}, rc._CFG)
        if any(c.basis == "missing" for c in chips):
            with_gap += 1
    return with_gap, with_gap / n * 100

st.subheader("Pursuit score weights")
st.caption("Weighted composite (0–100), scored live against the active company profile (yours, or the labeled demo). Every component score is stored in fact_scoring_breakdown.")
st.table(pd.DataFrame([
    ("Capability match", "25%", "NAICS/PSC/title overlap with the active company profile"),
    ("Expiration urgency", "20%", "Graduated by runway (v2.0.0): peaks near expiry, decays with runway; expired records do NOT max it out"),
    ("Estimated value", "15%", "Fit against the profile's comfortable value range"),
    ("Past-performance fit", "10%", "Contract's DoD component is in the profile's past-performance list"),
    ("Set-aside / competition fit", "10%", "Set-aside type + extent-competed code (FPDS)"),
    ("Recompete confidence", "10%", "Expiration-basis + classification-confidence blend"),
    ("Location fit", "5%", "Place of performance in the profile's states served"),
    ("Data quality", "5%", "Neutral 70 for unknown (not 100); −20 per recorded note, −15 per flag; floor 20"),
], columns=["Component", "Weight", "What it measures"]))

st.subheader("Priority tiers")
st.table(pd.DataFrame([
    ("Tier 1: Pursue Now", "80–100", "Scored"), ("Tier 2: Capture Research", "65–79", "Scored"),
    ("Tier 3: Monitor", "50–64", "Scored"), ("Tier 4: Low Priority", "< 50", "Scored"),
    ("Data Gap", "n/a", "Override — quarantined regardless of score"),
], columns=["Tier", "Pursuit score", "Assigned by"]))

st.subheader("Candidate status & the expired-record policy (graduated)")
st.markdown(
    "Earlier versions gave **every** expired award maximum urgency, so a contract that ended in **2003** could "
    "score into Tier 1. v2.0.0 replaces that with a graduated policy keyed on how long ago a contract expired, "
    "because the honest treatment of an expired record depends entirely on *how* expired it is:\n\n"
    "| Status | Rule (runway vs. today) | Treatment |\n"
    "|---|---|---|\n"
    "| **Active** | expires today or later | Tiers 1–4 as scored; counted in every headline |\n"
    "| **Expired ≤90d (grace)** | expired within 90 days | Still tiered, but flagged *verify on SAM.gov* — often "
    "mid-recompete or on a bridge, a genuine lead |\n"
    "| **Needs verification (stale)** | expired >90 days ago | Forced to **Data Gap**, excluded from every headline "
    "KPI, chart, tier board, and default export; reachable only via the Needs-Verification surfaces |\n\n"
    "**Why 90 days, and why quarantine the rest:** a contract a few weeks past its end date is frequently bridged "
    "or mid-recompete. But given this pipeline's lookback-window coverage gap (known limitation #1 below), a "
    "contract months-to-decades past its end date has almost certainly been re-awarded already — it is dead data, "
    "not a lead. Runway is **recomputed to today** on every load, so this classification never drifts as the "
    "snapshot ages."
)

st.subheader("Data-quality flags")
st.table(pd.DataFrame([
    ("Garbled title", "A raw FPDS record dump or unusable junk in the title field. The raw value is never shown; a cleaned placeholder is displayed and the record is quarantined."),
    ("Raw IGF code", "Title carries an inherently-governmental IGF::CT:: / IGF::OT:: code prefix (stripped for display)."),
    ("Short title", "Under 10 characters after trimming — real but low-information (e.g. 'LABOR', 'ORACLE')."),
    ("Stale expiration", "Expired more than 90 days ago → Data Gap."),
    ("Missing end date", "No usable expiration date → Data Gap."),
], columns=["Flag", "What it means"]))

st.subheader("Expiration windows (buckets)")
st.markdown(
    "Forward-looking runway windows — **`Expired — verify`** is a separate quarantine bucket (shown in gray, never "
    "blended into pipeline totals): "
    "`Expired — verify` · `0-6 Months` · `6-12 Months` · `12-18 Months` · `18-24 Months` · `24+ Months`."
)

st.subheader("Competitive Price Range — why it's not a \"price-to-win\"")
st.markdown(
    "For each expiring contract we estimate a **range of what comparable work has historically been _won_ for**, "
    "as an annual **run-rate**, from real USAspending awards. It is deliberately **not** called a price-to-win: "
    "a true price-to-win predicts a competitor's *bid*, and **losing bids never appear in any public dataset**. "
    "This is the interquartile range of past *winning* awards — defensible and auditable, not a bid recommendation.\n\n"
    "**How it's built** (and why each choice keeps it honest):\n"
    "- **Comparable set** — awards matching the contract's NAICS / 2-char PSC class / size band / recency, via a "
    "recorded match-tier ladder. Each relaxation is tagged and **downgrades Data strength**. Parent IDVs are excluded.\n"
    "- **Run-rate basis** — every award is annualized on its **elapsed** term (obligated ÷ elapsed years), so an "
    "award one year into a five-year deal isn't mistaken for a small contract. Ceilings never build the distribution.\n"
    "- **Range** — winsorized P25 / **Market Median** / P75; the median carries an 80% bootstrap interval.\n"
    "- **Incumbent** run-rate is shown as a **separate reference line**, never blended in — its divergence from the "
    "market median is itself a signal (scope change, or a price-attack opening).\n"
    "- **Escalation** (out-year projection) and **competition** nudges are **off-by-default, disclosed toggles**, "
    "never baked into the baseline. Below a comparables floor the tool **refuses** rather than inventing a range.\n"
    "- **Data strength** (Strong/Moderate/Weak) reflects comparable count, dispersion, recency, % still in-progress, "
    "and pricing homogeneity — any single disqualifier forces a downgrade.\n\n"
    "Outputs are **informational market analysis from public data — not certified cost or pricing data (FAR 15.4).**"
)

st.subheader("Obligation pace — how obligated dollars track the contract clock (descriptive, not spend)")
_pace_cov = ""
_pace_cands = ctx.get("candidates")
if _pace_cands is not None and not _pace_cands.empty and "burn_basis" in _pace_cands.columns:
    _n_measured = int((_pace_cands["burn_basis"].astype(str) == "measured").sum())
    _n_total = len(_pace_cands)
    _pct_pace = _n_measured / _n_total * 100 if _n_total else 0
    _pace_cov = (f"\n\n**Obligation pace is computable for {_n_measured:,} of {_n_total:,} loaded candidates "
                 f"({_pct_pace:.0f}%)** — the rest don't report an order ceiling, are fully obligated, exceed it, "
                 "or are net deobligations.")
st.markdown(
    "For each expiring order we compare the **fraction of its ceiling obligated** against the **fraction of its "
    "period of performance elapsed** — a point-in-time read of whether obligations are running ahead of or behind "
    "the clock. It is **descriptive**: it largely reflects the **funding profile** (fully-funded / FFP awards "
    "obligate early; incrementally-funded awards obligate in tranches), so it is **not** a measure of **spend** and "
    "**not** a recompete predictor. Because obligations jump at each option exercise and then drift, the same "
    "healthy contract can read *ahead of pace* just after an exercise and *behind pace* months later — so it is "
    "shown **as of the snapshot**, never advanced to today. Four honesty bases gate it: **measured** (a pace band "
    "is drawn from two facts against ±0.20 asserted-prior thresholds), **fully_funded** and **ceiling_exceeded** "
    "(shown as facts, no band), and **insufficient** (no reported ceiling, missing PoP dates, a parent vehicle, "
    "out of window, or a net deobligation — we refuse to read a pace). On this DoD data that refusal is *most* rows."
    + _pace_cov
)

st.subheader("SAM.gov opportunity linkage — coverage is intentionally disclosed")
bridge = ctx.get("bridge")
cands = ctx.get("candidates")
if bridge is not None and not bridge.empty and "link_confidence" in bridge and cands is not None and not cands.empty:
    linked = bridge[bridge["link_confidence"] != "No Match"]
    n_linked, n_total = len(linked), len(cands)
    rate = n_linked / n_total * 100 if n_total else 0
    conf = linked["link_confidence"].value_counts().to_dict()
    st.markdown(
        f"We load real SAM.gov Contract Opportunity notices and fuzzy-match them to expiring awards. "
        f"**Only {n_linked:,} of {n_total:,} candidates ({rate:.0f}%) currently link to a live notice** "
        f"(High: {conf.get('High', 0)}, Medium: {conf.get('Medium', 0)}, Low: {conf.get('Low', 0)}). "
        "That low rate is **structural, not a bug**: a recompete's solicitation is usually posted only a few "
        "months before award, so most contracts expiring 6–24 months out simply have no notice yet; PIIDs and "
        "titles also differ between the award and its future solicitation. Treat a linked notice as a strong "
        "early signal and its absence as **no signal**, never as evidence a recompete isn't coming."
    )
else:
    st.caption("No opportunity-bridge data loaded in this dataset.")

st.subheader("Facts vs. estimates")
st.markdown(
    "- **Facts** (from USAspending.gov): PIID, awardee/UEI, agencies, obligated amount & ceiling, PoP dates, "
    "NAICS, PSC, contract pricing type, set-aside type, extent competed, number of offers received, and the "
    "**ceiling-obligated ratio** (obligated ÷ ceiling — a ratio of two facts; note it can run very large "
    "(nine-digit) when a large obligation runs against a near-zero recorded ceiling, flagged "
    "`burn_basis = ceiling_exceeded`).\n"
    "- **Estimates** (analytical, not official predictions): cyber/IT classification confidence, recompete candidacy, "
    "recompete windows, pursuit score & tier, the Competitive Price Range, incumbent analysis, link confidence, "
    "and the **obligation pace & pace band**.\n"
    "- **Reason codes are a legend, not a new score** — `●` fact, `◐` estimate (asserted-prior bands), `○` not "
    "reported; the price-to-win median stays an estimate in the Price-Range panel, never a `●` chip."
)

st.subheader("Reason codes — making the score legible, and refusing to guess")
_rc_line = ""
_rc_cands = ctx.get("candidates")
if _rc_cands is not None and not _rc_cands.empty:
    _n_gap, _pct_gap = _reason_coverage(str(ctx.get("as_of", "")), rescore.SCORER_VERSION, _rc_cands)
    _rc_line = (f"\n\n**Of {len(_rc_cands):,} reportable candidates, {_pct_gap:.0f}% carry at least one ○ "
                "'not reported' chip** - the honest reality of DoD FPDS data.")
st.markdown(
    "Every pursuit score is a weighted sum of eight components, but a nine-second scan can't see *why* a row is "
    "Tier 1. **Reason codes** turn the score into a short, ordered list of chips, each stamped with an honesty "
    "glyph: `●` a **fact** read straight off the contract, `◐` an **estimate** (an asserted-prior band, or a "
    "neutral baseline the *locked* scorer substituted for a missing match — an unknown agency becomes 50, "
    "clean-looking notes become 70), and `○` where the government simply **didn't report** it. The chips read the "
    "*raw* column behind each number, so a neutral 50 that means \"no idea\" is shown as `◐`/`○`, never dressed as "
    "a `●` fact — and a **blank set-aside is never rendered as full-and-open** (it's a `○` refusal). The agency and "
    "capability/value/location fit chips reflect **your own declared company profile** and recompute live when you "
    "change it; the price-range chip is an estimate from **public** award comparables, never CPARS or proposal "
    "pricing. Reason codes change no number — they only make the existing score legible." + _rc_line
)

st.subheader("Data quality report")
if dq.empty:
    st.info("No data quality report found in this dataset.")
else:
    counts = dq[dq["category"].isin(["row_count", "missing_value", "duplicate_check", "confidence_distribution"])] \
        if "category" in dq else dq
    st.dataframe(counts, hide_index=True, width="stretch")
    if "category" in dq:
        notes = dq[dq["category"] == "source_coverage_notes"]
        if not notes.empty:
            st.subheader("Known limitations")
            for _, r in notes.iterrows():
                st.markdown(f"- {r['value']}")

st.caption(f"Dataset mode: **{ctx['mode']}** · as of **{ctx['as_of']}**")
