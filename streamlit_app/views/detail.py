"""Contract Detail — per-candidate profile, scoring breakdown, linked notices,
the incumbent's other contracts, and a downloadable one-page Capture Brief.
Opens directly from a ?cid= deep link (Explorer row-click)."""
import html
from datetime import date

import pandas as pd
import streamlit as st

from components import briefing, charts, rescore, shell, theme
from components import eligibility_lane as el
from components import price_to_win as ptw
from components import reason_codes as rc
from components.data import get_context, notice_response_days, page_header, profile_is_custom, usd
from labels.taxonomy import SURFACE_LANGUAGE

MONTHS = 30.44

# D15 — the fixed mods-signal disclosure (A7 spec), rendered verbatim on every
# mods-derived surface. Duplicated (not imported) in views/explorer.py: shell.py is
# off-limits for this task and the two views don't otherwise share a module.
MODS_DISCLOSURE = "DoD FPDS reporting lags ~90 days; termination signals are ≥3 months old."
_TERMINATION_KIND_LABELS = {"complete_likely": "complete", "partial_or_unclear": "partial/unclear"}

ctx = get_context()
page_header("Contract Detail", ctx, subtitle="Capture profile, transparent score, and a one-page brief.")
cand = ctx["candidates"]

if cand.empty:
    st.warning("No candidate data loaded.")
    st.stop()

ids = cand["candidate_id"].astype(str).tolist()
labels = {str(r["candidate_id"]): f"{str(r.get('title_display') or '[Untitled]')[:60]} — {r.get('subagency', '')} [{r['candidate_id']}]"
          for _, r in cand.iterrows()}

def _months(days):
    return days / MONTHS if pd.notna(days) else None


def _pct(x):
    return f"{x * 100:+.0f}%" if x is not None and pd.notna(x) else "—"


def _mod_true(v) -> bool:
    """House truthiness for the baked MOD_COLUMNS (A7): they round-trip CSV as a real
    bool OR as the strings "True"/"False" (NaN for missing/not-yet-baked) — never
    rely on bool() (Python's bool("False") is truthy). Mirrors
    src/scoring/mods_signal.py's ModResult flags."""
    return str(v) == "True"


def _render_competitive_price_range(row, ctx, sel_cid):
    """The Competitive Price Range panel: baked baseline range + live-tweakable
    recompute from the exact comparables the pipeline used. Honest by construction —
    a range of historical WINNERS (never a bid prediction), the incumbent shown as
    a separate line (never blended), and a refusal state below the comparables floor."""
    st.divider()
    st.markdown('<div class="rr-title" style="font-size:18px">Competitive Price Range</div>', unsafe_allow_html=True)
    st.caption("A range of what **comparable work has historically been _won_ for**, as an annual run-rate from real "
               "USAspending awards. **Not a price-to-win** — competitor bids are never public, so this is the spread of "
               "past *winning* awards, not a bid recommendation.")

    comps_all = ctx.get("comparables")
    my_comps = (comps_all[comps_all["candidate_id"].astype(str) == str(sel_cid)]
                if comps_all is not None and not comps_all.empty and "candidate_id" in comps_all else pd.DataFrame())

    if row.get("ptw_basis") == "insufficient" or my_comps.empty:
        st.warning("**Insufficient comparables to estimate.** Fewer than the minimum number of similar historical "
                   "awards (same NAICS / PSC class / size band) were found, so we won't invent a range. As more "
                   "fiscal-year data is loaded, or for better-covered NAICS, a range appears here.")
        return

    incumbent_rr = row.get("ptw_incumbent_runrate")
    incumbent_rr = float(incumbent_rr) if pd.notna(incumbent_rr) else None
    baseline_median = row.get("ptw_market_median")

    # ---- live controls (all OFF/neutral by default) ----
    ctrl1, ctrl2 = st.columns([1, 1])
    term = ctrl1.slider("Assume follow-on term (years)", 1, 10, 1,
                        help="1 = show the annual run-rate. Higher projects a full-term total, escalating each "
                             "out-year at the BLS ECI (Professional & Technical Services) rate — a labeled assumption.")
    competition = ctrl2.radio("Competition assumption", ptw.COMPETITION_CHOICES, horizontal=False,
                              help="A disclosed scenario nudge, not a measured elasticity — no losing-bid data exists.")
    id_label = {str(r["comp_award_id"]): f"{r.get('comp_piid', r['comp_award_id'])} · {theme.usd_short(r['comp_run_rate'])}/yr"
                for _, r in my_comps.iterrows()}
    excluded = st.multiselect("Exclude comparables you know are a poor fit", list(id_label), format_func=lambda i: id_label[i])

    res = ptw.recompute(my_comps, excluded_ids=excluded, competition=competition, term_years=term,
                        match_tier=row.get("ptw_match_tier"), incumbent_runrate=incumbent_rr)
    if res.get("basis") == "insufficient":
        st.warning(f"After excluding those comparables, only {res['n']} remain — below the floor to estimate. "
                   "Remove an exclusion to see the range.")
        return

    projected = bool(term and term > 1)
    unit = f"total over {term} yrs" if projected else "per year"
    m1, m2, m3 = st.columns(3)
    m1.metric(f"Low ({unit})", theme.usd_short(res["low"]))
    delta = None
    if baseline_median and pd.notna(baseline_median) and res["annual_median"]:
        delta = res["annual_median"] / float(baseline_median) - 1.0
    # Only show the delta once it's a real move (>=0.5%); a bare float-rounding
    # residual shouldn't render "+0% vs. baseline". delta_color off — this is a
    # price, not a KPI, so green/red "good/bad" coloring would mislead.
    show_delta = f"{_pct(delta)} vs. baseline" if (delta is not None and abs(delta) >= 0.005) else None
    m2.metric(f"Market Median ({unit})", theme.usd_short(res["median"]), delta=show_delta, delta_color="off")
    m3.metric(f"High ({unit})", theme.usd_short(res["high"]))

    strengths = {"Strong": "🟢", "Moderate": "🟡", "Weak": "🟠", "Insufficient": "⚪"}
    ds = res["data_strength"]
    spread = (res["annual_high"] - res["annual_low"]) / res["annual_median"] if res["annual_median"] else 0
    # Weak explains itself so "Weak — based on 31 comparables" doesn't read as a contradiction.
    meaning = {"Strong": "tight, well-matched set", "Moderate": "usable, some spread/mismatch",
               "Weak": "wide dispersion — directional only"}.get(ds, "")
    st.markdown(f"{strengths.get(ds, '⚪')} **Data strength: {ds}**"
                + (f" ({meaning})" if meaning else "")
                + f" — based on **{res['n']} comparables**, IQR spread ±{spread * 50:.0f}%. "
                + ("Median 80% interval "
                   f"{theme.usd_short(res['ci_low'])}–{theme.usd_short(res['ci_high'])}/yr." if res['n'] else ""))

    # incumbent as a SEPARATE reference line (never blended into the number)
    if incumbent_rr:
        div = res.get("incumbent_divergence")
        note = ""
        if div is not None:
            where = "above" if div > 0 else "below"
            note = f" — **{_pct(abs(div))} {where}** the market median"
        if res.get("incumbent_outside_iqr"):
            note += " · outside the comparable P25–P75 (possible scope mismatch or a price-attack opening)"
        st.markdown(f"📍 **Incumbent run-rate: {theme.usd_short(incumbent_rr)}/yr**{note}.")

    st.plotly_chart(
        charts.ptw_strip(my_comps[~my_comps["comp_award_id"].astype(str).isin({str(x) for x in excluded})],
                         res["annual_low"], res["annual_median"], res["annual_high"], incumbent_rr),
        width="stretch")
    st.caption("Each dot is a comparable award's **actual** historical run-rate (a fact). The shaded band and median "
               "line reflect your current assumptions — so with a competition adjustment on, the band may sit off the dots.")

    with st.expander(f"Why this range — {res['n']} comparable awards + how to read it"):
        st.markdown(
            f"- **Winners only:** these are past *winning* award values, not the bids that lost — a true price-to-win "
            f"needs losing bids, which are not public.\n"
            f"- **Match tier {row.get('ptw_match_tier', '—')}** · pricing mix: {res['pricing_mix']} · "
            f"**{res['pct_in_progress'] * 100:.0f}%** are still in-progress (their obligated-to-date understates final value; "
            f"we annualize on elapsed time to correct for it).\n"
            f"- Each row links to USAspending — click through and re-derive it yourself.")
        show = my_comps.copy()
        show["run_rate"] = show["comp_run_rate"].map(theme.usd_short)
        show["USAspending"] = "https://www.usaspending.gov/award/" + show["comp_award_id"].astype(str)
        cols = {"comp_piid": "PIID", "comp_agency": "DoD Component", "comp_psc": "PSC", "run_rate": "Run-rate/yr",
                "comp_in_progress": "In progress", "comp_offers": "Offers", "match_tier": "Tier", "USAspending": "Link"}
        show = show[[c for c in cols if c in show.columns]].rename(columns=cols)
        st.dataframe(show, hide_index=True, width="stretch",
                     column_config={"Link": st.column_config.LinkColumn("USAspending", display_text="open ↗")})
    st.caption("🔒 Estimate — a market-based range from public data, not certified cost or pricing data (FAR 15.4). "
               f"Assumptions: {competition.lower()}" + (f", {term}-yr term." if projected else ", annual run-rate."))


def _render_obligation_pace(row, as_of):
    """Obligation pace — fraction of the order's ceiling obligated minus fraction of its PoP
    elapsed. A DESCRIPTIVE read of the funding profile (FFP obligates early; incremental in
    tranches) — NOT spend, and NOT a recompete forecast (Corrections C1.1). Baked at the
    snapshot; the app never recomputes it live. Refuses (no band) whenever it can't measure."""
    if "burn_basis" not in row.index or not str(row.get("burn_basis", "")):
        return
    basis = str(row.get("burn_basis"))
    cr = row.get("ceiling_burn_ratio")
    st.divider()
    st.markdown('<div class="rr-title" style="font-size:18px">Obligation pace</div>', unsafe_allow_html=True)

    if basis == "insufficient":
        st.info("**Obligation pace not measurable.** This order's ceiling (base + all options) isn't reported, "
                "it's a parent vehicle, its period-of-performance dates are missing, or it's a net deobligation — "
                "so we don't read a pace. Verify the obligations on USAspending.")
        return
    if basis == "ceiling_exceeded":
        shown = "≥10× ceiling" if (pd.notna(cr) and float(cr) >= 10) else (f"{float(cr):.0%}" if pd.notna(cr) else "—")
        st.caption(f"⚠ **Obligated exceeds the recorded ceiling ({shown})** — the ceiling field is unreliable for "
                   "this order, so we don't read a pace. Shown as a fact, not a pace.")
        return
    if basis == "fully_funded":
        pct = f"{float(cr):.0%}" if pd.notna(cr) else "—"
        st.caption(f"🔒 **Fully obligated (~{pct} of ceiling)** — no headroom left to pace against, so obligation "
                   "pace isn't informative here. Shown as a fact, no band.")
        return

    # measured — the only branch that draws a band
    band = str(row.get("burn_band"))
    bp = row.get("burn_pressure")
    time_ratio = float(cr) - float(bp)
    chip_cls = theme.BURN_CHIP.get(band, "chip-muted")
    st.markdown(
        f'<span class="chip {chip_cls}">{theme.BURN_GLYPHS.get(band, "")} '
        f'{html.escape(theme.BURN_LABELS.get(band, "—"))}</span>',
        unsafe_allow_html=True,
    )
    m1, m2 = st.columns(2)
    m1.metric("Ceiling obligated", f"{float(cr):.0%}")
    m2.metric("Clock elapsed", f"{time_ratio:.0%}")
    st.plotly_chart(charts.burn_pressure_bar(float(cr), time_ratio, band), width="stretch")
    st.caption("Both figures are facts from USAspending; the pace band is the estimate (±0.20 asserted-prior "
               "thresholds). Obligation pace reflects the **funding profile** (fully-funded awards obligate early; "
               "incremental awards in tranches), **not** the rate of spend, and does **not** forecast a recompete. "
               f"As of {as_of}.")


def _render_mods_signals(row):
    """Termination / bridge / ceiling-balloon chips from the baked MOD_COLUMNS
    (src/scoring/mods_signal.py, A2/A7). Column-guarded like _render_obligation_pace above:
    the currently-committed sample bundle predates the full mods bake, so this renders
    nothing until a row actually carries the columns. Renders no panel at all when the
    row carries the columns but none of the three signals fired (Subtraction principle —
    no empty-panel noise); each rendered chip is an ESTIMATE (◐), never a certified fact."""
    if "terminated" not in row.index:
        return
    chips = []
    if _mod_true(row.get("terminated")):
        kind = _TERMINATION_KIND_LABELS.get(str(row.get("termination_kind", "")), "partial/unclear")
        bits = []
        code = row.get("termination_code")
        if isinstance(code, str) and code.strip():
            bits.append(html.escape(code.strip()))
        tdate = row.get("termination_action_date")
        if isinstance(tdate, str) and tdate.strip():
            bits.append(html.escape(tdate.strip()))
        suffix = f" ({', '.join(bits)})" if bits else ""
        chips.append(
            f'<span class="chip chip-red">◐ Terminated (verify) — {html.escape(kind)}{suffix}</span>'
        )
    if _mod_true(row.get("bridge_flag")):
        # Chip text from the ONE outcome-taxonomy language source (S27).
        chips.append(f'<span class="chip chip-amber">◐ {html.escape(SURFACE_LANGUAGE["extension_bridge"])}</span>')
    if _mod_true(row.get("ceiling_balloon_flag")):
        ratio = row.get("ceiling_growth_ratio")
        if pd.notna(ratio):
            chips.append(f'<span class="chip chip-steel">◐ Ceiling {_pct(float(ratio) - 1.0)}</span>')
    if not chips:
        return
    st.divider()
    st.markdown('<div class="rr-title" style="font-size:18px">Termination &amp; modification signals</div>',
                unsafe_allow_html=True)
    st.markdown("  ".join(chips), unsafe_allow_html=True)
    st.caption(f"◐ Estimate — {MODS_DISCLOSURE}")


def _linked_notice_days(ctx, sel_cid):
    """D1: the live countdown (in days, vs TODAY) to this candidate's LINKED SAM.gov
    notice deadline — the NEAREST future response_deadline among its linked notice(s),
    else the MOST RECENT past one. None when there's no linked notice or no parseable
    deadline. Column-guarded exactly like the "Linked opportunity notices" table
    further down this file: bridge/notices may be absent or empty on a pre-bake bundle,
    and must never raise."""
    bridge = ctx.get("bridge")
    if bridge is None or bridge.empty or "candidate_id" not in bridge or "linked_notice_id" not in bridge:
        return None
    brow = bridge[bridge["candidate_id"].astype(str) == sel_cid]
    if "link_confidence" in brow:
        brow = brow[brow["link_confidence"] != "No Match"]
    if brow.empty:
        return None
    notices = ctx.get("notices")
    if notices is None or notices.empty or "notice_id" not in notices or "response_deadline" not in notices:
        return None
    linked = brow.merge(notices, left_on=brow["linked_notice_id"].astype(str),
                        right_on=notices["notice_id"].astype(str), how="left")
    if linked.empty or "response_deadline" not in linked:
        return None
    today = date.today()
    all_days = [d for d in (notice_response_days(v, today) for v in linked["response_deadline"]) if d is not None]
    if not all_days:
        return None
    future = [d for d in all_days if d >= 0]
    return min(future) if future else max(all_days)


def _render_notice_clock(ctx, sel_cid):
    """D1 — the live "response window closes in N days" countdown chip: recomputed
    against TODAY at every render (never baked), for candidates with a LINKED SAM.gov
    notice carrying a parseable response_deadline. Renders NOTHING when there's no
    linked notice or no parseable deadline (Subtraction principle — no noise for the
    majority of candidates that carry no notice link). Reuses the existing chip/chip-* CSS
    vocabulary from shell.py's stylesheet (no new CSS) — same pattern as
    shell.runway_chip, just a different message. ALWAYS paired with a caption naming
    the linked-notice subset so the chip can never be misread as full-pipeline
    coverage."""
    days = _linked_notice_days(ctx, sel_cid)
    if days is None:
        return
    if days >= 0:
        text = f"Response window closes in {days} day(s)"
        cls = "chip-red" if days <= 7 else "chip-amber" if days <= 30 else "chip-steel"
    else:
        text = f"Response window closed {abs(days)} day(s) ago"
        cls = "chip-muted"
    st.markdown(f'<span class="chip {cls}">{html.escape(text)}</span>', unsafe_allow_html=True)
    st.caption("for the linked-notice subset (~low coverage) — recomputed live against today, not baked.")


# Deep link: ?cid= (from the Explorer row-click) identifies the candidate. When
# it's missing OR stale (not in this dataset), show a launcher instead of silently
# defaulting to row 0 — no KeyError, and the user picks intentionally.
qp_cid = st.query_params.get("cid")
if qp_cid not in ids:
    st.markdown('<div class="rr-title" style="font-size:20px">Your top 5 fits</div>', unsafe_allow_html=True)
    st.caption("Pick a recompete candidate to open its Capture Brief — ranked by pursuit score, "
               "or search the full pipeline below.")
    for _, r in cand.sort_values("pursuit_score", ascending=False).head(5).iterrows():
        cid = str(r["candidate_id"])
        col_row, col_btn = st.columns([5, 1])
        col_row.markdown(
            shell.radar_row(r.get("title_display") or cid, r.get("subagency", "—"),
                            _months(r.get("days_until_expiration")), r.get("total_obligated_amount")),
            unsafe_allow_html=True,
        )
        if col_btn.button("Open →", key=f"open_{cid}"):
            st.query_params["cid"] = cid
            st.rerun()
    pick = st.selectbox("…or search all candidates", ["—"] + ids,
                        format_func=lambda c: labels.get(c, c) if c != "—" else "—")
    if pick != "—":
        st.query_params["cid"] = pick
        st.rerun()
    st.stop()

# Valid cid → the existing profile rendering. Keep a selectbox so users can switch
# candidates without going back to the launcher.
sel_cid = st.selectbox("Recompete candidate", ids, index=ids.index(qp_cid),
                       format_func=lambda c: labels.get(c, c))
st.query_params["cid"] = sel_cid  # keep the URL shareable/bookmarkable
row = cand[cand["candidate_id"].astype(str) == sel_cid].iloc[0]

months_left = _months(row.get("days_until_expiration"))

# ---- title + runway ----
# Always the cleaned display title — the raw contract_title can be an 800-char FPDS
# record dump with a vendor address; it stays behind the source_url link only.
_disp_title = str(row.get("title_display") or "[Untitled award — see source record]")
st.markdown(f'<div class="rr-title" style="font-size:20px">{html.escape(_disp_title)}</div>',
            unsafe_allow_html=True)

# ---- status chip + verify callout ----
_status = str(row.get("candidate_status", ""))
_status_chip = {
    "active": ('<span class="rr-badge live">ACTIVE</span>', None),
    "expired_grace": ('<span class="rr-badge sample">EXPIRED ≤90d — VERIFY</span>', None),
    "expired_stale": ('<span class="rr-badge" style="background:rgba(201,203,207,0.3);color:#6b6e73">DATA GAP — EXPIRED &gt;90d</span>',
                      "This award expired more than 90 days ago and is quarantined from the pipeline "
                      "(likely already re-awarded). Verify current status on SAM.gov before pursuing."),
}.get(_status, ("", None))
st.markdown(shell.runway_chip(months_left) + "  " + shell.runway_bar(months_left)
            + "  " + _status_chip[0], unsafe_allow_html=True)
if _status_chip[1]:
    _sam = shell.sam_gov_search_url(row.get("piid"), row.get("title_display"))
    st.warning(_status_chip[1], icon="⚠️")
    st.markdown(f"[Verify on SAM.gov ↗]({_sam})")

# ---- D1: live "response window" notice-clock chip (linked-notice subset only) ----
_render_notice_clock(ctx, sel_cid)

# ---- Eligibility lane (categorical, ABOVE the score — a gate, never a component) ----
_lane = el.lane_for(ctx, row, sel_cid, date.today())
st.markdown(el.lane_chip_html(_lane), unsafe_allow_html=True)
with st.expander("Why — evidence and the teaming path"):
    st.markdown(_lane.detail)
    if _lane.teaming:
        st.markdown(_lane.teaming)
    if profile_is_custom():
        st.caption(el.ATTESTED_NOTE)
st.caption("Eligibility is a gate, not a score — it never moves the number below.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Pursuit score", f"{row.get('pursuit_score', float('nan')):.0f}")
c2.metric("Priority tier", str(row.get("priority_tier", "—")).replace("Tier ", "T"))
c3.metric("Estimated value", usd(row.get("total_obligated_amount")))
c4.metric("Expires", str(row.get("selected_expiration_date", "—")))

# ---- Reason codes: why this scores what it does (read-only projection; the score can't move) ----
# Placed directly under the metrics (above Obligation pace) so it explains the score just shown.
chips = rc.detail_chips(row, ctx["profile"])
summary = rc.engine.summary_chips(chips)  # executive row: context/tier-6 chips dropped
st.markdown(
    '<div class="rr-title" style="font-size:16px">Why this scores '
    f'{row.get("pursuit_score", float("nan")):.0f} - {str(row.get("priority_tier", "-"))}</div>',
    unsafe_allow_html=True,
)
if summary and summary[0].code != "empty_state":
    st.markdown(shell.reason_chip_row(summary), unsafe_allow_html=True)
    st.caption("● fact · ◐ estimate · ○ not reported - we don't guess")
    with st.expander("Evidence for each reason"):
        for c in chips:  # FULL list incl. context/tier-6 chips
            st.markdown(
                f"{c.glyph} **{html.escape(c.text)}** - {html.escape(c.evidence)}  ·  "
                f"_{rc.engine.BASIS_LABELS[c.basis]}_",
                unsafe_allow_html=True,
            )
else:
    st.caption("No standout reason codes - the weighted breakdown below is the full story.")

# ---- Obligation pace (baked burn_* columns; read-only, descriptive) ----
_render_obligation_pace(row, ctx.get("as_of", "the snapshot date"))

# ---- Termination / bridge / ceiling-balloon signals (baked mod_* columns) ----
_render_mods_signals(row)

# ---- Competitive Price Range (price-to-win) ----
_render_competitive_price_range(row, ctx, sel_cid)

# Breakdown computed live from the ACTIVE profile so it always sums to the score shown.
brk = rescore.breakdown_rows(row, ctx["profile"])

left, right = st.columns([3, 2])
with left:
    st.markdown("**Contract profile**")
    fields = [
        ("PIID", "piid"), ("DoD Component", "subagency"),
        ("Incumbent", "incumbent_vendor"), ("UEI", "incumbent_uei"), ("NAICS", "naics"), ("PSC", "psc"),
        ("Award type", "award_type"), ("Extent competed", "extent_competed"),
        ("Place of performance", "place_of_performance_state"),
        ("Current end date", "current_end_date"), ("Potential end date", "potential_end_date"),
        ("Expiration basis", "expiration_date_basis"), ("Capture phase", "capture_phase"),
        ("Recompete window start (est.)", "estimated_recompete_window_start"),
        ("Recompete window end (est.)", "estimated_recompete_window_end"),
        ("Classification confidence", "classification_confidence"),
        ("Data quality notes", "data_quality_notes"),
    ]
    st.table({"Field": [n for n, k in fields if k in row.index],
              "Value": [str(row[k]) for n, k in fields if k in row.index]})
    # (The per-record data-quality flags are now surfaced as honesty-glyph reason chips above — the
    #  reason-codes data-gap/quality chips carry the same five flag_* facts, so the old duplicate
    #  data-quality caption block was subsumed and removed here per the Subtraction principle.)
    # Only a real, non-empty string URL renders — a missing link is None or NaN (float),
    # and NaN is truthy, so guard on the type to avoid a broken "nan" link (T05).
    _src = row.get("source_url")
    if isinstance(_src, str) and _src.strip():
        st.markdown(f"[🔗 View raw source record on USAspending.gov]({_src})")

with right:
    if not brk.empty:
        st.plotly_chart(charts.scoring_breakdown_bar(brk), width="stretch")
        st.caption("Blue components move when you edit your company profile; gray ones are facts of the "
                   "contract itself.")
    st.markdown("**Linked opportunity notices**")
    bridge = ctx["bridge"]
    brow = bridge[bridge["candidate_id"].astype(str) == sel_cid] if not bridge.empty and "candidate_id" in bridge else bridge.head(0)
    if "link_confidence" in brow:
        brow = brow[brow["link_confidence"] != "No Match"]
    if brow.empty:
        st.caption("No linked SAM.gov notice for this contract. Absence is **no signal** — solicitations are "
                   "usually posted only months before award (see Methodology).")
    else:
        notices = ctx.get("notices")
        if notices is not None and not notices.empty and "notice_id" in notices:
            linked = brow.merge(notices, left_on=brow["linked_notice_id"].astype(str),
                                right_on=notices["notice_id"].astype(str), how="left")
        else:
            linked = brow.copy()
        conf_order = pd.CategoricalDtype(["High", "Medium", "Low"], ordered=True)
        if "link_confidence" in linked:
            linked["link_confidence"] = linked["link_confidence"].astype(conf_order)
            linked = linked.sort_values("link_confidence")
        cols = {"link_confidence": "Confidence", "title": "Notice title", "notice_type": "Type",
                "posted_date": "Posted", "response_deadline": "Response due",
                "solicitation_number": "Solicitation #", "source_url": "SAM.gov",
                "link_reason": "Match basis"}
        show = linked[[c for c in cols if c in linked.columns]].rename(columns=cols)
        st.dataframe(show, hide_index=True, width="stretch",
                     column_config={"SAM.gov": st.column_config.LinkColumn("SAM.gov", display_text="open ↗")})
        st.caption("Fuzzy-matched from real SAM.gov notices — **treat Low confidence as a lead to verify**, "
                   "not a confirmed recompete.")

# ---- other contracts by this incumbent ----
incumbent = row.get("incumbent_vendor")
if incumbent and "incumbent_vendor" in cand:
    others = cand[(cand["incumbent_vendor"] == incumbent) & (cand["candidate_id"].astype(str) != sel_cid)]
    if not others.empty:
        st.markdown(f"**Other recompete candidates held by {html.escape(str(incumbent))}** ({len(others):,})")
        ocols = [c for c in ["title_display", "subagency", "selected_expiration_date",
                             "total_obligated_amount", "pursuit_score", "priority_tier"] if c in others.columns]
        st.dataframe(
            others[ocols].sort_values("total_obligated_amount", ascending=False).head(25),
            hide_index=True, width="stretch",
            column_config={
                "total_obligated_amount": st.column_config.NumberColumn("Est. value", format="$%d"),
                "pursuit_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            },
        )


# ---- Capture Brief (evidence-contract renderer — src/briefing via the ONE adapter) ----
brief_html = briefing.build_brief_html(ctx, row, sel_cid, date.today())
st.download_button("Download Capture Brief (print-ready HTML → save as PDF)",
                   brief_html.encode("utf-8"),
                   file_name=f"capture_brief_{sel_cid}.html", mime="text/html")

st.info("Pursuit score, priority tier, capture phase, and recompete windows are **estimates** — "
        "see the Data Quality & Methodology page.")
