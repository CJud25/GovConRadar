"""Incumbent Landscape — incumbent concentration + per-vendor detail, plus a
Market Map tab (geography, DoD components, component×PSC, FY obligation trend)."""
import html
import re

import pandas as pd
import plotly.express as px
import streamlit as st

from components import charts, export, shell, theme
from components.data import apply_filters, get_context, page_header, reportable_candidates, sidebar_filters, usd
from scoring.market_concentration import compute_hhi_concentration  # hhi_concentration
from utils.config import HHI_CONCENTRATION_CONFIG  # hhi_concentration

ctx = get_context()
page_header("Incumbent Landscape", ctx,
            subtitle="Who holds the expiring pipeline — concentration and near-term expiring exposure, from public award data.")
sel = sidebar_filters(ctx["candidates"])
full = apply_filters(ctx["candidates"], sel)
# All incumbent concentration/value analysis runs on the reportable set (Data Gap /
# quarantined rows excluded) so a re-awarded contract never inflates a vendor's footprint.
df = reportable_candidates(full)

if full.empty or "incumbent_vendor" not in full:
    st.warning("No candidates match the current filters. Widen them in the control panel on the left.")
    st.stop()

shell.needs_verification_strip(full)

tab_inc, tab_map = st.tabs(["  Incumbents  ", "  Market map  "])

with tab_inc:
    st.plotly_chart(charts.incumbent_pareto(df), width="stretch")

    # Footprint: contract count (x) vs near-term expiring value (y), sized by total pipeline.
    exp = df[df["days_until_expiration"].notna() & df["days_until_expiration"].between(0, 365)]
    agg = df.groupby("incumbent_vendor").agg(
        contracts=("candidate_id", "count"),
        pipeline_value=("total_obligated_amount", "sum"),
    ).reset_index()
    exp_val = exp.groupby("incumbent_vendor")["total_obligated_amount"].sum().rename("expiring_value")
    agg = agg.merge(exp_val, on="incumbent_vendor", how="left").fillna({"expiring_value": 0})
    if not agg.empty:
        agg["value_fmt"] = agg["expiring_value"].map(theme.usd_short)
        fig = px.scatter(agg, x="contracts", y="expiring_value", size=agg["pipeline_value"].clip(lower=1),
                         custom_data=["incumbent_vendor", "value_fmt"],
                         labels={"contracts": "Contract count", "expiring_value": "Value expiring ≤12mo"})
        fig.update_traces(marker_color=theme.STEEL, marker_line=dict(width=0.5, color="white"),
                          hovertemplate="<b>%{customdata[0]}</b><br>%{x} contracts · %{customdata[1]} ≤12mo<extra></extra>")
        theme.money_axis(fig, "y")
        st.plotly_chart(theme.style(fig, title="Incumbent footprint (count vs. near-term expiring value)"),
                        width="stretch")

    st.markdown("**Vendor detail**")
    vendor = st.selectbox("Select incumbent", sorted(df["incumbent_vendor"].dropna().unique()))
    vdf = df[df["incumbent_vendor"] == vendor]
    c1, c2, c3 = st.columns(3)
    c1.metric("Contracts in view", f"{len(vdf):,}")
    c2.metric("Pipeline value", usd(vdf["total_obligated_amount"].sum()))
    c3.metric("Avg pursuit score", f"{vdf['pursuit_score'].mean():.0f}")

    # ---- Size-standard risk badge (baked size_standard_shift/_basis on dim_vendor, F2) ----
    # Column-guarded like the mods-signal panel in views/detail.py and the bridge-watch lens in
    # views/explorer.py: the currently-committed sample bundle predates this bake, so this renders
    # nothing until dim_vendor actually carries both columns. String truthiness only —
    # size_standard_shift round-trips CSV as "True"/"False"/None, never a real bool (mirrors
    # detail.py's _mod_true). False/None renders nothing (Subtraction principle — no noise).
    dim_vendor = ctx.get("dim_vendor")
    if (dim_vendor is not None and not dim_vendor.empty
            and {"incumbent_vendor", "size_standard_shift", "size_standard_basis"}.issubset(dim_vendor.columns)):
        vrow = dim_vendor[dim_vendor["incumbent_vendor"] == vendor]
        if not vrow.empty and str(vrow.iloc[0]["size_standard_shift"]) == "True":
            basis = html.escape(str(vrow.iloc[0]["size_standard_basis"]))
            st.markdown(
                f'<span class="chip chip-amber">◐ Size-standard risk (verify)</span> {basis} '
                "— per-procurement determination; not a vendor-size verdict",
                unsafe_allow_html=True,
            )

    cols = [c for c in ["title_display", "subagency", "selected_expiration_date", "total_obligated_amount",
                        "pursuit_score", "priority_tier"] if c in vdf.columns]
    st.dataframe(vdf[cols], hide_index=True, width="stretch",
                 column_config={
                     "title_display": st.column_config.TextColumn("Contract"),
                     "subagency": st.column_config.TextColumn("DoD Component"),
                     "total_obligated_amount": st.column_config.NumberColumn("Value", format="$%d"),
                     "pursuit_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                 })
    export.export_bar(vdf, f"incumbent_{re.sub(r'[^A-Za-z0-9_-]+', '_', str(vendor))[:30]}",
                      key="exp_vendor")

with tab_map:
    st.plotly_chart(charts.state_choropleth(df), width="stretch")
    left, right = st.columns(2)
    left.plotly_chart(charts.top_bar(df, "subagency", "Pipeline value by DoD component"), width="stretch")
    right.plotly_chart(charts.agency_psc_heatmap(df), width="stretch")

    # Obligation trend by federal fiscal year, from the awards fact.
    awards = ctx["awards"]
    if not awards.empty and "date_signed" in awards and "total_obligated_amount" in awards:
        a = awards.copy()
        a["date_signed"] = pd.to_datetime(a["date_signed"], errors="coerce")
        a = a.dropna(subset=["date_signed"])
        if not a.empty:
            a["fiscal_year"] = a["date_signed"].apply(lambda d: d.year + 1 if d.month >= 10 else d.year)
            g = a.groupby("fiscal_year")["total_obligated_amount"].sum().reset_index()
            g["value_fmt"] = g["total_obligated_amount"].map(theme.usd_short)
            fig = px.bar(g, x="fiscal_year", y="total_obligated_amount", custom_data=["value_fmt"],
                         labels={"fiscal_year": "Federal fiscal year", "total_obligated_amount": "Obligations"})
            fig.update_traces(marker_color=theme.TEAL,
                              hovertemplate="FY%{x}<br>%{customdata[0]}<extra></extra>")
            theme.money_axis(fig, "y")
            st.plotly_chart(theme.style(fig, title="Obligations by federal fiscal year"), width="stretch")

    # ── Incumbent concentration (descriptive) — top-incumbent obligated-dollar share by DoD
    #    component over the reportable set. NO Herfindahl number, NO DOJ/FTC bands (Corrections
    #    v2, Option A); thin / low-coverage markets refuse to guess and render as Unknown.
    st.markdown("**Incumbent concentration (descriptive)**")
    markets = compute_hhi_concentration(df, HHI_CONCENTRATION_CONFIG)  # hhi_concentration; reads df only
    assessable = [m for m in markets if m.assessable]
    unknown = [m for m in markets if not m.assessable]
    if assessable:
        st.plotly_chart(charts.hhi_concentration_bar(assessable), width="stretch")
    if unknown:
        st.caption("Refuses to guess — shown as *Unknown*: "
                   + "; ".join(f"{m.market} ({m.reason})" for m in unknown))
    if markets:
        with st.expander("Concentration evidence (per component)"):
            ev = pd.DataFrame(
                [
                    {
                        "DoD Component": m.market,
                        "Top-incumbent share": f"{m.top_share * 100:.0f}%" if m.top_share is not None else "Unknown",
                        "Incumbents": m.n_ueis,
                        "$ attributed": f"{m.coverage * 100:.0f}%",
                        "Status": "assessable" if m.assessable else m.reason,
                    }
                    for m in sorted(markets, key=lambda mk: (mk.top_share is None, -(mk.top_share or 0.0)))
                ]
            )
            st.dataframe(ev, hide_index=True, width="stretch")
    st.caption("A descriptive read of incumbent dollar-share within this expiring recompete set — "
               "not market share, market power, or contestability.")
