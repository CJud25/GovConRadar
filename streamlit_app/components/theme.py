"""
theme.py — one place for the app's palette and Plotly styling, kept in sync with
the Power BI theme (powerbi/theme/govcon_theme.json) and the dim_priority_tier /
dim_capture_phase hex colors so both surfaces read as one product.
"""

import plotly.graph_objects as go
import plotly.io as pio

from scoring.quality_flags import BUCKET_ORDER

# Executive palette (mirrors govcon_theme.json dataColors).
CHARCOAL = "#2A2D34"
NAVY = "#1B435D"
STEEL = "#3E7CB1"
TEAL = "#2E8B76"
AMBER = "#F2A900"
REDORANGE = "#E4572E"
GRAY = "#8A8D91"
LIGHT_BG = "#F4F6F8"
GRID = "#E7EBEF"
# "Intelligence Desk" product tokens (shell/identity only — never repaint data).
RADAR_INK = "#0E1B26"   # header band + dark base; deepened NAVY, reads "night ops"
CARD_BORDER = "#E7EBEF"

# Typefaces (loaded via Google Fonts in shell.py; referenced by Plotly + CSS).
FONT_BODY = "Public Sans"      # USWDS government UI face
FONT_DISPLAY = "Archivo"       # titles, KPI numerals, wordmark
FONT_MONO = "IBM Plex Mono"    # all data: PIIDs, dates, dollars, scores, countdowns

CATEGORICAL = [STEEL, REDORANGE, AMBER, TEAL, "#6C8EBF", "#B23A48", GRAY, "#5C6B73", "#A44200", NAVY]

# Must match dim_priority_tier[tier_hex_color] / dim_capture_phase[phase_hex_color].
TIER_COLORS = {
    "Tier 1: Pursue Now": REDORANGE,
    "Tier 2: Capture Research": AMBER,
    "Tier 3: Monitor": STEEL,
    "Tier 4: Low Priority": GRAY,
    "Data Gap": "#C9CBCF",
}
PHASE_COLORS = {
    "Early Watch": "#6C8EBF", "Pre-RFP Shaping": STEEL, "Capture Planning": TEAL,
    "Proposal Prep": AMBER, "Proposal / Submit": REDORANGE, "Expired": GRAY,
    "Unknown / Data Gap": "#C9CBCF",
}
DATA_GAP_GRAY = "#C9CBCF"  # matches dim_priority_tier "Data Gap" hex

# v2 runway buckets. "Expired — verify" is the quarantine bucket (Data-Gap gray,
# visually separated first); it is NEVER blended into forward-looking totals.
# BUCKET_ORDER is imported above from scoring.quality_flags (the one copy).
EXPIRED_BUCKET = BUCKET_ORDER[0]
FORWARD_BUCKETS = BUCKET_ORDER[1:]  # forward-looking windows only (exclude expired)
# Keyed positionally off the imported BUCKET_ORDER so a bucket rename in
# quality_flags can't silently orphan a color.
BUCKET_COLORS = dict(zip(BUCKET_ORDER, [DATA_GAP_GRAY, REDORANGE, AMBER, STEEL, TEAL, "#6C8EBF"]))

# ─── OBLIGATION PACE (baked burn_* columns) — presentation only ────────────────
# Descriptive obligation-vs-PoP pace (Corrections C1.1): NOT spend, NOT a recompete
# forecast. Direction is carried by the ▲/▼ glyph + the signed diverging bar, so color is
# deliberately NEUTRAL (no alarm red) — a single STEEL/GRAY pair reused from the palette.
# Internal enum keys (burning_hot/on_pace/underutilized/not_applicable) are byte-stable.
BURN_LABELS = {
    "burning_hot": "Obligated ahead of pace", "on_pace": "On pace",
    "underutilized": "Obligated behind pace", "not_applicable": "Not measurable",
}
BURN_GLYPHS = {"burning_hot": "▲", "on_pace": "•", "underutilized": "▼", "not_applicable": "—"}
BURN_COLORS = {"burning_hot": STEEL, "on_pace": GRAY, "underutilized": STEEL}
BURN_CHIP = {
    "burning_hot": "chip-steel", "on_pace": "chip-muted",
    "underutilized": "chip-steel", "not_applicable": "chip-muted",
}

# ─── REASON-CODES basis chips — presentation only (reuses existing shell.py chip classes) ───
# Maps a chip's honesty basis to a pre-existing chip class (no CSS added). The ●◐○ glyph carries
# the basis even if two classes ever shared a color, so this is redundant reinforcement. `critical`
# (a data-gap caveat) overrides to chip-red. Missing key falls back to muted at the call site.
BASIS_CHIP = {"observed": "chip-steel", "inferred": "chip-amber", "missing": "chip-muted", "critical": "chip-red"}

# Register a shared Plotly template so every chart inherits the look.
_TEMPLATE = go.layout.Template(
    layout=dict(
        font=dict(family=f"{FONT_BODY}, Segoe UI, Arial, sans-serif", color=CHARCOAL, size=13),
        paper_bgcolor="white",
        plot_bgcolor="white",
        colorway=CATEGORICAL,
        title=dict(font=dict(family=f"{FONT_DISPLAY}, {FONT_BODY}, sans-serif", size=16, color=CHARCOAL)),
        xaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID),
        yaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID),
        margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(font=dict(family=f"{FONT_MONO}, monospace", size=12)),
    )
)
pio.templates["govcon"] = _TEMPLATE


def style(fig, height=360, title=None):
    """Apply the shared template + common layout to a figure."""
    fig.update_layout(template="govcon", height=height)
    if title is not None:
        fig.update_layout(title=title)
    return fig


# ─── Number formatting ────────────────────────────────────────────────────────
# One place that turns raw dollars into $1.2B / $340M / $45K — used in KPI cards,
# hovertemplates (via customdata), and table columns so nothing shows raw floats.
def usd_short(value) -> str:
    """Compact currency: 1_230_000_000 -> '$1.2B'. Returns '—' for null/NaN."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v != v:  # NaN
        return "—"
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e9:
        return f"{sign}${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{sign}${v / 1e3:.0f}K"
    return f"{sign}${v:.0f}"


def money_axis(fig, axis: str = "x"):
    """Compact $ SI ticks on a value axis (e.g. $1.2G). Hovers use usd_short for
    exact B/M/K wording; axes stay short."""
    upd = {f"{axis}axis": dict(tickprefix="$", tickformat="~s")}
    fig.update_layout(**upd)
    return fig
