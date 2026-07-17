"""
opportunity_linking.py — Best-effort fuzzy matching of recompete candidates to
SAM.gov opportunity notices. Runs correctly (and produces an empty, well-formed
bridge table) when zero SAM.gov notices are present — this environment's
default. See documentation/data_acquisition_plan.md for why.
"""

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from transform.cleaning import CONTACT_TITLE_PLACEHOLDER
from utils.coerce import nan_str, norm_id
from utils.config import OPPORTUNITY_LINKING

TITLE_MATCH_THRESHOLD = 70  # rapidfuzz token_sort_ratio, 0-100 — a STRONG title hit (links alone)
# A loosened floor. A title score in [LOOSE, STRONG) links ONLY when a corroborating signal
# (NAICS / agency / PoP-state) is also present, so loosening never manufactures a false link.
TITLE_LOOSE_THRESHOLD = 55

# Recency gate (asserted priors — config/opportunity_linking.yaml): an establishing match is
# REJECTED when both dates are known and the notice was not posted within
# [anchor - BEFORE months, anchor + AFTER months] around EITHER of the candidate's own ends —
# the policy-selected expiration OR the current period end (an award whose options go
# unexercised is legitimately recompeted near current_end_date, far before the potential end;
# that displacement event must not be gated out). A recompete solicitation appears near/after
# the incumbent's expiry — never years before it (the 2026-07 audit's degenerate case: one
# 2018 notice "recompeting" 70 of 120 2025-enders).
# A missing/unparseable date on either side never gates: it cannot prove a violation.
RECENCY_MONTHS_BEFORE = int(OPPORTUNITY_LINKING["recency_months_before"])
RECENCY_MONTHS_AFTER = int(OPPORTUNITY_LINKING["recency_months_after"])

BRIDGE_COLUMNS = ["candidate_id", "linked_notice_id", "linked_notice_type", "link_confidence", "link_reason"]

# Normalized columns that link_candidate_to_notices reads from a notices row.
OPPORTUNITY_CLEAN_COLUMNS = [
    "notice_id",
    "notice_type",
    "solicitation_number",
    "title",
    "naics",
    "agency",
    "place_of_performance_state",
    "posted_date",
]


def _extract_sam_naics(rec: dict):
    """SAM.gov v2 returns the primary NAICS as `naicsCode` (a string); some
    payloads instead carry a `naics` list of {code: ...} dicts. Prefer the
    scalar, fall back to the first list entry. Returned as a string to match
    the candidate's `naics` (also a string) for the equality check."""
    code = rec.get("naicsCode")
    if code:
        return str(code)
    naics_list = rec.get("naics")
    if isinstance(naics_list, list) and naics_list:
        first = naics_list[0]
        if isinstance(first, dict):
            code = first.get("code") or first.get("naicsCode")
            return str(code) if code else None
        return str(first)
    return None


def _extract_sam_agency(rec: dict) -> str:
    """Best-effort agency name. SAM.gov v2 encodes the org hierarchy in
    `fullParentPathName` (e.g. 'DEPT OF DEFENSE.DEPT OF THE ARMY...'); the
    top-level department is the first dot-delimited segment. Falls back to a
    flat `department` field if present. Uppercased so it lines up with
    awards_clean's normalize_agency_name output. Agency is only ever a
    corroborating (+10) signal, so exact abbreviation reconciliation
    (DEPT vs DEPARTMENT) is intentionally out of scope for v1."""
    path = rec.get("fullParentPathName")
    if path and str(path).strip():
        top = str(path).split(".")[0].strip()
        if top:
            return top.upper()
    dept = rec.get("department")
    if dept and str(dept).strip():
        return str(dept).strip().upper()
    return "UNKNOWN"


def _extract_sam_pop_state(rec: dict):
    """Best-effort place-of-performance state code from a live SAM.gov v2 record
    (`placeOfPerformance.state.code`). A corroborating signal only, so a miss is harmless."""
    pop = rec.get("placeOfPerformance")
    if isinstance(pop, dict):
        state = pop.get("state")
        if isinstance(state, dict):
            code = state.get("code") or state.get("name")
            return str(code).strip().upper() if code else None
        if state:
            return str(state).strip().upper()
    return None


def build_opportunities_clean(sam_records: list) -> pd.DataFrame:
    """Maps SAM.gov opportunity records to the normalized column names
    link_candidate_to_notices expects.

    Handles two input shapes:
      * live API records (camelCase: noticeId, solicitationNumber, naicsCode, ...)
      * bulk-export records already normalized by sam_bulk.load_sam_opportunities_from_csv
        (snake_case: notice_id, solicitation_number, naics, agency, ...)

    Without this step the raw API field names never line up with the snake_case
    columns the linker reads, so every candidate would come back 'No Match' even
    when SAM data is present. Returns a well-formed empty frame for the
    zero-SAM-data case."""
    if not sam_records:
        return pd.DataFrame(columns=OPPORTUNITY_CLEAN_COLUMNS)
    rows = []
    for rec in sam_records:
        if "notice_id" in rec:  # already-normalized bulk-export record
            rows.append(
                {
                    "notice_id": rec.get("notice_id"),
                    "notice_type": rec.get("notice_type"),
                    "solicitation_number": rec.get("solicitation_number"),
                    "title": rec.get("title"),
                    "naics": str(rec["naics"]) if rec.get("naics") else None,
                    "agency": (str(rec["agency"]).strip().upper() if rec.get("agency") else "UNKNOWN"),
                    "place_of_performance_state": (
                        str(rec["place_of_performance_state"]).strip().upper()
                        if rec.get("place_of_performance_state")
                        else None
                    ),
                    "posted_date": rec.get("posted_date"),
                }
            )
        else:  # live API v2 record
            rows.append(
                {
                    "notice_id": rec.get("noticeId"),
                    "notice_type": rec.get("type"),
                    "solicitation_number": rec.get("solicitationNumber"),
                    "title": rec.get("title"),
                    "naics": _extract_sam_naics(rec),
                    "agency": _extract_sam_agency(rec),
                    "place_of_performance_state": _extract_sam_pop_state(rec),
                    "posted_date": rec.get("postedDate"),
                }
            )
    return pd.DataFrame(rows, columns=OPPORTUNITY_CLEAN_COLUMNS)


def _matchable_title(v) -> str:
    """Title text the fuzzy matcher may score: '' for missing AND for the public-artifact
    redaction placeholder (transform.cleaning.redact_contact_titles replaces whole titles
    with one fixed string — two redacted titles must not fabricate a 100-point match, the
    same trap as rapidfuzz scoring "" vs "" as 100)."""
    t = nan_str(v)
    return "" if t == CONTACT_TITLE_PLACEHOLDER else t


def _prepare_notices(notices: pd.DataFrame) -> list[dict]:
    """Precompute each notice's matcher inputs ONCE (normalized solicitation id / PoP state,
    cleaned title), so build_bridge_table does not re-normalize every notice for every
    candidate — the O(candidates × notices) hot path. The list order matches ``notices`` row
    order, so it aligns with the cdist title-score matrix columns."""
    if notices.empty:
        return []
    return [
        {
            "notice_id": rec.get("notice_id"),
            "notice_type": rec.get("notice_type"),
            "naics": rec.get("naics"),
            "agency": rec.get("agency"),
            "sol_norm": norm_id(rec.get("solicitation_number")),
            "state_norm": nan_str(rec.get("place_of_performance_state")).upper(),
            "title": _matchable_title(rec.get("title")),
            "posted": pd.to_datetime(rec.get("posted_date"), errors="coerce"),
        }
        for rec in notices.to_dict("records")
    ]


def _candidate_recency_anchors(candidate: dict) -> list:
    """The award-end Timestamps the recency gate anchors on (deduped, order-stable):
    the pipeline's policy-selected expiration when present (recorded per-row in
    expiration_date_basis — see config/recompete.yaml), else the potential end, else the
    current end — PLUS the current period end whenever it parses. Anchoring on the current
    end as well keeps a legitimate early recompete linkable when options go unexercised
    (the follow-on posts near current_end_date, far before the potential end). Empty when
    none parse — an unknown expiry cannot gate."""
    anchors = []
    for col in ("selected_expiration_date", "potential_end_date", "current_end_date"):
        ts = pd.to_datetime(candidate.get(col), errors="coerce")
        if pd.notna(ts):
            anchors.append(ts)
            break
    cur = pd.to_datetime(candidate.get("current_end_date"), errors="coerce")
    if pd.notna(cur) and cur not in anchors:
        anchors.append(cur)
    return anchors


def link_candidate_to_notices(
    candidate: dict, notices: pd.DataFrame, title_scores=None, prepared: list[dict] | None = None
) -> dict:
    """Best match for one candidate via multi-signal corroboration (§6).

    A link is ESTABLISHED only by: an exact/normalized PIID *or* referenced_idv_piid ↔
    notice solicitation_number match (=> High); a strong title hit (>= STRONG, => Medium);
    or a loosened title (>= LOOSE, < STRONG) **promoted by** at least one corroborating
    signal — NAICS / agency / PoP-state (=> Low). NAICS/agency/PoP alone never establish a
    link (a NAICS is shared by thousands of unrelated awards). Only an exact id → High, so a
    near-miss is never promoted to a false High.

    RECENCY GATE (config/opportunity_linking.yaml): whatever the establishing signal, a
    notice whose posted_date falls outside [anchor - RECENCY_MONTHS_BEFORE,
    anchor + RECENCY_MONTHS_AFTER] around EVERY known anchor — the policy-selected
    expiration AND the current period end (_candidate_recency_anchors) — is rejected: a
    recompete solicitation appears near/after the incumbent's expiry, not years before it.
    Landing inside EITHER anchor's window accepts, so an early recompete posted near
    current_end_date after options go unexercised still links.

    ORIGIN GATE: an establishing match whose posted_date precedes the candidate's own
    pop_start_date is rejected — an award's own origin solicitation (posted before its
    performance even started) is never its successor, however strong the id/title hit.

    Both gates need BOTH of their dates: a missing/unparseable posted_date, candidate end,
    or pop_start_date never gates (it cannot prove a violation), preserving legacy
    behavior for undated rows.

    ``title_scores`` is an optional precomputed 1-D array of token_sort_ratio scores aligned
    to the notice order (build_bridge_table vectorizes this). ``prepared`` is the output of
    ``_prepare_notices`` — passed in by build_bridge_table so notices are normalized once for
    the whole batch; when omitted it is computed from ``notices`` for a standalone call.
    """
    if prepared is None:
        prepared = _prepare_notices(notices)
    if not prepared:
        return {
            "linked_notice_id": None,
            "linked_notice_type": None,
            "link_confidence": "No Match",
            "link_reason": "No SAM.gov opportunity notices available",
        }

    candidate_title = _matchable_title(candidate.get("contract_title"))
    candidate_ids = {norm_id(candidate.get("piid")), norm_id(candidate.get("referenced_idv_piid"))} - {""}
    candidate_state = nan_str(candidate.get("place_of_performance_state")).upper()
    cand_naics, cand_agency = candidate.get("naics"), candidate.get("agency")

    # Recency windows around this candidate's own ends — selected expiry AND current end
    # (empty when no end is known; inside EITHER window accepts).
    recency_windows = [
        (anchor - pd.DateOffset(months=RECENCY_MONTHS_BEFORE), anchor + pd.DateOffset(months=RECENCY_MONTHS_AFTER))
        for anchor in _candidate_recency_anchors(candidate)
    ]
    # Origin gate: a notice posted before the award's own start is its origin paperwork,
    # never its successor (NaT when unknown — cannot gate).
    pop_start = pd.to_datetime(candidate.get("pop_start_date"), errors="coerce")
    recency_rejected = False
    origin_rejected = False

    best_rank, best_notice, best_reasons, best_conf = -1.0, None, [], "No Match"
    for i, notice in enumerate(prepared):
        id_match = bool(notice["sol_norm"]) and notice["sol_norm"] in candidate_ids
        naics_match = bool(cand_naics) and cand_naics == notice["naics"]
        agency_match = bool(cand_agency) and cand_agency == notice["agency"]
        pop_match = bool(candidate_state) and candidate_state == notice["state_norm"]
        corroboration = naics_match + agency_match + pop_match

        if title_scores is not None:
            title_score = float(title_scores[i])
        elif candidate_title and notice["title"]:
            title_score = fuzz.token_sort_ratio(candidate_title, notice["title"])
        else:
            title_score = 0
        title_strong = title_score >= TITLE_MATCH_THRESHOLD
        title_loose = TITLE_LOOSE_THRESHOLD <= title_score < TITLE_MATCH_THRESHOLD

        # Decide the establishing signal (or skip this notice entirely).
        if id_match:
            establishing = "id"
        elif title_strong:
            establishing = "title_strong"
        elif title_loose and corroboration >= 1:
            establishing = "title_loose"
        else:
            continue

        # ORIGIN GATE: an establishing match posted before this award's own start is the
        # award's origin solicitation (the notice that PRODUCED it), never its successor —
        # a short PoP puts the origin notice inside the recency window, so this must be
        # checked in its own right. Applies only when BOTH dates are known.
        if pd.notna(pop_start) and pd.notna(notice["posted"]) and notice["posted"] < pop_start:
            origin_rejected = True
            continue

        # RECENCY GATE: an establishing match posted outside the sane window around EVERY
        # known end (selected expiry / current end) is rejected — a notice posted years
        # before an award's end cannot be its recompete solicitation, while one near a
        # declined-options current end still links. Applies only when BOTH dates are known.
        if recency_windows and pd.notna(notice["posted"]) and not any(
            lo <= notice["posted"] <= hi for lo, hi in recency_windows
        ):
            recency_rejected = True
            continue

        rank, reasons = 0.0, []
        if id_match:
            rank += 100
            reasons.append("exact solicitation/PIID match")
        if title_strong:
            rank += title_score / 5
            reasons.append(f"title similarity {title_score:.0f}")
        elif establishing == "title_loose":
            rank += title_score / 10
            reasons.append(f"loosened title {title_score:.0f} (corroborated)")
        if naics_match:
            rank += 15
            reasons.append("NAICS match")
        if agency_match:
            rank += 10
            reasons.append("agency match")
        if pop_match:
            rank += 8
            reasons.append("place-of-performance match")

        # Confidence comes from the establishing signal, not the raw rank: only an exact id
        # is High, so corroboration/title can never manufacture a false High.
        confidence = {"id": "High", "title_strong": "Medium", "title_loose": "Low"}[establishing]

        if rank > best_rank:
            best_rank, best_notice, best_reasons, best_conf = rank, notice, reasons, confidence

    if best_notice is None:
        rejected = []
        if recency_rejected:
            rejected.append("outside the recency window around this award's expiration")
        if origin_rejected:
            rejected.append("before this award's own start (an origin notice, not a successor)")
        reason = (
            "Only match(es) posted " + " or ".join(rejected)
            if rejected
            else "No notice cleared the minimum match threshold"
        )
        return {
            "linked_notice_id": None,
            "linked_notice_type": None,
            "link_confidence": "No Match",
            "link_reason": reason,
        }

    return {
        "linked_notice_id": best_notice["notice_id"],
        "linked_notice_type": best_notice["notice_type"],
        "link_confidence": best_conf,
        "link_reason": "; ".join(best_reasons),
    }


def build_bridge_table(recompete_candidates: pd.DataFrame, notices: pd.DataFrame) -> pd.DataFrame:
    if recompete_candidates.empty:
        return pd.DataFrame(columns=BRIDGE_COLUMNS)

    # Normalize every notice ONCE for the whole batch (ids/state/title), instead of
    # re-normalizing N notices for each of C candidates — the former O(C×N) hot path.
    prepared = _prepare_notices(notices)

    # Vectorize the title fuzzy-match once with a C kernel (process.cdist) instead of
    # ~candidates × notices Python-level fuzz calls. Scores are IDENTICAL to the per-pair
    # path: token_sort_ratio in float, with empty candidate or notice titles forced to 0 to
    # reproduce the `candidate_title and notice_title else 0` guard (rapidfuzz scores "" vs
    # "" as 100, which must not fabricate a link; _matchable_title blanks the redaction
    # placeholder for the same reason).
    score_matrix = None
    if prepared:
        cand_titles = (
            [_matchable_title(t) for t in recompete_candidates["contract_title"]]
            if "contract_title" in recompete_candidates.columns
            else [""] * len(recompete_candidates)
        )
        notice_titles = [n["title"] for n in prepared]  # already cleaned in _prepare_notices
        score_matrix = process.cdist(
            cand_titles,
            notice_titles,
            scorer=fuzz.token_sort_ratio,
            dtype=np.float64,
            workers=-1,
        )
        cand_empty = np.array([t == "" for t in cand_titles])
        notice_empty = np.array([t == "" for t in notice_titles])
        if cand_empty.any():
            score_matrix[cand_empty, :] = 0.0
        if notice_empty.any():
            score_matrix[:, notice_empty] = 0.0

    rows = []
    for pos, candidate in enumerate(recompete_candidates.to_dict("records")):
        title_scores = score_matrix[pos] if score_matrix is not None else None
        link = link_candidate_to_notices(candidate, notices, title_scores=title_scores, prepared=prepared)
        rows.append({"candidate_id": candidate["candidate_id"], **link})
    return pd.DataFrame(rows)
