"""
eligibility_lane (app adapter) — Streamlit-side glue for the strict prime-path lane.

Mirrors the components/reason_codes.py adapter pattern: this is the ONLY app module that
imports the strict lane (scoring.eligibility_lane). It owns the three edges the engine must
never see — the attested profile → EntityCerts translation, the live linked-notice lookup
(bridge ⋈ notices, column-guarded for older bundles), and the chip render boundary where
every token is html.escape'd. App glue (NOT in the mypy `files` list / FMT_PATHS).

The hard gate stays certain-only: the notice path fires only on a High-confidence link whose
response window is still open AND whose notice reports a non-empty set-aside code. Anything
less falls back to the historical path, which only ever warns.
"""

from __future__ import annotations

import html
from datetime import date
from typing import Mapping

import pandas as pd

from api_clients.sam_entity import EntityCerts
from components.data import CERT_TOKENS, get_profile, notice_response_days
from scoring.eligibility_lane import ATTESTED_NOTE, LANE_STATES, LaneVerdict, lane_verdict
from utils.coerce import nan_str

__all__ = [
    "ATTESTED_NOTE",  # re-exported: views consume the lane ONLY through this adapter
    "entity_from_profile",
    "lane_chip_html",
    "lane_counts",
    "lane_for",
    "live_notice_code",
]

# Lane state -> existing shell.py chip CSS (no new CSS).
_CHIP_CLASS = {"gate": "chip-red", "warn": "chip-amber", "unknown": "chip-muted", "clear": "chip-steel"}


def entity_from_profile(profile: Mapping[str, object]) -> EntityCerts | None:
    """The attested profile as gate input. None for the demo or a blank profile — no
    attestation exists, so the lane must say "unknown", never guess. Certifications are
    self-attested (uei sentinel says so) and never verified."""
    if not profile or bool(profile.get("is_demo")) or not nan_str(profile.get("company_name")):
        return None
    raw = profile.get("certs")
    certs = {str(c) for c in raw if str(c) in CERT_TOKENS} if isinstance(raw, list) else set()
    exit_ts = pd.to_datetime(nan_str(profile.get("exit_8a")) or None, errors="coerce")
    naics = profile.get("preferred_naics")
    sizes = (
        {str(n).split(".")[0]: "small" for n in naics}
        if bool(profile.get("sb_small_naics")) and isinstance(naics, list)
        else {}
    )
    return EntityCerts(
        uei="self-attested",
        is_8a="8A" in certs,
        program_exit_date_8a=exit_ts.date() if pd.notna(exit_ts) else None,
        is_sdvosb="SDVOSB" in certs,
        is_vosb="VOSB" in certs,
        is_wosb="WOSB" in certs,
        is_edwosb="EDWOSB" in certs,
        is_hubzone="HUBZONE" in certs,
        size_standard_by_naics=sizes,
    )


def live_notice_code(ctx: Mapping[str, object], candidate_id: str, today: date) -> tuple[str, str] | None:
    """(set_aside_code, set_aside label) from this candidate's LIVE linked notice:
    link_confidence == "High", response window still open (days >= 0), non-empty
    set_aside_code; the nearest future deadline wins. Column-guarded like the Detail
    view's notice clock — bridge/notices/set_aside_code may be absent on an older
    bundle, and this returns None, never raises."""
    bridge = ctx.get("bridge")
    if (
        not isinstance(bridge, pd.DataFrame)
        or bridge.empty
        or not {"candidate_id", "linked_notice_id", "link_confidence"}.issubset(bridge.columns)
    ):
        return None
    brow = bridge[
        (bridge["candidate_id"].astype(str) == str(candidate_id)) & (bridge["link_confidence"] == "High")
    ]
    if brow.empty:
        return None
    notices = ctx.get("notices")
    if (
        not isinstance(notices, pd.DataFrame)
        or notices.empty
        or not {"notice_id", "response_deadline", "set_aside_code"}.issubset(notices.columns)
    ):
        return None
    linked = brow.merge(
        notices,
        left_on=brow["linked_notice_id"].astype(str),
        right_on=notices["notice_id"].astype(str),
        how="left",
    )
    best: tuple[int, str, str] | None = None
    for rec in linked.to_dict("records"):
        code = nan_str(rec.get("set_aside_code"))
        if not code:
            continue
        days = notice_response_days(rec.get("response_deadline"), today)
        if days is None or days < 0:
            continue
        if best is None or days < best[0]:
            best = (days, code, nan_str(rec.get("set_aside")))
    return (best[1], best[2]) if best else None


def lane_for(ctx: Mapping[str, object], row: Mapping[str, object], candidate_id: str, today: date) -> LaneVerdict:
    """The lane for one candidate under the ACTIVE profile: live-notice path when a
    certain (High + open + coded) link exists, else the historical FPDS path."""
    entity = entity_from_profile(get_profile())
    notice = live_notice_code(ctx, candidate_id, today)
    if notice is None:
        return lane_verdict(row, entity, today)
    code, label = notice
    return lane_verdict(row, entity, today, notice_code=code, notice_label=label)


def lane_chip_html(v: LaneVerdict) -> str:
    """The lane as one chip (existing chip CSS). html.escape EVERY token — the headline
    carries data-derived set-aside text, an untrusted render input."""
    cls = _CHIP_CLASS.get(v.state, "chip-muted")
    return f'<span class="chip {cls}">{html.escape(v.headline)}</span>'


def lane_counts(candidates: pd.DataFrame, entity: EntityCerts | None, today: date) -> dict[str, int]:
    """Historical-path-only lane tally for the Company strip (no notice join at 5k-row
    scale — the live check belongs to the Detail view, and the strip's caption says so)."""
    counts = {s: 0 for s in LANE_STATES}
    for rec in candidates.to_dict("records"):
        counts[lane_verdict(rec, entity, today).state] += 1
    return counts
