"""
GovCon Recompete Radar — Streamlit companion app (home = "Monday Briefing").

Reads the same data/powerbi/ star schema the Power BI report uses (single source
of truth); falls back to a bundled synthetic sample so it runs on Streamlit
Community Cloud with no pipeline or API access. Run:  streamlit run streamlit_app/app.py
"""

import html

import pandas as pd
import streamlit as st

from components import charts, export, shell, theme
from components.data import (
    BRIDGE_WATCH_COPY,
    BRIDGE_WATCH_LABEL,
    DISCLAIMER,
    apply_filters,
    bridge_watch_mask,
    get_context,
    reportable_candidates,
    sidebar_filters,
)

MONTHS = 30.44  # avg days per month, for days -> months


def _months_left(days):
    return None if pd.isna(days) else days / MONTHS


def main():
    ctx = get_context()
    shell.render_header(ctx)

    sel = sidebar_filters(ctx["candidates"])
    full = apply_filters(ctx["candidates"], sel)
    # Every headline number, chart, tier board, and default export runs on the
    # reportable set (Data Gap / quarantined rows excluded). The quarantined rows are
    # surfaced ONLY via the Needs-verification strip, which reads `full`.
    df = reportable_candidates(full)

    if full.empty:
        st.warning("No candidates match the current filters. Widen the filters in the control panel on the left.")
        return

    # ---- headline metrics ----
    tier1_mask = df["priority_tier"] == "Tier 1: Pursue Now"
    tier1 = int(tier1_mask.sum())
    tier1_expired = int((tier1_mask & df["days_until_expiration"].notna()
                         & (df["days_until_expiration"] < 0)).sum())
    exp_12_mask = df["days_until_expiration"].notna() & df["days_until_expiration"].between(0, 365)
    actionable_12 = df.loc[exp_12_mask, "total_obligated_amount"].sum()
    tier1_12_value = df.loc[tier1_mask & exp_12_mask, "total_obligated_amount"].sum()
    tier1_12_count = int((tier1_mask & exp_12_mask).sum())
    active = df[df["days_until_expiration"].notna() & (df["days_until_expiration"] >= 0)]
    median_runway = _months_left(active["days_until_expiration"].median()) if not active.empty else None
    inc = df.groupby("incumbent_vendor")["total_obligated_amount"].sum().sort_values(ascending=False)
    top5_share = (inc.head(5).sum() / inc.sum() * 100) if inc.sum() > 0 else 0
    median_label = f"T–{int(round(median_runway))} MO" if median_runway is not None else "—"

    # ---- hero (onboarding + CTA), personalized to the active company profile ----
    prof = ctx.get("profile") or {}
    custom = ctx.get("profile_custom")
    who = (prof.get("company_name") or "your company") if custom else None
    # Escape — company_name is user/URL-supplied and rendered as raw HTML below.
    fit_clause = f'that fit <span class="hi">{html.escape(who)}</span> ' if custom else ""
    st.markdown(
        f'<div class="rr-hero"><span class="hi">{theme.usd_short(tier1_12_value)}</span> across '
        f'<span class="hi">{tier1_12_count:,} Tier-1 recompetes</span> {fit_clause}entering their pursuit window in the next 12 months.'
        "</div>"
        '<div class="rr-subcopy">A <b>recompete candidate</b> is an expiring DoD cyber/IT contract likely to be '
        're-competed. Each is scored 0–100 for pursuit fit and tiered — transparently, so you can defend the call.'
        "</div>",
        unsafe_allow_html=True,
    )
    if not custom:
        st.markdown(
            '<div style="background:rgba(242,169,0,0.14);border-left:4px solid #F2A900;border-radius:8px;'
            'padding:10px 14px;margin:10px 0;color:#8A6100;font-size:13.5px">'
            '⚠️ These scores rank fit for a <b>synthetic demo company</b>. Enter your company (≈60 seconds) to see '
            '<b>your</b> pursuit scores across the whole pipeline.</div>',
            unsafe_allow_html=True,
        )
        st.page_link("views/company.py", label="Enter your company  →")
    else:
        st.page_link("views/explorer.py", label=f"View {who}'s pipeline board  →")
    st.write("")

    # ---- KPI cards (4 always + 1 column-guarded bridge-watch card) ----
    kpis = [
        {"label": "Tier 1 — Pursue Now", "value": f"{tier1:,}", "accent": theme.REDORANGE,
         "sub": f"{tier1_12_count:,} in next 12 mo · {tier1_expired:,} already expired"},
        {"label": "Actionable value ≤ 12 mo", "value": theme.usd_short(actionable_12), "accent": theme.STEEL,
         "sub": f"{int(exp_12_mask.sum()):,} contracts expiring"},
        {"label": "Median runway", "value": median_label, "accent": theme.AMBER,
         "sub": "median time to expiration"},
        {"label": "Top-5 incumbent share", "value": f"{top5_share:.0f}%", "accent": theme.NAVY,
         "sub": "of pipeline value"},
    ]
    # Bridge-watch KPI: candidate_status == "expired_grace" AND successor_visible_basis
    # == "none_visible" (single-sourced predicate — see components.data). The currently-
    # committed sample bundle predates the successor_proxy bake, so this card is
    # column-guarded and simply absent until a later full bake adds both columns.
    if {"candidate_status", "successor_visible_basis"}.issubset(df.columns):
        bw_count = int(bridge_watch_mask(df).sum())
        kpis.append({"label": BRIDGE_WATCH_LABEL, "value": f"{bw_count:,}", "accent": theme.GRAY,
                     "sub": BRIDGE_WATCH_COPY})
    shell.kpi_row(kpis)
    st.write("")

    # Honest counterweight to the headline: the quarantined records excluded from it.
    shell.needs_verification_strip(full)
    st.write("")

    # ---- runway board + On the Radar ----
    board, radar = st.columns([2, 1])
    with board:
        st.markdown('<div class="rr-title" style="font-size:16px">T-minus runway — Tier 1 & 2</div>',
                    unsafe_allow_html=True)
        focus = df[df["priority_tier"].isin(["Tier 1: Pursue Now", "Tier 2: Capture Research"])]
        st.plotly_chart(charts.recompete_timeline(focus, height=430), width="stretch")
    with radar:
        st.markdown('<div class="rr-title" style="font-size:16px">On the radar</div>', unsafe_allow_html=True)
        st.caption("Soonest to expire, in view")
        soon = active.sort_values("days_until_expiration").head(6)
        rows = "".join(
            shell.radar_row(r.get("title_display") or r.get("candidate_id", ""), r.get("subagency", "—"),
                            _months_left(r["days_until_expiration"]), r.get("total_obligated_amount"))
            for _, r in soon.iterrows()
        )
        st.markdown(rows or "<div class='m'>Nothing in the active window.</div>", unsafe_allow_html=True)

    st.divider()

    # ---- survey charts, demoted to tabs ----
    t_timing, t_where, t_who = st.tabs(["  Timing  ", "  Where  ", "  Who  "])
    with t_timing:
        c1, c2 = st.columns(2)
        c1.plotly_chart(charts.value_by_bucket(df), width="stretch")
        c2.plotly_chart(charts.capture_phase_bar(df), width="stretch")
    with t_where:
        st.plotly_chart(charts.state_choropleth(df, height=440), width="stretch")
    with t_who:
        w1, w2 = st.columns(2)
        w1.plotly_chart(charts.top_bar(df, "subagency", "Top DoD components by pipeline value"), width="stretch")
        w2.plotly_chart(charts.top_bar(df, "incumbent_vendor", "Top incumbents by pipeline value"),
                        width="stretch")

    st.divider()

    # ---- Tier 1 board ----
    st.markdown('<div class="rr-title" style="font-size:16px">Tier 1 — Pursue Now</div>', unsafe_allow_html=True)
    t1 = df[tier1_mask].copy()
    # Tag grace rows (expired ≤90d, still on the board but flagged to verify). Stale
    # rows are Data Gap by construction and never appear here.
    if "candidate_status" in t1.columns:
        t1["status_flag"] = t1["candidate_status"].map(
            {"active": "", "expired_grace": "⚠ verify (expired ≤90d)"}).fillna("")
    cols = [c for c in ["title_display", "status_flag", "subagency", "incumbent_vendor",
                        "selected_expiration_date", "total_obligated_amount", "pursuit_score"] if c in t1.columns]
    if t1.empty:
        st.info("No Tier 1 candidates in the current filter — adjust filters or review Tier 2.")
    else:
        st.dataframe(
            t1[cols].sort_values("pursuit_score", ascending=False), hide_index=True, width="stretch",
            column_config={
                "title_display": st.column_config.TextColumn("Contract"),
                "status_flag": st.column_config.TextColumn("Status"),
                "subagency": st.column_config.TextColumn("DoD Component"),
                "incumbent_vendor": st.column_config.TextColumn("Incumbent"),
                "selected_expiration_date": st.column_config.TextColumn("Expires"),
                "total_obligated_amount": st.column_config.NumberColumn("Est. value", format="$%d"),
                "pursuit_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            },
        )

    export.export_bar(df, "recompete_candidates", key="exp_home")
    st.caption(DISCLAIMER)


main()
