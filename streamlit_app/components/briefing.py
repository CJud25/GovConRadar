"""
briefing (app adapter) — the ONLY app module importing src/briefing (adapter pattern,
same firewall as reason_codes/eligibility_lane). Owns the joins the pure renderer must
never do: dim_vendor by incumbent_uei, dim_agency by subagency, bridge ⋈ notices for the
linked-notice sources, the live chips, and the lane (custom profiles only). Column-guarded
throughout — an older bundle degrades to omitted sections, never a raise.
"""

from __future__ import annotations

from datetime import date
from typing import Mapping

import pandas as pd

from briefing.evidence import gather_evidence
from briefing.render import render_brief_html
from components import eligibility_lane as el
from components import reason_codes as rc
from components.data import profile_is_custom
from utils.coerce import nan_str


def _match_row(table: object, col: str, value: str) -> Mapping[str, object] | None:
    """First row of `table` where str(col) == value — None when the table/column is
    absent, empty, or unmatched (column-guard: never a raise)."""
    if not isinstance(table, pd.DataFrame) or table.empty or col not in table.columns or not value:
        return None
    hit = table[table[col].astype(str) == value]
    return hit.iloc[0].to_dict() if not hit.empty else None


def _notice_rows(ctx: Mapping[str, object], candidate_id: str) -> tuple[Mapping[str, object], ...]:
    """This candidate's linked notices (bridge link_confidence != "No Match"), each row
    pre-joined with its bridge fields so SOURCES can cite the link confidence."""
    bridge = ctx.get("bridge")
    if (
        not isinstance(bridge, pd.DataFrame)
        or bridge.empty
        or not {"candidate_id", "linked_notice_id"}.issubset(bridge.columns)
    ):
        return ()
    brow = bridge[bridge["candidate_id"].astype(str) == str(candidate_id)]
    if "link_confidence" in brow.columns:
        brow = brow[brow["link_confidence"].astype(str) != "No Match"]
    if brow.empty:
        return ()
    notices = ctx.get("notices")
    if not isinstance(notices, pd.DataFrame) or notices.empty or "notice_id" not in notices.columns:
        return ()
    merged = brow.merge(
        notices,
        left_on=brow["linked_notice_id"].astype(str),
        right_on=notices["notice_id"].astype(str),
        how="inner",
    )
    return tuple(merged.to_dict("records"))


def build_brief_html(ctx: Mapping[str, object], row: Mapping[str, object], candidate_id: str, today: date) -> str:
    """The print-ready capture brief for one candidate under the ACTIVE profile."""
    row_d = dict(row)
    vendor_row = _match_row(ctx.get("dim_vendor"), "incumbent_uei", nan_str(row_d.get("incumbent_uei")))
    office_row = _match_row(ctx.get("dim_agency"), "subagency", nan_str(row_d.get("subagency")))
    profile = ctx.get("profile") or {}
    chips = tuple(rc.detail_chips(row_d, profile))
    custom = profile_is_custom()
    lane = el.lane_for(ctx, row_d, candidate_id, today) if custom else None
    profile_label = nan_str(profile.get("company_name")) or "your company" if custom else "demo profile"
    ev = gather_evidence(
        row_d,
        vendor_row=vendor_row,
        office_row=office_row,
        notice_rows=_notice_rows(ctx, candidate_id),
        chips=chips,
        lane=lane,
        profile_label=profile_label,
        as_of=str(ctx.get("as_of", "unknown")),
        today=today,
    )
    return render_brief_html(ev)
