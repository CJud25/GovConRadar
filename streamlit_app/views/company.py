"""Your Company — enter a company profile once; every pursuit score across the app
is recomputed live against it. The app's answer to "is this pipeline for ME?"."""
import datetime

import pandas as pd
import streamlit as st

from components import eligibility_lane as el
from components import rescore, theme
from components.data import (
    CERT_TOKENS,
    clear_profile,
    get_context,
    get_profile,
    page_header,
    profile_is_custom,
    set_profile,
)

# Display labels for the attested-cert tokens (tokens are the stored vocabulary).
_CERT_LABELS = {"8A": "8(a)", "HUBZONE": "HUBZone"}

ctx = get_context()
page_header("Your Company", ctx,
            subtitle="Enter your company once — every pursuit score in the app becomes yours.")

st.markdown(
    "Pursuit scores rank how well each expiring contract fits **your** capabilities. "
    "About **55%** of the score comes from your profile (capabilities, NAICS/PSC, past-performance "
    "DoD components, comfortable value, geography); the other **45%** is intrinsic to the contract "
    "(urgency, recompete confidence, competition posture, data quality). It's a transparent "
    "estimate — change an input and watch the scores move."
)

cands = ctx["candidates"]

# ---- option seeds from the data itself ----
def _counts(col):
    if col not in cands:
        return {}
    s = cands[col].dropna().astype(str).str.split(".").str[0] if col == "naics" else cands[col].dropna().astype(str)
    return s.value_counts().to_dict()

naics_counts, psc_counts = _counts("naics"), _counts("psc")
naics_opts = sorted(naics_counts, key=naics_counts.get, reverse=True)
psc_opts = sorted(psc_counts, key=psc_counts.get, reverse=True)
# DoD components (subagency) — `agency` is a constant "DEPARTMENT OF DEFENSE" for
# the whole DoD scope, so past-performance matching keys off the component.
agency_opts = sorted(cands["subagency"].dropna().astype(str).unique()) if "subagency" in cands else []
state_opts = sorted(cands["place_of_performance_state"].dropna().astype(str).unique()) if "place_of_performance_state" in cands else []
naics_titles = {}
if not ctx["dim_naics"].empty:
    dn = ctx["dim_naics"]
    code_col = next((c for c in ["naics_code", "naics"] if c in dn), None)
    title_col = next((c for c in dn.columns if "title" in c.lower() or "desc" in c.lower()), None)
    if code_col and title_col:
        naics_titles = {str(k).split(".")[0]: str(v) for k, v in zip(dn[code_col], dn[title_col])}
cap_seed = sorted(set(rescore.DEMO_PROFILE["capabilities"] + [
    "zero trust", "SOC / SIEM", "DevSecOps", "data analytics", "systems engineering",
    "cloud migration", "help desk", "network operations", "software development"]))
VALUE_STEPS = [1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000, 100_000_000, 250_000_000]

# ---- quick-start (outside the form) ----
draft = st.session_state.get("profile_draft") or (dict(get_profile()) if profile_is_custom() else dict(rescore.BLANK_PROFILE))
qs1, qs2, _ = st.columns([1, 1, 3])
if qs1.button("Load demo profile", width="stretch"):
    st.session_state["profile_draft"] = dict(rescore.DEMO_PROFILE); st.rerun()
if qs2.button("Start blank", width="stretch"):
    st.session_state["profile_draft"] = dict(rescore.BLANK_PROFILE); st.rerun()

# ---- the profile form ----
# Form key MUST differ from the "company_profile" session_state key that set_profile()
# writes below: a form's key shares Streamlit's widget/session_state namespace, so
# reusing "company_profile" made `st.session_state["company_profile"] = ...` (in
# set_profile, on submit) raise StreamlitAPIException ("cannot be modified after the
# widget with key company_profile is instantiated").
with st.form("company_profile_form"):
    name = st.text_input("Company name", value=draft.get("company_name", ""), placeholder="Acme Cyber, LLC")
    naics_sel = st.multiselect(
        "Preferred NAICS  *", naics_opts, default=[n for n in draft.get("preferred_naics", []) if n in naics_opts],
        format_func=lambda c: f"{c} — {naics_titles.get(c, 'NAICS')[:34]} ({naics_counts.get(c, 0):,})")
    caps = st.multiselect(
        "Capabilities / keywords  *", cap_seed,
        default=[c for c in draft.get("capabilities", []) if c] or None,
        accept_new_options=True, help="Type to add your own — matched against contract titles.")
    st.caption("★ Enter at least one NAICS **or** capability. Everything below is optional.")
    with st.expander("Fine-tune (optional) — PSC, past performance, value, geography"):
        psc_sel = st.multiselect("Preferred PSC", psc_opts,
                                 default=[p for p in draft.get("preferred_psc", []) if p in psc_opts],
                                 format_func=lambda c: f"{c} ({psc_counts.get(c, 0):,})")
        agencies_sel = st.multiselect("DoD components with past performance", agency_opts,
                                      default=[a for a in draft.get("agencies_with_past_performance", []) if a in agency_opts])
        default_val = min(VALUE_STEPS, key=lambda v: abs(v - (draft.get("max_comfortable_contract_value") or 25_000_000)))
        max_val = st.select_slider("Max comfortable contract value", VALUE_STEPS, value=default_val,
                                   format_func=theme.usd_short)
        nationwide = st.checkbox("Nationwide / remote (skip state matching)", value=bool(draft.get("nationwide")))
        states_sel = st.multiselect("States served", state_opts, disabled=nationwide,
                                    default=[s for s in draft.get("states_served", []) if s in state_opts])
        certs_sel = st.multiselect("Certifications you hold (self-attested)", list(CERT_TOKENS),
                                   default=[c for c in draft.get("certs", []) if c in CERT_TOKENS],
                                   format_func=lambda t: _CERT_LABELS.get(t, t))
        exit_8a = ""
        if "8A" in certs_sel:
            prior = pd.to_datetime(draft.get("exit_8a") or None, errors="coerce")
            d8a = st.date_input("8(a) program exit date (if known)",
                                value=prior.date() if pd.notna(prior) else None,
                                min_value=datetime.date(2000, 1, 1), max_value=datetime.date(2100, 1, 1))
            exit_8a = d8a.isoformat() if d8a else ""
        sb_small = st.checkbox("Small business under your preferred NAICS (self-certified)",
                               value=bool(draft.get("sb_small_naics")))
        st.caption("Self-attested — used only to check set-aside eligibility. "
                   "The radar never verifies certifications.")
    st.caption("🔒 Your profile is saved in this page's URL — **not on any server** — so anyone you share the "
               "link with can see your company's capabilities and targets. Share it like you'd share the profile itself.")
    submitted = st.form_submit_button("Score my pipeline  →", type="primary", width="stretch")

if submitted:
    if not (naics_sel or caps):
        st.error("Add at least one NAICS or capability so we can score fit for your company.")
    else:
        profile = {
            "company_name": name.strip() or "Your company", "capabilities": caps,
            "preferred_naics": naics_sel, "preferred_psc": psc_sel,
            "agencies_with_past_performance": agencies_sel,
            "max_comfortable_contract_value": max_val, "states_served": states_sel,
            "nationwide": nationwide, "is_demo": False,
            "certs": certs_sel, "exit_8a": exit_8a, "sb_small_naics": sb_small,
        }
        set_profile(profile)
        st.session_state.pop("profile_draft", None)
        st.toast(f"Pipeline re-scored for {profile['company_name']}.", icon="🎯")
        st.switch_page("views/home.py")

# ---- current status + impact ----
st.divider()
if profile_is_custom():
    p = get_profile()
    st.success(f"**Scoring as {p.get('company_name', 'your company')}.** Change anything above and re-submit to rescore.")
    if {"priority_tier", "priority_tier_demo"}.issubset(cands.columns):
        order = ["Tier 1: Pursue Now", "Tier 2: Capture Research", "Tier 3: Monitor", "Tier 4: Low Priority"]
        now = cands["priority_tier"].value_counts()
        base = cands["priority_tier_demo"].value_counts()
        rows = [{"Tier": t.split(":")[0], "Demo baseline": int(base.get(t, 0)), "Your company": int(now.get(t, 0)),
                 "Δ": int(now.get(t, 0) - base.get(t, 0))} for t in order]
        st.markdown("**How your profile changed the board** (vs. the synthetic demo baseline, as of the snapshot date):")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    entity = el.entity_from_profile(p)
    if entity is not None and not cands.empty:
        # Data-Gap quarantine excluded (house rule: never in a headline denominator) —
        # the strip tallies the real forward pipeline, not stale dead records.
        strip_pool = cands[cands["priority_tier"].astype(str) != "Data Gap"] \
            if "priority_tier" in cands.columns else cands
        counts = el.lane_counts(strip_pool, entity, datetime.date.today())
        st.markdown(
            f"Prime-path check across {len(strip_pool):,} candidates: {counts['gate']} gated · "
            f"{counts['warn']} cautions · {counts['clear']} clear · {counts['unknown']} unknown"
        )
        st.caption(
            "Historical set-asides vs. your attested certifications. Unknown dominates honestly — "
            "most records don't report a set-aside, and blank does not mean unrestricted. "
            "Open any contract for its live-notice check."
        )
    if st.button("Reset to demo profile"):
        clear_profile(); st.session_state.pop("profile_draft", None); st.rerun()
else:
    st.info("You're viewing the **synthetic demo profile**. Enter your company above to see *your* pursuit scores.")
