"""
shell.py — the "Intelligence Desk" app shell: one Google-Fonts + CSS injection,
the RECOMPETE RADAR header band, and the render helpers (KPI cards, T-minus
countdown chips, runway bars) that make the app read as a product rather than a
default Streamlit template. Pure presentation — imports only theme (no data), so
data.py can call render_header without a circular import.
"""

import html
import re

import streamlit as st

from components import theme

# CSS is a literal string (hex values inlined) to avoid f-string brace escaping.
# Colors kept in sync with theme.py: RADAR_INK #0E1B26, REDORANGE #E4572E,
# AMBER #F2A900, STEEL #3E7CB1, CHARCOAL #2A2D34, GRAY #8A8D91, border #E7EBEF.
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&family=Public+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

/* ---- global type ---- */
html, body, [data-testid="stAppViewContainer"], [data-testid="stMarkdownContainer"], .stApp {
    font-family: 'Public Sans', 'Segoe UI', system-ui, sans-serif;
    color: #2A2D34;
}
h1, h2, h3, .rr-hero, .rr-title, .kpi-value, .rr-wordmark {
    font-family: 'Archivo', 'Public Sans', sans-serif; letter-spacing: -0.01em;
}
[data-testid="stAppViewContainer"] { background: #F4F6F8; }
[data-testid="stHeader"] { background: transparent; }
#MainMenu, footer, [data-testid="stDecoration"] { visibility: hidden; }
.block-container { padding-top: 1.1rem; max-width: 1280px; }

/* ---- header band ---- */
.rr-header {
    background: #0E1B26; color: #F4F6F8; border-radius: 12px;
    padding: 13px 22px; margin: 0 0 14px 0;
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
}
.rr-wordmark { font-weight: 800; letter-spacing: 0.16em; font-size: 15px; color: #FFFFFF; }
.rr-wordmark .dot { color: #E4572E; }
.rr-wordmark .tag { color: #8FA6B6; font-weight: 600; letter-spacing: 0.14em; font-size: 11px; margin-left: 10px; }
.rr-meta { display: flex; align-items: center; gap: 14px; font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: #B9C6D1; }
.rr-badge { padding: 3px 10px; border-radius: 20px; font-weight: 600; font-size: 11px; letter-spacing: 0.04em; }
.rr-badge.live { background: rgba(46,139,118,0.22); color: #7FE3C7; }
.rr-badge.sample { background: rgba(242,169,0,0.20); color: #F2C879; }
.rr-title { font-size: 22px; font-weight: 700; margin: 4px 0 2px 0; color: #2A2D34; }
.rr-sub { color: #8A8D91; font-size: 13.5px; margin-bottom: 6px; }
.rr-disclaimer { color: #8A8D91; font-size: 12px; border-left: 3px solid #E7EBEF; padding: 4px 0 4px 10px; margin: 6px 0 2px 0; }

/* ---- hero (home) ---- */
.rr-hero { font-size: 27px; font-weight: 700; line-height: 1.22; color: #14212B; }
.rr-hero .hi { color: #E4572E; }
.rr-subcopy { color: #64707A; font-size: 14px; margin-top: 8px; max-width: 720px; }

/* ---- KPI cards ---- */
.kpi-card { background: #FFFFFF; border: 1px solid #E7EBEF; border-left-width: 4px; border-radius: 10px; padding: 13px 16px 14px 16px; height: 100%; }
.kpi-label { font-size: 10.5px; font-weight: 700; letter-spacing: 0.09em; text-transform: uppercase; color: #8A8D91; }
.kpi-value { font-weight: 700; font-size: 29px; line-height: 1.12; color: #14212B; margin-top: 5px; }
.kpi-sub { font-family: 'IBM Plex Mono', monospace; font-size: 11.5px; color: #64707A; margin-top: 5px; }
.kpi-sub .up { color: #2E8B76; } .kpi-sub .down { color: #E4572E; }

/* ---- chips + runway ---- */
.chip { font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 600; padding: 2px 9px; border-radius: 20px; white-space: nowrap; }
.chip-red { background: rgba(228,87,46,0.14); color: #C6431F; }
.chip-amber { background: rgba(242,169,0,0.18); color: #8A6100; }
.chip-steel { background: rgba(62,124,177,0.14); color: #2C5E86; }
.chip-muted { background: #EEF1F4; color: #8A8D91; }
.runway-track { position: relative; height: 9px; background: #EEF1F4; border-radius: 6px; overflow: hidden; margin-top: 3px; }
.runway-fill { position: absolute; left: 0; top: 0; height: 100%; border-radius: 6px 0 0 6px; }
.runway-est { position: absolute; top: 0; height: 100%; opacity: 0.55;
    background-image: repeating-linear-gradient(45deg, rgba(20,33,43,0.22) 0 4px, transparent 4px 8px); }

/* ---- "On the Radar" list rows ---- */
.radar-row { display: flex; align-items: center; justify-content: space-between; gap: 10px;
    background: #FFFFFF; border: 1px solid #E7EBEF; border-radius: 9px; padding: 9px 12px; margin-bottom: 8px; }
.radar-row .t { font-weight: 600; font-size: 13px; color: #14212B; }
.radar-row .m { font-family: 'IBM Plex Mono', monospace; font-size: 11.5px; color: #64707A; }
.radar-val { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 13px; color: #1B435D; }

/* ---- sidebar as control panel ---- */
[data-testid="stSidebar"] { background: #EEF2F5; border-right: 1px solid #E7EBEF; }
[data-testid="stSidebar"] h2 { font-family: 'Archivo', sans-serif; font-size: 13px; letter-spacing: 0.1em; text-transform: uppercase; color: #1B435D; }

/* ---- filter chips ---- */
.filter-chip { display: inline-block; font-family: 'IBM Plex Mono', monospace; font-size: 11px; background: #FFFFFF;
    border: 1px solid #D6DEE5; color: #2C5E86; border-radius: 16px; padding: 2px 10px; margin: 0 6px 6px 0; }
</style>
"""


def inject_css():
    """Inject the shell CSS once per run (idempotent)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_header(ctx: dict, title: str = None, subtitle: str = None, disclaimer: str = None):
    """The canonical page header: CSS + the RADAR_INK band (wordmark + data badge +
    as-of), then an optional page title/subtitle and the estimates-vs-facts note."""
    inject_css()
    from components.data import snapshot_age_days  # lazy: data.py imports this module

    live = ctx.get("mode") == "live"
    badge_cls = "live" if live else "sample"
    # "LIVE DATA" overstated a static snapshot. This is a periodically-refreshed
    # public-data snapshot (runway recomputed to today), not a live feed.
    badge_txt = "PUBLIC DATA SNAPSHOT" if live else "SAMPLE DATA"
    # Freshness in the band itself: the as-of date plus its age in days (both live and
    # sample modes — a sample bundle ages too). Age omitted when unknown, never guessed.
    as_of = ctx.get("as_of", "unknown")
    age = snapshot_age_days(as_of)
    as_of_txt = f"as of {as_of} · {age}d old" if age is not None else f"as of {as_of}"
    # "Scoring as" badge — green when a real company profile is set, amber for demo.
    prof = ctx.get("profile") or {}
    custom = ctx.get("profile_custom")
    name = (prof.get("company_name") or "Your company").strip()
    if len(name) > 26:
        name = name[:24] + "…"
    score_cls = "live" if custom else "sample"
    # Escape — company_name is user/URL-supplied (?p= param) and rendered as raw HTML.
    score_txt = f"SCORING: {html.escape(name.upper())}" if custom else "DEMO PROFILE"
    st.markdown(
        f"""
        <div class="rr-header">
          <div class="rr-wordmark">RECOMPETE<span class="dot">·</span>RADAR<span class="tag">DoD CYBER / IT PIPELINE</span></div>
          <div class="rr-meta">
            <span class="rr-badge {score_cls}">{score_txt}</span>
            <span class="rr-badge {badge_cls}">{badge_txt}</span>
            <span>{as_of_txt}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    # Freshness banner: a snapshot older than 45 days gets a soft amber notice so no
    # one mistakes recomputed-to-today runway for newly-ingested awards.
    if live:
        if age is not None and age > 45:
            st.warning(
                f"Snapshot is {age} days old — runway figures are recomputed to today, "
                "but awards issued since the snapshot are not yet included.",
                icon="⏳",
            )
    if title:
        st.markdown(f'<div class="rr-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="rr-sub">{subtitle}</div>', unsafe_allow_html=True)
    if disclaimer:
        # Render markdown **bold** as <b> inside the styled div (disclaimer is an
        # app constant — safe; user data is never passed here).
        disc_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", disclaimer)
        st.markdown(f'<div class="rr-disclaimer">{disc_html}</div>', unsafe_allow_html=True)


def sam_gov_search_url(piid=None, title=None) -> str:
    """A SAM.gov search deep-link to verify a candidate's current status. Prefers the
    PIID (precise); falls back to the cleaned title. Verified externally by the user —
    the app never asserts an award is or isn't re-competed."""
    import urllib.parse
    term = ""
    if piid and str(piid).strip() and str(piid).lower() != "nan":
        term = str(piid).strip()
    elif title and str(title).strip():
        term = str(title).strip()[:80]
    return "https://sam.gov/search/?keywords=" + urllib.parse.quote(term) if term else "https://sam.gov/search/"


def needs_verification_strip(candidates, link_target: str = "views/explorer.py"):
    """Collapsed 'Needs verification' strip: the Data Gap records — expired >90 days,
    garbled, or missing an end date — that are quarantined OUT of every headline KPI,
    chart, tier board, and default export, surfaced honestly with a count, value, and
    per-row Verify-on-SAM.gov links. `candidates` should be the FULL (pre-exclusion) set."""
    if candidates is None or candidates.empty or "priority_tier" not in candidates.columns:
        return
    gap = candidates[candidates["priority_tier"] == "Data Gap"]
    if gap.empty:
        return
    val = theme.usd_short(gap["total_obligated_amount"].sum())
    sort_col = "days_until_expiration" if "days_until_expiration" in gap.columns else "candidate_id"
    with st.expander(f"⚠️ Needs verification — {len(gap):,} quarantined records ({val}) held out of the pipeline", expanded=False):
        st.caption(
            "These are **Data Gap** records — expired more than 90 days ago, or with a garbled/"
            "missing title or end date. Given this pipeline's lookback-window coverage gap the expired "
            "ones are almost certainly already re-awarded, so **none of these are counted** in any "
            "headline number, chart, tier board, or default export. Verify current status on SAM.gov "
            "before pursuing."
        )
        for _, r in gap.sort_values(sort_col).head(15).iterrows():
            title = str(r.get("title_display") or r.get("candidate_id", ""))
            url = sam_gov_search_url(r.get("piid"), r.get("title_display"))
            exp = html.escape(str(r.get("selected_expiration_date", "—")))  # escape data-sourced string
            st.markdown(
                f'- {html.escape(title[:70])} · <span style="color:{theme.GRAY}">exp {exp}</span> · '
                f'[Verify on SAM.gov ↗]({url})', unsafe_allow_html=True)
        if len(gap) > 15:
            st.caption(f"…and {len(gap) - 15:,} more — open the Explorer and add “Needs verification” to the Status filter.")


def kpi_card(label: str, value: str, accent: str = theme.STEEL, sub: str = "") -> str:
    """HTML for one KPI card with a semantic left accent bar. Place inside a column:
    col.markdown(kpi_card(...), unsafe_allow_html=True). label/value/sub are ESCAPED
    (security review 2026-07-13: this was the one raw-HTML sink without escaping — today's
    callers pass constants, but a future data-derived KPI must not become an XSS)."""
    sub_html = f'<div class="kpi-sub">{html.escape(str(sub))}</div>' if sub else ""
    return (
        f'<div class="kpi-card" style="border-left-color:{accent}">'
        f'<div class="kpi-label">{html.escape(str(label))}</div>'
        f'<div class="kpi-value">{html.escape(str(value))}</div>{sub_html}</div>'
    )


def kpi_row(cards: list):
    """Render a row of KPI cards. `cards` = list of dicts(label, value, accent, sub)."""
    cols = st.columns(len(cards))
    for col, c in zip(cols, cards):
        col.markdown(
            kpi_card(c["label"], c["value"], c.get("accent", theme.STEEL), c.get("sub", "")),
            unsafe_allow_html=True,
        )


def _tier_color(months) -> str:
    if months is None or months != months:
        return theme.GRAY
    if months <= 6:
        return theme.REDORANGE
    if months <= 12:
        return theme.AMBER
    return theme.STEEL


def runway_chip(months) -> str:
    """Monospace countdown chip: 14 -> 'T–14 MO'. Negative months = expired (T+)."""
    if months is None or months != months:
        return '<span class="chip chip-muted">—</span>'
    m = int(round(months))
    if m < 0:
        return f'<span class="chip chip-red">EXPIRED {abs(m)}MO</span>'
    cls = "chip-red" if m <= 6 else "chip-amber" if m <= 12 else "chip-steel"
    return f'<span class="chip {cls}">T–{m} MO</span>'


def reason_chip(chip) -> str:
    """One reason-code chip as HTML. `chip` is a scoring.reason_codes.ReasonChip. The engine returns
    RAW text; ALL data tokens reach HTML only through html.escape here (the render-layer XSS boundary)."""
    cls = theme.BASIS_CHIP["critical"] if getattr(chip, "critical", False) else theme.BASIS_CHIP.get(chip.basis, "chip-muted")
    return f'<span class="chip {cls}">{chip.glyph} {html.escape(chip.text)}</span>'


def reason_chip_row(chips) -> str:
    """A space-joined row of reason chips, or a muted em-dash when there are none to claim."""
    return " ".join(reason_chip(c) for c in chips) or '<span class="chip chip-muted">—</span>'


def radar_row(title, agency, months, value) -> str:
    """HTML for one "On the radar" list row: title + agency on the left, a runway
    chip + estimated value on the right. title/agency are data-supplied, so both
    are html.escape'd before landing in raw markup."""
    chip = runway_chip(months)
    ts = str(title)
    t = (ts[:46] + "…") if len(ts) > 46 else ts
    t = html.escape(t)
    a = html.escape(str(agency))
    return (
        '<div class="radar-row"><div>'
        f'<div class="t">{t}</div><div class="m">{a}</div></div>'
        f'<div style="text-align:right">{chip}<div class="radar-val">{theme.usd_short(value)}</div></div>'
        "</div>"
    )


def runway_bar(months_to_exp, est_window_months: float = 6.0, max_months: float = 24.0) -> str:
    """Runway bar: solid segment (today -> expiration, tinted by tier) flowing into a
    hatched band for the ESTIMATED recompete window — the facts-vs-estimates ethic
    drawn as a mark."""
    if months_to_exp is None or months_to_exp != months_to_exp:
        return '<div class="runway-track"></div>'
    m = max(0.0, float(months_to_exp))
    color = _tier_color(months_to_exp)
    solid_pct = min(m / max_months, 1.0) * 100
    est_pct = min(est_window_months / max_months, max(0.0, 1.0 - m / max_months)) * 100
    return (
        '<div class="runway-track">'
        f'<div class="runway-fill" style="width:{solid_pct:.1f}%;background:{color}"></div>'
        f'<div class="runway-est" style="left:{solid_pct:.1f}%;width:{est_pct:.1f}%"></div>'
        "</div>"
    )
