"""Pipeline Explorer — search-first workbench: the trusted default view (spec §3.3),
global search, shareable filter chips, a ranked candidate table with runway columns,
and row → Contract Detail."""
import html

import pandas as pd
import streamlit as st

from components import charts, export, theme
from components import reason_codes as rc
from components.data import (
    BRIDGE_WATCH_COPY,
    BRIDGE_WATCH_LABEL,
    active_filter_chips,
    apply_filters,
    bridge_watch_mask,
    get_context,
    page_header,
    sidebar_filters,
)

# src/ is on sys.path (app.py) — import the ONE trusted-view definition, no inlined twin,
# plus the E1 early-warning lens (Sources Sought / RFI classifier + filter, also pure).
from scoring.notice_lens import early_warning_notices
from transform.recompete import default_pipeline_view

# E1 — the Sources Sought / RFI early-warning lane is staged on whatever notices are
# ALREADY baked into fact_opportunity_notices; it never calls the SAM.gov API live. Its
# CURRENCY depends on the owner refreshing the SAM bulk CSV (see docs/data_acquisition_plan.md
# and docs/SOP_Recompete_Radar_v2.1.md) — shown as a standing caption, never silently assumed.
EARLY_WARNING_STALENESS_NOTE = (
    "Staleness note: this lane lists notices already captured in the snapshot — it does not "
    "call the SAM.gov API live. Freshness depends on the last SAM bulk-export refresh "
    "(`ContractOpportunitiesFullCSV.csv`); see docs/data_acquisition_plan.md. "
    "Coverage note: SAM's notice_type enum has no explicit 'Request for Information' value "
    "on current exports — RFIs typically post under Sources Sought (listed here) or "
    "Special Notice (not listed); the classifier also matches an explicit RFI type if one "
    "ever appears."
)

MONTHS = 30.44

_TIER_RANK = {"Tier 1: Pursue Now": 0, "Tier 2: Capture Research": 1,
              "Tier 3: Monitor": 2, "Tier 4: Low Priority": 3, "Data Gap": 4}

# D15 — the fixed mods-signal disclosure (A7 spec), rendered verbatim on every
# mods-derived surface. Duplicated (not imported) from views/detail.py: shell.py is
# off-limits for this task and the two views don't otherwise share a module.
MODS_DISCLOSURE = "DoD FPDS reporting lags ~90 days; termination signals are ≥3 months old."


def _mod_true(v) -> bool:
    """House truthiness for the baked MOD_COLUMNS (A7): they round-trip CSV as a real
    bool OR as the strings "True"/"False" (NaN for missing/not-yet-baked) — never
    rely on bool(). Mirrors the same helper in views/detail.py."""
    return str(v) == "True"


def _vehicle_rollup(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse task orders sharing a referenced_idv_piid into one vehicle row:
    order count, summed value, earliest expiration, best (lowest-numbered) tier."""
    grp = df[df["referenced_idv_piid"].notna()]
    if grp.empty:
        return pd.DataFrame(columns=["vehicle", "vehicle_title", "order_count",
                                     "total_value", "earliest_expiration", "best_tier"])
    rows = []
    for idv, g in grp.groupby("referenced_idv_piid"):
        best = min(g["priority_tier"], key=lambda t: _TIER_RANK.get(t, 9))
        title = g["title_display"].iloc[0] if "title_display" in g.columns else str(idv)
        rows.append({
            "vehicle": str(idv),
            "vehicle_title": str(title)[:70],
            "order_count": int(len(g)),
            "total_value": float(g["total_obligated_amount"].sum()),
            "earliest_expiration": g["selected_expiration_date"].min(),
            "best_tier": best,
        })
    return (pd.DataFrame(rows).sort_values(["order_count", "total_value"], ascending=False)
            .reset_index(drop=True))


ctx = get_context()
page_header("Pipeline Explorer", ctx, subtitle="Search, rank, and open any recompete candidate.")
sel = sidebar_filters(ctx["candidates"])
df = apply_filters(ctx["candidates"], sel)

if df.empty:
    st.warning("No candidates match the current filters. Widen them in the control panel on the left.")
    st.stop()

# Active-filter chip strip (shareable view lives in the URL).
chips = active_filter_chips(sel)
if chips:
    st.markdown(chips, unsafe_allow_html=True)

# ---- trusted default view (spec §3.3) ----
# The base frame every tab (table, charts, calendar) shares is default_pipeline_view:
# forward-dated + Medium/High classification confidence + no Data-Gap quarantine.
# Excluded rows are never deleted — the checkbox surfaces them for audit with an honest
# live count, and the status multiselect (audit mode only) narrows them further.
total = len(df)
trusted = default_pipeline_view(df)
n_hidden = total - len(trusted)
include_excluded = st.checkbox(
    f"Include excluded rows ({n_hidden:,} hidden: expired / low-confidence / Data Gap)",
    key="explorer_include_excluded",
    help="Off = the trustworthy default view (§3.3). On = every row matching the sidebar "
         "filters, so you can audit exactly what the default hides.")
STATUS_LABELS = {"active": "Active", "expired_grace": "Expired ≤90d (verify)",
                 "expired_stale": "Needs verification (expired >90d)"}
if include_excluded:
    # Audit mode: ALL THREE statuses render by default so the hidden count advertised on
    # the checkbox always matches what's on screen; narrowing by status is opt-in.
    if "candidate_status" in df.columns:
        picked_labels = st.multiselect(
            "Status", list(STATUS_LABELS.values()),
            default=list(STATUS_LABELS.values()), key="explorer_status",
            help="Audit narrowing only — deselect a status to focus the excluded rows.")
        inv = {v: k for k, v in STATUS_LABELS.items()}
        picked = [inv[x] for x in picked_labels] or list(STATUS_LABELS)
        df = df[df["candidate_status"].isin(picked)]
    st.caption(f"Showing {len(df):,} of {total:,} candidates — audit view includes expired, "
               "low-confidence, and Data-Gap rows.")
    if df.empty:
        st.info("No candidates for the selected status. Add a status above.")
        st.stop()
else:
    df = trusted
    st.caption(f"Showing {len(df):,} of {total:,} candidates — default view is forward-dated, "
               "Medium/High classification confidence, no Data-Gap quarantine. Nothing is "
               "deleted — include excluded rows to audit.")
    if df.empty:
        st.info(f"No candidates in the default view for these filters, but {n_hidden:,} excluded "
                "row(s) match — check **Include excluded rows** above to audit them.")
        st.stop()

# ---- global search ----
q = st.text_input("Search", placeholder="Search contract title, incumbent, DoD component, or ID…",
                  label_visibility="collapsed")
view = df
if q:
    needle = q.strip().lower()
    search_cols = [c for c in ["title_display", "contract_title", "incumbent_vendor", "subagency", "candidate_id"] if c in view.columns]
    mask = pd.Series(False, index=view.index)
    for c in search_cols:
        mask |= view[c].astype(str).str.lower().str.contains(needle, na=False, regex=False)
    view = view[mask]

view = view.copy().reset_index(drop=True)
if "days_until_expiration" in view:
    view["months_left"] = (view["days_until_expiration"] / MONTHS).round(0)
    view["tminus"] = view["months_left"].map(lambda m: f"T–{int(m)} MO" if pd.notna(m) and m >= 0
                                             else (f"EXPIRED {abs(int(m))}MO" if pd.notna(m) else "—"))
# Obligation-pace chip (baked; fact-based). not_applicable renders a bare "—" (C1.3);
# the filter degrades safely on a pre-burn bundle that lacks burn_band.
if "burn_band" in view.columns:
    _REAL_BANDS = {"burning_hot", "on_pace", "underutilized"}
    view["burn"] = view["burn_band"].map(
        lambda b: f"{theme.BURN_GLYPHS.get(b, '')} {theme.BURN_LABELS.get(b, '')}".strip()
        if b in _REAL_BANDS else "—"
    )

# ---- Terminated / bridge lens (baked mod_* columns, A7) ----
# Column-guarded like the burn block above: the currently-committed sample bundle
# predates the full mods bake, so this renders nothing until MOD_COLUMNS are present.
# A compact indicator column travels on `view` unconditionally (so it also reaches the
# vehicle-rollup and shortlist frames downstream); the narrowing checkbox is opt-in.
if {"terminated", "bridge_flag"}.issubset(view.columns):
    view["mods"] = view.apply(
        lambda r: " · ".join(
            t for t in (
                "◐ Terminated" if _mod_true(r.get("terminated")) else None,
                "◐ Bridge" if _mod_true(r.get("bridge_flag")) else None,
            ) if t
        ) or "—",
        axis=1,
    )
    term_bridge_only = st.checkbox(
        "Terminated / bridge only",
        key="explorer_term_bridge",
        help="Narrow to candidates with an observed termination code or a non-competed "
             f"bridge extension (mod-derived estimate). {MODS_DISCLOSURE}")
    if term_bridge_only:
        view = view[view["terminated"].map(_mod_true) | view["bridge_flag"].map(_mod_true)]

# ---- Bridge-watch lens (baked candidate_status / successor_visible_basis, B2) ----
# Column-guarded like the mods block above: the currently-committed sample bundle
# predates the successor_proxy bake, so this renders nothing until both columns are
# present. The predicate and the fixed copy are single-sourced in components.data so
# this checkbox and the Home KPI can never drift on the definition or the wording.
if {"candidate_status", "successor_visible_basis"}.issubset(view.columns):
    bridge_watch_only = st.checkbox(
        BRIDGE_WATCH_LABEL,
        key="explorer_bridge_watch",
        help=f"Narrow to expired-grace candidates (lapsed ≤90 days) with {BRIDGE_WATCH_COPY}.")
    if bridge_watch_only:
        view = view[bridge_watch_mask(view)]
        if view.empty and not include_excluded:
            # The lens cohort is expired_grace rows, which the trusted default view
            # excludes by construction (forward-dated only, T17). Never silently widen
            # the trusted base — point at the ONE explicit audit toggle instead.
            st.info(
                "Recently-lapsed candidates sit outside the trusted default view "
                "(forward-dated only). Check **Include excluded rows** above to see the "
                f"bridge-watch cohort — {BRIDGE_WATCH_COPY}."
            )
            st.stop()
        st.caption(f"{len(view):,} candidate(s) — {BRIDGE_WATCH_COPY}.")

# ---- default best-fit shortlist: Tier 1–2 candidates inside an ~18-month runway.
# The table/selection/export all run on table_df so the CSV + row-click index match
# exactly what's on screen; the Charts + Calendar tabs stay on the full `view`.
SHORTLIST_DAYS = 548  # ~18 months
if {"priority_tier", "days_until_expiration"}.issubset(view.columns):
    shortlist_mask = (
        view["priority_tier"].isin(["Tier 1: Pursue Now", "Tier 2: Capture Research"])
        & view["days_until_expiration"].between(0, SHORTLIST_DAYS)
    )
else:
    shortlist_mask = pd.Series(True, index=view.index)
shortlisted = view[shortlist_mask]
show_all = st.session_state.setdefault("explorer_show_all", False)
# Audit mode bypasses the shortlist entirely: the shortlist is a fit convenience for
# the trusted default view, and audit mode advertises EVERY row matching the sidebar
# filters + status multiselect — the rendered table must equal that advertised count.
use_shortlist = ((not include_excluded) and (not show_all)
                 and not shortlisted.empty and len(shortlisted) < len(view))
table_df = ((shortlisted if use_shortlist else view)
            .sort_values("pursuit_score", ascending=False).reset_index(drop=True))
# ---- "Why" column: top-2 profile-INDEPENDENT fact chips per row (glyph + 18-char reason label).
# Built on table_df (the small, sorted, SHOWN frame) so there's no per-row re-scoring and the cell
# is glyph-prefixed → formula-safe. explorer_chips passes component_scores=None → stable across ?p=.
table_df = table_df.copy()
table_df["reasons"] = table_df.apply(
    lambda r: " · ".join(f"{c.glyph} {rc.engine.grid_label(c)}" for c in rc.explorer_chips(r)) or "—",
    axis=1,
)

tab_table, tab_charts, tab_cal, tab_early_warning = st.tabs(
    ["  Candidates  ", "  Charts  ", "  Capture calendar  ", "  Early warning  "]
)

with tab_table:
    if use_shortlist:
        st.caption(f"Showing your {len(table_df):,} best-fit, in-window candidates "
                   "(Tier 1–2, ≤18 mo runway) — click a row to open its Capture Brief.")
        if st.button(f"Show all {len(view):,}", key="btn_show_all"):
            st.session_state["explorer_show_all"] = True
            st.rerun()
    else:
        st.caption(f"{len(view):,} candidate(s) — click a row to open its Capture Brief.")
        # The shortlist button pair is a trusted-view affordance only — never in audit mode.
        if not include_excluded and not shortlisted.empty and len(shortlisted) < len(view):
            if st.button(f"Back to best-fit shortlist ({len(shortlisted):,})", key="btn_shortlist"):
                st.session_state["explorer_show_all"] = False
                st.rerun()
    # ---- vehicle rollup: collapse many task orders under one IDV into a vehicle row.
    # A BD team pursues the vehicle, not 212 identical task-order rows.
    rollup = st.checkbox("Roll up by contract vehicle (group task orders under their IDV)",
                         key="explorer_rollup")
    if rollup and "referenced_idv_piid" in table_df.columns:
        grouped = _vehicle_rollup(table_df)
        n_orders = int(table_df["referenced_idv_piid"].notna().sum())
        st.caption(f"{len(grouped):,} vehicles · {n_orders:,} task orders "
                   f"· {int(table_df['referenced_idv_piid'].isna().sum()):,} standalone awards")
        st.dataframe(
            grouped, hide_index=True, width="stretch",
            column_config={
                "vehicle": st.column_config.TextColumn("Vehicle (IDV)"),
                "vehicle_title": st.column_config.TextColumn("Title"),
                "order_count": st.column_config.NumberColumn("Orders", width="small"),
                "total_value": st.column_config.NumberColumn("Total value", format="$%d"),
                "earliest_expiration": st.column_config.TextColumn("Earliest expires"),
                "best_tier": st.column_config.TextColumn("Best tier"),
            },
        )
        export.export_bar(grouped, "pipeline_vehicles", key="exp_explorer_veh")
    else:
        # NOTE: filter against table_df (the RENDERED frame), not `view` — table_df carries every `view`
        # column plus render-only projections like `reasons`. Any column added to table_df upstream and
        # named in display_cols will auto-surface here; keep display_cols the allow-list of intent.
        display_cols = [c for c in [
            "priority_tier", "tminus", "months_left", "burn", "mods", "pursuit_score", "reasons", "title_display",
            "subagency", "incumbent_vendor", "selected_expiration_date", "total_obligated_amount", "source_url",
        ] if c in table_df.columns]
        event = st.dataframe(
            table_df[display_cols], hide_index=True, width="stretch",
            on_select="rerun", selection_mode="single-row",
            column_config={
                "priority_tier": st.column_config.TextColumn("Tier", width="small"),
                "tminus": st.column_config.TextColumn("T-minus", width="small"),
                "months_left": st.column_config.ProgressColumn("Runway (mo)", min_value=0, max_value=24, format="%d"),
                "burn": st.column_config.TextColumn(
                    "Oblig. pace", width="small",
                    help="Obligation-vs-PoP pace (fact-based; reflects the funding profile — not spend, not a "
                         "recompete forecast). '—' where the order ceiling isn't reported, is fully obligated, or "
                         "exceeds ceiling — open Contract Detail."),
                "mods": st.column_config.TextColumn(
                    "Terminated / bridge", width="small",
                    help=f"Mod-derived estimate (◐). {MODS_DISCLOSURE} '—' where neither signal fired."),
                "pursuit_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                "reasons": st.column_config.TextColumn(
                    "Why", width="medium",
                    help="Top fact-based reasons (profile-independent). ● fact · ◐ estimate · ○ not reported. "
                         "Full evidence + your-profile fit reasons are on Contract Detail."),
                "title_display": st.column_config.TextColumn("Contract"),
                "subagency": st.column_config.TextColumn("DoD Component"),
                "incumbent_vendor": st.column_config.TextColumn("Incumbent"),
                "selected_expiration_date": st.column_config.TextColumn("Expires"),
                "total_obligated_amount": st.column_config.NumberColumn("Est. value", format="$%d"),
                "source_url": st.column_config.LinkColumn("Source", display_text="USAspending"),
            },
        )
        # Row selection → open Contract Detail via ?cid= deep link.
        if event.selection.rows and "candidate_id" in table_df.columns:
            cid = str(table_df.iloc[event.selection.rows[0]]["candidate_id"])
            st.query_params["cid"] = cid
            st.switch_page("views/detail.py")

        export.export_bar(table_df, "pipeline", key="exp_explorer")

with tab_charts:
    c1, c2 = st.columns(2)
    c1.plotly_chart(charts.score_vs_value(view), width="stretch")
    c2.plotly_chart(charts.capture_phase_bar(view), width="stretch")
    st.markdown("**Pipeline value by dimension**")
    group_label = st.radio("Group by", ["DoD Component", "Incumbent", "NAICS", "PSC"], horizontal=True,
                           label_visibility="collapsed")
    group_col = {"DoD Component": "subagency", "Incumbent": "incumbent_vendor", "NAICS": "naics", "PSC": "psc"}[group_label]
    st.plotly_chart(charts.top_bar(view, group_col, f"Pipeline value by {group_label.lower()}"),
                    width="stretch")

with tab_cal:
    st.caption("Estimated recompete windows — when to start capture, not official solicitation dates.")
    st.plotly_chart(charts.recompete_timeline(view), width="stretch")
    st.plotly_chart(charts.expiration_histogram(view), width="stretch")
    bridge = ctx["bridge"]
    if bridge.empty or ("link_confidence" in bridge and (bridge["link_confidence"] == "No Match").all()):
        st.info("🟡 **No SAM.gov opportunity signal in this dataset.** Recompete windows are estimated from award "
                "expiration dates only; run the pipeline with a SAM.gov key to light up the notice lane.")

with tab_early_warning:
    # E1 — early-warning lane: Sources Sought / RFI notices ALREADY in fact_opportunity_notices
    # (no live SAM.gov call, no new ingest). Presolicitation is deliberately excluded — it is
    # later in the shaping window; this lane's capture value is catching requirements while
    # they are still shapeable.
    st.subheader("Early warning — Sources Sought / RFI")
    st.caption(EARLY_WARNING_STALENESS_NOTE)
    early_warning = early_warning_notices(ctx["notices"])
    if early_warning.empty:
        st.info(
            "No Sources Sought / RFI notices in the current snapshot. This is an honest empty "
            "lane, not an error — refresh the SAM bulk export "
            "(`SAM.gov data/ContractOpportunitiesFullCSV.csv`) per **docs/data_acquisition_plan.md** "
            "and re-run the pipeline to populate it."
        )
    else:
        st.caption(f"{len(early_warning):,} Sources Sought / RFI notice(s), most recent first.")
        for _, notice in early_warning.iterrows():
            title = html.escape(str(notice.get("title") or "").strip() or "[Untitled notice]")
            posted_ts = pd.to_datetime(notice.get("posted_date"), errors="coerce")
            posted_str = posted_ts.date().isoformat() if pd.notna(posted_ts) else "date unknown"
            url = notice.get("source_url")
            # Scheme allowlist (defense-in-depth, security review 2026-07-13): html.escape
            # stops anchor breakout but not a javascript:/data: scheme. Every real SAM
            # source_url is https, so a non-http(s) value renders as text, never a link.
            if isinstance(url, str) and url.strip().lower().startswith(("https://", "http://")):
                safe_url = html.escape(url.strip())
                link_html = f'<a href="{safe_url}" target="_blank" rel="noopener">{safe_url}</a>'
            elif isinstance(url, str) and url.strip():
                link_html = html.escape(url.strip())  # shown, but never an href
            else:
                link_html = "no direct link"  # never fabricate a URL
            st.markdown(f"**{title}** &nbsp;·&nbsp; {posted_str} &nbsp;·&nbsp; {link_html}",
                       unsafe_allow_html=True)
