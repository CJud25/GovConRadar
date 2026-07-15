"""Assembly for the baked trust_metrics_report table (mypy-strict).

Reads labels via labels.ingest (fail-loud), scans data/snapshots/*/ for
COMPARABLE snapshots (dir date >= cfg rank_stability.comparable_since AND the
snapshot's scorer_version matches the current bake's), prepares the frames the
pure scoring.trust_metrics functions expect, and stamps snapshot_date. The build
NEVER fails for missing labels/snapshots/columns — every unmeasurable metric
degrades to an honest gated row; the ONLY raise is a malformed label CSV.
"""

from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from labels.ingest import load_link_labels, load_outcome_labels
from scoring.trust_metrics import (
    METRIC_COLUMNS,
    abstention_rows,
    baseline_rows,
    first_flag_dates,
    lead_time_rows,
    link_precision_rows,
    precision_at_50_rows,
    rank_stability_rows,
)

_CANDIDATE_COLUMNS = (
    "candidate_id",
    "candidate_status",
    "days_until_expiration",
    "potential_value",
    "pursuit_score",
    "priority_tier",
    "ptw_basis",
    "burn_basis",
    "successor_visible_basis",
    "incumbent_uei",
    "flag_garbled_title",
    "flag_code_prefix",
    "flag_short_title",
    "flag_stale_expiration",
    "flag_missing_end_date",
)

_ABSTENTION_COLUMN_OF = {
    "ptw_abstention_share": "ptw_basis",
    "burn_abstention_share": "burn_basis",
    "successor_abstention_share": "successor_visible_basis",
    "vulnerability_abstention_share": "vulnerability_basis",
    "data_gap_share": "priority_tier",
}


def _read_csv(path: Path, usecols: list[str] | None = None) -> pd.DataFrame:
    return pd.read_csv(path, usecols=usecols, dtype=str, keep_default_na=False, encoding="utf-8", low_memory=False)


def _available(path: Path, wanted: tuple[str, ...]) -> list[str]:
    header = list(pd.read_csv(path, nrows=0, encoding="utf-8").columns)
    return [c for c in wanted if c in header]


def _prepare_candidates(powerbi_dir: Path) -> tuple[pd.DataFrame, set[str]]:
    """Candidates + the vendor vulnerability basis joined on. Returns the prepared
    frame and the set of abstention columns that could NOT be prepared."""
    cand_path = powerbi_dir / "fact_recompete_candidates.csv"
    missing: set[str] = set()
    if not cand_path.exists():
        return pd.DataFrame(columns=list(_CANDIDATE_COLUMNS) + ["vulnerability_basis"]), set()
    cols = _available(cand_path, _CANDIDATE_COLUMNS)
    cand = _read_csv(cand_path, usecols=cols)
    for c in _CANDIDATE_COLUMNS:
        if c not in cand.columns:
            cand[c] = ""
            missing.add(c)

    vendor_path = powerbi_dir / "dim_vendor.csv"
    cand["vulnerability_basis"] = ""
    vendor_ok = False
    if vendor_path.exists():
        vcols = _available(vendor_path, ("incumbent_uei", "vulnerability_basis"))
        if set(vcols) == {"incumbent_uei", "vulnerability_basis"}:
            vendors = _read_csv(vendor_path, usecols=vcols).drop_duplicates("incumbent_uei")
            basis = vendors.set_index("incumbent_uei")["vulnerability_basis"]
            cand["vulnerability_basis"] = cand["incumbent_uei"].map(basis).fillna("")
            vendor_ok = True
    if not vendor_ok:
        missing.add("vulnerability_basis")
    return cand, missing


def _patch_unpreparable(rows: pd.DataFrame, missing: set[str]) -> pd.DataFrame:
    """An abstention share over a column this bundle doesn't carry is not 0% —
    it is unmeasurable. Degrade those rows to honest gated rows."""
    out = rows.copy()
    for metric, column in _ABSTENTION_COLUMN_OF.items():
        if column in missing:
            idx = out.index[out["metric"] == metric]
            out.loc[idx, ["value", "ci_low", "ci_high"]] = None
            out.loc[idx, "gate_state"] = "not_yet_measured"
            out.loc[idx, "note"] = f"column '{column}' absent in this bundle — share unmeasurable"
    return out


def _snapshot_top_and_tiers(cand_path: Path, top_k: int) -> tuple[frozenset[str], dict[str, str]]:
    """One snapshot's top-K candidate-id set (S17's radar rule: non-Data-Gap, forward-
    dated where days are carried, pursuit_score desc with candidate_id tie-break) and
    its candidate_id -> priority_tier map. Column-guarded: an archive without the
    ranking columns yields empty structures, never a raise."""
    wanted = ("candidate_id", "pursuit_score", "priority_tier", "days_until_expiration")
    cols = _available(cand_path, wanted)
    if "candidate_id" not in cols or "priority_tier" not in cols or "pursuit_score" not in cols:
        return frozenset(), {}
    cand = _read_csv(cand_path, usecols=cols)
    tiers = dict(zip(cand["candidate_id"].astype(str), cand["priority_tier"].astype(str)))
    pool = cand[cand["priority_tier"].astype(str) != "Data Gap"].copy()
    if "days_until_expiration" in pool.columns:
        days = pd.to_numeric(pool["days_until_expiration"], errors="coerce")
        pool = pool[days >= 0]
    pool["_score"] = pd.to_numeric(pool["pursuit_score"], errors="coerce")
    pool = pool.sort_values(["_score", "candidate_id"], ascending=[False, True], kind="mergesort")
    return frozenset(pool.head(top_k)["candidate_id"].astype(str)), tiers


def _scan_snapshots(
    snapshots_root: Path | None, comparable_since: str, current_scorer: str, top_k: int
) -> tuple[
    dict[str, frozenset[str]],
    pd.DataFrame,
    str,
    list[tuple[str, frozenset[str]]],
    list[tuple[str, dict[str, str]]],
]:
    """COMPARABLE snapshots only -> (candidate-id sets per snapshot date,
    the UNION of (candidate_id, notice_id, link_confidence, posted_date) link rows,
    the earliest comparable snapshot date, per-snapshot top-K sets, per-snapshot
    candidate->tier maps)."""
    ids_by_date: dict[str, frozenset[str]] = {}
    linked_parts: list[pd.DataFrame] = []
    tops: list[tuple[str, frozenset[str]]] = []
    tier_maps: list[tuple[str, dict[str, str]]] = []
    empty_linked = pd.DataFrame(columns=["candidate_id", "notice_id", "link_confidence", "posted_date"])
    if snapshots_root is None or not Path(snapshots_root).is_dir():
        return ids_by_date, empty_linked, "", tops, tier_maps
    for d in sorted(Path(snapshots_root).iterdir()):
        if not d.is_dir() or len(d.name) != 10 or d.name < comparable_since:
            continue
        kpi_path = d / "dashboard_kpi_summary.csv"
        if kpi_path.exists():
            kpi = _read_csv(kpi_path)
            if "scorer_version" in kpi.columns and len(kpi) and current_scorer:
                if str(kpi["scorer_version"].iloc[0]) != current_scorer:
                    continue
        cand_path = d / "fact_recompete_candidates.csv"
        bridge_path = d / "bridge_award_opportunity_links.csv"
        notices_path = d / "fact_opportunity_notices.csv"
        if not cand_path.exists():
            continue
        ids = _read_csv(cand_path, usecols=["candidate_id"])["candidate_id"]
        ids_by_date[d.name] = frozenset(ids.astype(str))
        top, tiers = _snapshot_top_and_tiers(cand_path, top_k)
        if top:
            tops.append((d.name, top))
        if tiers:
            tier_maps.append((d.name, tiers))
        if bridge_path.exists() and notices_path.exists():
            bridge = _read_csv(bridge_path)
            bcols = {"candidate_id", "linked_notice_id", "link_confidence"}
            if bcols.issubset(bridge.columns):
                notices = _read_csv(notices_path, usecols=_available(notices_path, ("notice_id", "posted_date")))
                if {"notice_id", "posted_date"}.issubset(notices.columns):
                    part = bridge[list(bcols)].merge(
                        notices.rename(columns={"notice_id": "linked_notice_id"}).drop_duplicates("linked_notice_id"),
                        on="linked_notice_id",
                        how="left",
                        validate="many_to_one",
                    )
                    part = part.rename(columns={"linked_notice_id": "notice_id"})
                    linked_parts.append(part[["candidate_id", "notice_id", "link_confidence", "posted_date"]])
    linked = (
        pd.concat(linked_parts, ignore_index=True).drop_duplicates(["candidate_id", "notice_id"])
        if linked_parts
        else empty_linked
    )
    earliest = min(ids_by_date) if ids_by_date else ""
    return ids_by_date, linked, earliest, tops, tier_maps


def build_trust_metrics_report(
    powerbi_dir: Path,
    snapshots_root: Path | None,
    labels_dir: Path,
    cfg: Mapping[str, object],
    snapshot_date: str,
) -> pd.DataFrame:
    link_labels = load_link_labels(Path(labels_dir) / "link_labels.csv")
    outcome_labels = load_outcome_labels(Path(labels_dir) / "outcome_labels.csv")

    candidates, missing = _prepare_candidates(Path(powerbi_dir))
    bridge_path = Path(powerbi_dir) / "bridge_award_opportunity_links.csv"
    bridge = (
        _read_csv(bridge_path, usecols=_available(bridge_path, ("candidate_id", "link_confidence")))
        if bridge_path.exists()
        else pd.DataFrame(columns=["candidate_id", "link_confidence"])
    )

    kpi_path = Path(powerbi_dir) / "dashboard_kpi_summary.csv"
    current_scorer = ""
    if kpi_path.exists():
        kpi = _read_csv(kpi_path)
        if "scorer_version" in kpi.columns and len(kpi):
            current_scorer = str(kpi["scorer_version"].iloc[0])

    rank_cfg = cfg["rank_stability"]
    comparable_since = str(rank_cfg["comparable_since"]) if isinstance(rank_cfg, Mapping) else ""
    top_k = int(str(rank_cfg["top_k"])) if isinstance(rank_cfg, Mapping) else 50
    ids_by_date, linked, earliest, tops, tier_maps = _scan_snapshots(
        snapshots_root, comparable_since, current_scorer, top_k
    )
    first_flags = first_flag_dates(ids_by_date)

    parts = [
        link_precision_rows(link_labels, cfg),
        precision_at_50_rows(outcome_labels, cfg),
        _patch_unpreparable(abstention_rows(candidates), missing),
        lead_time_rows(first_flags, linked, earliest or snapshot_date, cfg),
        rank_stability_rows(tops, tier_maps, cfg),
        baseline_rows(candidates, bridge, cfg),
    ]
    report = pd.concat(parts, ignore_index=True)[list(METRIC_COLUMNS)]
    report["snapshot_date"] = snapshot_date
    return report
