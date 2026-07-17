"""
render — the deterministic 8-section capture brief (pure templates, strict).

Two renderers over one section builder: ``render_brief_html`` (self-contained print-ready
HTML — system font stack, inline CSS on the old brief's palette tokens, NO external
@import) and ``render_brief_text`` (the digest-body form: 8 headlines, one sentence each).

Determinism is the contract: no clock, no I/O, no config, no randomness — the same
``BriefEvidence`` renders byte-identical output forever. Sections render their refusal
copy when a basis refuses; a missing value renders an em dash, never ``nan``. Every
sentence passes ``html.escape`` on the HTML path (hostile titles/vendors exit inert).
"""

from __future__ import annotations

import html
import math

from briefing.evidence import BriefEvidence
from scoring.eligibility_lane import BLANK_NOT_NONE
from scoring.reason_codes import BASIS_LABELS

# Fixed copy (verbatim contract; tests pin these strings).
SIGNALS_FOOTER = "● fact · ◐ estimate · ○ not reported — we don't guess."
OFFICE_NOTE = "Descriptive aggregates of observed buying — no prediction."
PTW_REFUSAL = "Fewer than the minimum comparable awards exist — we won't invent a range."
PTW_NOTE = (
    "Not a bid prediction — competitor bids are never public. Estimate; not FAR 15.4 certified cost or pricing data."
)
NO_PROFILE_LINE = (
    "No company profile active — eligibility not assessed. "
    "Enter your company (with attested certifications) in the app."
)
CANT_KNOW_LINE = (
    "CPARS past-performance ratings, competitor pricing, and the agency's internal "
    "acquisition intent are not public — this brief never claims them."
)
ESTIMATE_FOOTER = (
    "ESTIMATE — pursuit signals, windows, ranges, and eligibility reads are analytical "
    "estimates, not government predictions. Identifiers, dollars, and dates are facts "
    "from USAspending.gov and SAM.gov."
)
GRACE_CAUTION = "Recently expired (≤90 days) — verify current status on SAM.gov before pursuing."
ATTESTED_NOTE_BRIEF = (
    "Based on the certifications you attested in your profile — the radar never verifies certifications."
)


def _missing(v: object) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _s(v: object) -> str:
    """NaN/None-safe display string ('' when missing — callers decide the dash)."""
    if _missing(v):
        return ""
    s = str(v).strip()
    return "" if s in ("nan", "NaN", "NaT", "<NA>", "None") else s


def _f(v: object) -> float | None:
    if _missing(v):
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _i(v: object) -> int | None:
    f = _f(v)
    return None if f is None else int(round(f))


def _usd(x: float) -> str:
    """Compact USD (deliver._usd_compact pattern — local by design; no app-theme import)."""
    ax = abs(x)
    if ax >= 1e9:
        return f"${x / 1e9:.1f}B"
    if ax >= 1e6:
        return f"${x / 1e6:.1f}M"
    if ax >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:.0f}"


def _dash(v: object) -> str:
    return _s(v) or "—"


def _why_now(ev: BriefEvidence) -> list[str]:
    c = ev.candidate
    lines: list[str] = []
    exp = _dash(c.get("selected_expiration_date"))
    basis = _s(c.get("expiration_date_basis")) or "not reported"
    days = _i(c.get("days_until_expiration"))
    if days is None:
        lines.append(f"Expires {exp} (basis: {basis}).")
    elif days >= 0:
        lines.append(f"Expires {exp} — {days} days of runway (basis: {basis}).")
    else:
        lines.append(f"Expired {abs(days)} days ago — end date {exp} (basis: {basis}).")
    start, end = _s(c.get("estimated_recompete_window_start")), _s(c.get("estimated_recompete_window_end"))
    if start and end:
        lines.append(f"Estimated recompete window: {start} → {end}.")
    phase = _s(c.get("capture_phase"))
    if phase:
        lines.append(f"Capture phase: {phase}.")
    cr, bp = _f(c.get("ceiling_burn_ratio")), _f(c.get("burn_pressure"))
    if _s(c.get("burn_basis")) == "measured" and cr is not None and bp is not None:
        band = _s(c.get("burn_band")).replace("_", " ") or "—"
        lines.append(f"Obligation pace: {cr:.0%} of ceiling obligated vs {cr - bp:.0%} of period elapsed — {band}.")
    if _s(c.get("candidate_status")) == "expired_grace":
        lines.append(GRACE_CAUTION)
    return lines


def _signals(ev: BriefEvidence) -> list[str]:
    lines = [
        f"{chip.glyph} {chip.text} — {chip.evidence} ({BASIS_LABELS.get(chip.basis, chip.basis)})." for chip in ev.chips
    ]
    if not lines:
        lines.append("No signal chips available for this candidate.")
    lines.append(SIGNALS_FOOTER)
    return lines


def _who_holds_it(ev: BriefEvidence) -> list[str]:
    c = ev.candidate
    lines: list[str] = []
    vendor, uei = _dash(c.get("incumbent_vendor")), _s(c.get("incumbent_uei"))
    lines.append(f"Incumbent: {vendor}" + (f" (UEI {uei})." if uei else "."))
    start, end = _s(c.get("pop_start_date")), _s(c.get("current_end_date"))
    if start and end:
        lines.append(f"On this award since {start}; current period ends {end}.")
    if _i(c.get("number_of_offers_received")) == 1:
        lines.append("One offer received at the last competition — the incumbent went unchallenged.")
    v = ev.vendor or {}
    if str(v.get("size_standard_shift", "")).strip().lower() == "true" or v.get("size_standard_shift") is True:
        basis = _s(v.get("size_standard_basis")) or "basis not reported"
        lines.append(
            f"Size-standard shift flagged — {basis}. Directional only; verify size status at the solicitation."
        )
    return lines


def _the_office(ev: BriefEvidence) -> list[str]:
    o = ev.office
    if not o:
        return ["No office aggregates available for this component.", OFFICE_NOTE]
    lines: list[str] = []
    name = _dash(o.get("subagency"))
    n, total = _i(o.get("number_of_contracts")), _f(o.get("total_cyber_it_obligations"))
    if n is not None and total is not None:
        lines.append(f"{name}: {n} tracked cyber/IT contracts, {_usd(total)} total obligations.")
    exp_n, exp_val = _i(o.get("expiring_contract_count_12_months")), _f(o.get("expiring_pipeline_value"))
    avg = _f(o.get("average_award_size"))
    if exp_n is not None and exp_val is not None:
        tail = f"; average award {_usd(avg)}" if avg is not None else ""
        lines.append(f"{exp_n} contracts expiring within 12 months ({_usd(exp_val)} pipeline){tail}.")
    if not lines:
        lines.append(f"{name}: aggregates not available.")
    # F4 — the component's incumbent concentration (baked dim_agency join; double-gated).
    # Presence-gated: an older bundle without the columns renders neither line. The
    # insufficient branch names the module's own refusal reason — Unknown, never imputed.
    c_basis = _s(o.get("concentration_basis"))
    c_share, c_n = _f(o.get("concentration_top_share")), _i(o.get("concentration_n_ueis"))
    if c_basis == "observed" and c_share is not None and c_n is not None:
        # "attributed (UEI-known)": top_share's denominator is the ATTRIBUTED slice of the
        # reportable pool (market_concentration._assess_market divides by attributed_net,
        # not market_net — up to max_unknown_uei_share of dollars carry no incumbent UEI).
        lines.append(
            f"Incumbent concentration: the top incumbent holds {c_share:.0%} of the component's "
            f"attributed (UEI-known) expiring obligated dollars, across {c_n} incumbents — a "
            "dollar-share of this recompete set, not market share or market power."
        )
    elif c_basis == "insufficient":
        reason = _s(o.get("concentration_reason")) or "insufficient data"
        lines.append(f"Incumbent concentration: not assessable — {reason}. Unknown, never an imputed number.")
    lines.append(OFFICE_NOTE)
    return lines


def _price_range(ev: BriefEvidence) -> list[str]:
    c = ev.candidate
    lines: list[str] = []
    lo, mid, hi = _f(c.get("ptw_low")), _f(c.get("ptw_market_median")), _f(c.get("ptw_high"))
    if _s(c.get("ptw_basis")) == "comparables" and lo is not None and mid is not None and hi is not None:
        n = _i(c.get("ptw_n_comparables"))
        strength = _s(c.get("ptw_data_strength")) or "not reported"
        n_txt = f"{n} comparables" if n is not None else "comparables"
        lines.append(
            f"Comparable historical winning awards ran {_usd(lo)}–{_usd(hi)}/yr "
            f"(market median {_usd(mid)}), from {n_txt} — data strength: {strength}."
        )
        rr = _f(c.get("ptw_incumbent_runrate"))
        if rr is not None:
            lines.append(f"Incumbent's own run-rate: {_usd(rr)}/yr — shown separately, never blended into the range.")
    else:
        lines.append(PTW_REFUSAL)
    lines.append(PTW_NOTE)
    return lines


def _eligibility(ev: BriefEvidence) -> list[str]:
    if ev.lane is None:
        return [NO_PROFILE_LINE]
    lane = ev.lane
    lines = [f"[{lane.state.upper()}] {lane.headline} — {lane.detail}"]
    if lane.teaming:
        lines.append(lane.teaming)
    lines.append(ATTESTED_NOTE_BRIEF)
    return lines


def _cant_know(ev: BriefEvidence) -> list[str]:
    lines = [CANT_KNOW_LINE]
    if not _s(ev.candidate.get("type_of_set_aside_code")):
        lines.append(BLANK_NOT_NONE)
    return lines


def _sources(ev: BriefEvidence) -> list[str]:
    lines: list[str] = []
    url = _s(ev.candidate.get("source_url"))
    if url:
        lines.append(f"Award record: {url}")
    for n in ev.notices:
        sol = _dash(n.get("solicitation_number"))
        n_url = _s(n.get("source_url")) or "no public URL"
        conf = _s(n.get("link_confidence")) or "unknown"
        lines.append(f"{sol}: {n_url} ({conf} confidence match)")
    lines.append(f"Data snapshot {ev.as_of}; DoD FPDS reporting lags ~90 days — recent actions may be missing.")
    lines.append(ESTIMATE_FOOTER)
    return lines


def _sections(ev: BriefEvidence) -> tuple[tuple[str, list[str]], ...]:
    """The eight sections as (headline, raw-text lines) — ONE builder for both renderers."""
    return (
        ("WHY NOW", _why_now(ev)),
        ("THE SIGNALS", _signals(ev)),
        ("WHO HOLDS IT", _who_holds_it(ev)),
        ("THE OFFICE", _the_office(ev)),
        ("PRICE RANGE", _price_range(ev)),
        ("ELIGIBILITY", _eligibility(ev)),
        ("WHAT WE CAN'T KNOW", _cant_know(ev)),
        ("SOURCES", _sources(ev)),
    )


# Old brief's palette tokens (band ink / accent / steel / body ink / muted / rule) with a
# system font stack — the Google-Fonts @import is deliberately gone (deterministic artifact).
_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       color: #2A2D34; margin: 0; padding: 32px 40px; }
.band { background: #0E1B26; color: #fff; padding: 10px 16px; border-radius: 8px;
        display: flex; justify-content: space-between; align-items: center; }
.wm { font-weight: 700; letter-spacing: .16em; font-size: 13px; }
.wm .d { color: #E4572E; }
.mono { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 11px; }
h1 { font-size: 22px; margin: 18px 0 4px; }
.sub { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
       font-size: 12px; color: #64707A; }
.fit { font-size: 13px; color: #1B435D; font-weight: 600; margin: 6px 0 0; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em;
     color: #1B435D; margin: 18px 0 4px; border-bottom: 1px solid #E7EBEF; padding-bottom: 3px; }
p { font-size: 13px; margin: 4px 0; line-height: 1.45; }
.foot { font-size: 11px; color: #8A8D91; border-top: 1px solid #E7EBEF;
        padding-top: 8px; margin-top: 18px; }
@media print { body { padding: 0; } }
"""


def render_brief_html(ev: BriefEvidence) -> str:
    """Self-contained, print-ready HTML. Every interpolated value is escaped."""
    c = ev.candidate
    title = _dash(c.get("title_display"))
    sub_bits = [_dash(c.get("subagency")), f"Incumbent: {_dash(c.get('incumbent_vendor'))}"]
    days = _i(c.get("days_until_expiration"))
    if days is not None:
        sub_bits.append(f"Runway: {days} days" if days >= 0 else "Runway: expired")
    fit_line = ""
    if ev.profile_label:
        score = _f(c.get("pursuit_score"))
        score_txt = f"{score:.0f}" if score is not None else "—"
        fit_line = (
            f'<p class="fit">Pursuit fit ({html.escape(ev.profile_label)}): '
            f"{html.escape(score_txt)} · {html.escape(_dash(c.get('priority_tier')))}</p>"
        )
    body: list[str] = []
    for headline, lines in _sections(ev):
        body.append(f"<h2>{html.escape(headline)}</h2>")
        body.extend(f"<p>{html.escape(line)}</p>" for line in lines)
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>Capture Brief — {html.escape(title)}</title>"
        f"<style>{_CSS}</style></head><body>"
        '<div class="band"><span class="wm">RECOMPETE<span class="d">·</span>RADAR</span>'
        f'<span class="mono">Capture Brief · {html.escape(ev.as_of)}</span></div>'
        f"<h1>{html.escape(title)}</h1>"
        f'<div class="sub">{html.escape(" · ".join(sub_bits))}</div>'
        f"{fit_line}"
        f"{''.join(body)}"
        f'<div class="foot">Generated by Recompete Radar · data as of {html.escape(ev.as_of)}.</div>'
        "</body></html>"
    )


def render_brief_text(ev: BriefEvidence) -> str:
    """The digest-body form: 8 section headlines, one sentence each (each section's lead
    line — refusals lead their sections, so a refusing section contributes its refusal)."""
    c = ev.candidate
    out = [f"CAPTURE BRIEF — {_dash(c.get('title_display'))} (data as of {ev.as_of})"]
    for headline, lines in _sections(ev):
        out.append(f"{headline}: {lines[0]}")
    return "\n".join(out)
