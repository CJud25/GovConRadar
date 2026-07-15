"""Pure gated trust-metrics engine (mypy-strict; no I/O, no clock, no config import).

Every function takes PREPARED frames + a plain Mapping cfg and returns rows shaped
exactly METRIC_COLUMNS. The honesty invariant is structural: `value` (and the CI)
is present IFF gate_state == "published"; a gated metric below its floor emits an
honest note and NO number. snapshot_date is stamped by the assembly layer
(export.trust_report), not here.

Strict self-containment (invariant 7): SUCCESSOR_BASES is imported from the strict
successor_proxy; VULNERABILITY_UNKNOWN_BASES is RESTATED below as literals because
its source (transform.incumbent_agency) is untyped first-party — the ONE sanctioned
restatement, pinned to its source by a mirror test in the untyped test layer.
"""

import math
from collections.abc import Mapping, Sequence
from typing import cast

import pandas as pd

from labels.taxonomy import CONTINUATION_OUTCOMES
from scoring.successor_proxy import SUCCESSOR_BASES

METRIC_COLUMNS: tuple[str, ...] = (
    "metric",
    "value",
    "n",
    "ci_low",
    "ci_high",
    "gate_state",
    "note",
    "surface",
    "snapshot_date",
)
GATE_STATES: tuple[str, ...] = ("published", "not_yet_measured", "insufficient_snapshots")

LINK_TIERS: tuple[str, ...] = ("High", "Medium", "Low")

# RESTATED from transform.incumbent_agency (untyped; see module docstring).
VULNERABILITY_UNKNOWN_BASES: frozenset[str] = frozenset({"no_forward_book", "insufficient_expiration_coverage"})

# Refusal values per basis column (measured vocabularies on the 2026-07-15 bake).
_PTW_ABSTAIN = "insufficient"
_BURN_ABSTAIN = "insufficient"
_SUCCESSOR_ABSTAIN = "insufficient_cell"  # one of SUCCESSOR_BASES
assert _SUCCESSOR_ABSTAIN in SUCCESSOR_BASES

_BASELINE_NOTE = "no outcome labels in this comparison — states what tiering adds, claims nothing it can't"


def _section(cfg: Mapping[str, object], name: str) -> Mapping[str, object]:
    sec = cfg[name]
    if not isinstance(sec, Mapping):
        raise TypeError(f"cfg[{name!r}] must be a mapping")
    return cast(Mapping[str, object], sec)


def _cfg_int(cfg: Mapping[str, object], key: str) -> int:
    v = cfg[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"cfg[{key!r}] must be an int")
    return v


def _cfg_float(cfg: Mapping[str, object], key: str) -> float:
    v = cfg[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(f"cfg[{key!r}] must be a number")
    return float(v)


def _row(
    metric: str,
    *,
    value: float | None,
    n: int | None,
    ci: tuple[float, float] | None,
    gate_state: str,
    note: str,
    surface: str,
) -> dict[str, object]:
    if gate_state not in GATE_STATES:
        raise ValueError(f"unknown gate_state {gate_state!r}")
    published = gate_state == "published"
    if published != (value is not None):
        raise ValueError(f"{metric}: value present <=> published (structural honesty)")
    if not published and not note:
        raise ValueError(f"{metric}: a gated metric must carry an honest note")
    return {
        "metric": metric,
        "value": value,
        "n": n,
        "ci_low": ci[0] if ci else None,
        "ci_high": ci[1] if ci else None,
        "gate_state": gate_state,
        "note": note,
        "surface": surface,
        "snapshot_date": "",  # stamped by the assembly layer
    }


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(METRIC_COLUMNS))


def wilson_interval(successes: int, n: int, z: float) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. Requires n >= 1."""
    if n < 1:
        raise ValueError("wilson_interval requires n >= 1")
    if not 0 <= successes <= n:
        raise ValueError("successes must be within [0, n]")
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def link_precision_rows(link_labels: pd.DataFrame, cfg: Mapping[str, object]) -> pd.DataFrame:
    """Per link_confidence tier: precision over hand-labeled verdicts, gated on the
    labeled-n floor. `unsure` is excluded from the numerator AND the denominator and
    disclosed as its own rate; false_link_rate = 1 - precision under the same gate."""
    floor = _cfg_int(_section(cfg, "link_labels"), "min_labels_per_tier")
    z = _cfg_float(cfg, "wilson_z")
    rows: list[dict[str, object]] = []
    for tier in LINK_TIERS:
        sub = link_labels[link_labels["link_confidence"].astype(str) == tier] if len(link_labels) else link_labels
        verdicts = sub["label"].astype(str).str.strip() if len(sub) else pd.Series(dtype=str)
        correct = int((verdicts == "correct").sum())
        incorrect = int((verdicts == "incorrect").sum())
        unsure = int((verdicts == "unsure").sum())
        labeled_n = correct + incorrect  # unsure excluded from BOTH sides
        verdict_n = labeled_n + unsure
        if labeled_n >= floor:
            precision = correct / labeled_n
            lo, hi = wilson_interval(correct, labeled_n, z)
            rows.append(
                _row(
                    f"link_precision_{tier.lower()}",
                    value=precision,
                    n=labeled_n,
                    ci=(lo, hi),
                    gate_state="published",
                    note=f"hand-labeled sample; {unsure} unsure excluded and disclosed",
                    surface="app",
                )
            )
            rows.append(
                _row(
                    f"link_false_link_rate_{tier.lower()}",
                    value=1.0 - precision,
                    n=labeled_n,
                    ci=(1.0 - hi, 1.0 - lo),
                    gate_state="published",
                    note="1 - precision on the same labeled sample",
                    surface="app",
                )
            )
        else:
            note = (
                f"not yet measured — {labeled_n} of the >={floor} labels the {tier} tier "
                "needs before a precision number publishes"
            )
            rows.append(
                _row(
                    f"link_precision_{tier.lower()}",
                    value=None,
                    n=labeled_n,
                    ci=None,
                    gate_state="not_yet_measured",
                    note=note,
                    surface="app",
                )
            )
            rows.append(
                _row(
                    f"link_false_link_rate_{tier.lower()}",
                    value=None,
                    n=labeled_n,
                    ci=None,
                    gate_state="not_yet_measured",
                    note=note,
                    surface="app",
                )
            )
        if verdict_n >= 1:
            rows.append(
                _row(
                    f"link_unsure_rate_{tier.lower()}",
                    value=unsure / verdict_n,
                    n=verdict_n,
                    ci=None,
                    gate_state="published",
                    note="share of filled verdicts answered 'unsure' (labeling progress disclosure)",
                    surface="app",
                )
            )
        else:
            rows.append(
                _row(
                    f"link_unsure_rate_{tier.lower()}",
                    value=None,
                    n=0,
                    ci=None,
                    gate_state="not_yet_measured",
                    note=f"no {tier}-tier verdicts filled yet",
                    surface="app",
                )
            )
    return _frame(rows)


# Pinned published-note copy for precision_at_50 (verbatim per the approved plan).
_PRECISION_AT_50_NOTE = (
    "share of the disclosed top-50 sample (shipped demo-profile ranking) with positive "
    "evidence the work continued — recompete, bridge, vehicle migration, sole-source "
    "follow-on, consolidation/split, or an out-of-scope successor; `ended` requires "
    "positive evidence; undeterminables excluded and disclosed. Not a win rate. "
    "Not a probability."
)


def precision_at_50_rows(outcome_labels: pd.DataFrame, cfg: Mapping[str, object]) -> pd.DataFrame:
    """Gated precision@50 over the disclosed top-50 outcome sample, plus the
    always-publishable progress/refusal rates. Universe: sample_set in ("top50",
    "both"). determinable = filled and != "undeterminable"; positive = a
    CONTINUATION_OUTCOMES member (a taxonomy change moves this by construction);
    negative = "ended" (positive evidence only). Blank rows count as unlabeled,
    never as negatives."""
    sec = _section(cfg, "outcome_labels")
    floor = _cfg_int(sec, "min_determinable_for_precision")
    z = _cfg_float(cfg, "wilson_z")

    if len(outcome_labels):
        uni = outcome_labels[outcome_labels["sample_set"].astype(str).isin(("top50", "both"))]
        labels = uni["outcome_label"].astype(str).str.strip()
    else:
        labels = pd.Series(dtype=str)
    universe_n = int(len(labels))
    filled = labels[labels != ""]
    labeled_n = int(len(filled))
    undeterminable = int((filled == "undeterminable").sum())
    out_of_scope = int((filled == "successor_out_of_scope").sum())
    positives = int(filled.isin(CONTINUATION_OUTCOMES).sum())
    determinable = labeled_n - undeterminable

    rows: list[dict[str, object]] = []
    if determinable >= floor:
        lo, hi = wilson_interval(positives, determinable, z)
        rows.append(
            _row(
                "precision_at_50",
                value=positives / determinable,
                n=determinable,
                ci=(lo, hi),
                gate_state="published",
                note=_PRECISION_AT_50_NOTE,
                surface="app",
            )
        )
    else:
        rows.append(
            _row(
                "precision_at_50",
                value=None,
                n=determinable,
                ci=None,
                gate_state="not_yet_measured",
                note=(
                    f"not yet measured — {determinable} of the >={floor} determinable outcome "
                    "labels the disclosed top-50 sample needs before a precision number publishes"
                ),
                surface="app",
            )
        )
    if labeled_n >= 1:
        rows.append(
            _row(
                "outcome_undeterminable_rate",
                value=undeterminable / labeled_n,
                n=labeled_n,
                ci=None,
                gate_state="published",
                note=(
                    "share of filled top-50 outcome labels judged undeterminable from public "
                    "data — refusals are always publishable"
                ),
                surface="app",
            )
        )
        rows.append(
            _row(
                "outcome_out_of_scope_rate",
                value=out_of_scope / labeled_n,
                n=labeled_n,
                ci=None,
                gate_state="published",
                note=(
                    "share of filled top-50 outcome labels whose successor sits outside this "
                    "dataset's scope (e.g. assisted acquisition) — the work continued where our "
                    "window can't see"
                ),
                surface="app",
            )
        )
    else:
        for metric in ("outcome_undeterminable_rate", "outcome_out_of_scope_rate"):
            rows.append(
                _row(
                    metric,
                    value=None,
                    n=0,
                    ci=None,
                    gate_state="not_yet_measured",
                    note="no top-50 outcome verdicts filled yet — a share of nothing is not a number",
                    surface="app",
                )
            )
    rows.append(
        _row(
            "outcome_labeled_n",
            value=float(labeled_n),
            n=universe_n,
            ci=None,
            gate_state="published",
            note=f"{labeled_n} of {universe_n} disclosed top-50 worksheet rows labeled (progress counter)",
            surface="app",
        )
    )
    return _frame(rows)


def abstention_rows(candidates: pd.DataFrame) -> pd.DataFrame:
    """Always-publishable honesty rates over the PREPARED candidates frame (the
    assembly layer joins dim_vendor.vulnerability_basis onto candidates by
    incumbent_uei): the share of candidates where each estimator REFUSED, plus the
    Data-Gap quarantine share. On an empty frame every row degrades to an honest
    not_yet_measured (a share of nothing is not a number)."""
    n = int(len(candidates))
    specs: list[tuple[str, str, object]] = [
        ("ptw_abstention_share", "ptw_basis", _PTW_ABSTAIN),
        ("burn_abstention_share", "burn_basis", _BURN_ABSTAIN),
        ("successor_abstention_share", "successor_visible_basis", _SUCCESSOR_ABSTAIN),
        ("vulnerability_abstention_share", "vulnerability_basis", VULNERABILITY_UNKNOWN_BASES),
        ("data_gap_share", "priority_tier", "Data Gap"),
    ]
    rows: list[dict[str, object]] = []
    for metric, column, refusal in specs:
        if n == 0:
            rows.append(
                _row(
                    metric,
                    value=None,
                    n=0,
                    ci=None,
                    gate_state="not_yet_measured",
                    note="no candidates in the prepared frame",
                    surface="app",
                )
            )
            continue
        values = candidates[column].astype(str).str.strip()
        if isinstance(refusal, frozenset):
            hits = int(values.isin(refusal).sum())
            what = " or ".join(sorted(refusal))
        else:
            hits = int((values == str(refusal)).sum())
            what = str(refusal)
        rows.append(
            _row(
                metric,
                value=hits / n,
                n=n,
                ci=None,
                gate_state="published",
                note=f"{hits:,} of {n:,} candidates where {column} = {what} — "
                "the estimator refused rather than guessed",
                surface="app",
            )
        )
    return _frame(rows)


_LEAD_TIME_BIAS_NOTE = (
    "selection bias disclosed: only the ~12% linked subset of candidates can carry a "
    "lead time, it skews late-stage (a notice already exists), and DoD FPDS reporting "
    "lags ~90 days"
)


def first_flag_dates(snapshot_candidate_ids: Mapping[str, frozenset[str]]) -> dict[str, str]:
    """candidate_id -> the earliest snapshot date (ISO string key) containing it.
    The caller passes COMPARABLE snapshots only (>= comparable_since, same scorer)."""
    first: dict[str, str] = {}
    for snap_date in sorted(snapshot_candidate_ids):
        for cid in snapshot_candidate_ids[snap_date]:
            first.setdefault(cid, snap_date)
    return first


def lead_time_rows(
    first_flags: Mapping[str, str],
    linked: pd.DataFrame,
    earliest_snapshot: str,
    cfg: Mapping[str, object],
) -> pd.DataFrame:
    """lead_time_days = earliest posted_date across a candidate's High/Medium linked
    notices − first_flag_date. Degenerate cases, all pinned: (1) negative (flagged
    after the notice) KEPT in the median and disclosed as lead_time_flagged_after_rate,
    never clipped; (2) notice precedes the data window → excluded from the median,
    counted as lead_time_window_precedes_n; (3) multiple notices → earliest parseable
    posted_date anchors (most conservative credit); (4) left-censored (first flag ==
    earliest snapshot) → included, the median is a CONSERVATIVE LOWER BOUND with
    lead_time_censored_share disclosed; (5) missing/unparseable posted_date →
    excluded, counted in the note."""
    min_conf = str(_section(cfg, "lead_time")["min_link_confidence"])
    allowed = ("High",) if min_conf == "High" else ("High", "Medium")
    window_start = pd.Timestamp(earliest_snapshot)

    usable = linked[linked["link_confidence"].astype(str).isin(allowed)] if len(linked) else linked
    unparseable = 0
    window_precedes = 0
    lead_days: list[float] = []
    censored = 0
    for cid, group in usable.groupby("candidate_id") if len(usable) else ():
        flag_date = first_flags.get(str(cid))
        if flag_date is None:
            continue
        posted = pd.to_datetime(group["posted_date"], errors="coerce")
        bad = int(posted.isna().sum())
        unparseable += bad
        posted = posted.dropna()
        if posted.empty:
            continue
        anchor = posted.min()  # earliest notice = the most conservative credit
        if anchor < window_start:
            window_precedes += 1
            continue
        lead_days.append(float((anchor - pd.Timestamp(flag_date)).days))
        if flag_date == earliest_snapshot:
            censored += 1

    n = len(lead_days)
    rows: list[dict[str, object]] = []
    if n >= 1:
        median = float(pd.Series(lead_days).median())
        negative = sum(1 for d in lead_days if d < 0)
        rows.append(
            _row(
                "lead_time_median_days",
                value=median,
                n=n,
                ci=None,
                gate_state="published",
                note=(
                    f"CONSERVATIVE LOWER BOUND — {censored} of {n} cases are left-censored at the "
                    f"earliest comparable snapshot; negatives kept, never clipped; {unparseable} "
                    f"unparseable posted_date(s) excluded; {_LEAD_TIME_BIAS_NOTE}"
                ),
                surface="app",
            )
        )
        rows.append(
            _row(
                "lead_time_flagged_after_rate",
                value=negative / n,
                n=n,
                ci=None,
                gate_state="published",
                note=f"share of computable cases flagged AFTER the notice posted; {_LEAD_TIME_BIAS_NOTE}",
                surface="app",
            )
        )
        rows.append(
            _row(
                "lead_time_censored_share",
                value=censored / n,
                n=n,
                ci=None,
                gate_state="published",
                note=(
                    "share of computable cases whose first flag IS the earliest comparable snapshot "
                    f"(true lead time can only be longer); {_LEAD_TIME_BIAS_NOTE}"
                ),
                surface="app",
            )
        )
        rows.append(
            _row(
                "lead_time_window_precedes_n",
                value=float(window_precedes),
                n=n,
                ci=None,
                gate_state="published",
                note=(
                    "count of linked candidates whose earliest notice predates the data window — "
                    f"excluded from the median (we cannot know when we would have flagged them); "
                    f"{_LEAD_TIME_BIAS_NOTE}"
                ),
                surface="app",
            )
        )
    else:
        note = (
            f"no High/Medium-linked candidates with usable dates yet "
            f"({window_precedes} linked case(s) precede the data window; {unparseable} "
            f"unparseable date(s)) — publishes as newly-posted notices get linked; "
            f"{_LEAD_TIME_BIAS_NOTE}"
        )
        for metric in (
            "lead_time_median_days",
            "lead_time_flagged_after_rate",
            "lead_time_censored_share",
            "lead_time_window_precedes_n",
        ):
            rows.append(_row(metric, value=None, n=0, ci=None, gate_state="not_yet_measured", note=note, surface="app"))
    return _frame(rows)


_RANK_STABILITY_PUBLISHED_NOTE = (
    "median across adjacent comparable snapshot pairs (top-50 by the shipped demo-profile "
    "ranking); describes ranking churn between snapshots, not accuracy"
)


def _stability_gate_note(n_snaps: int) -> str:
    return (
        f"{n_snaps} comparable snapshots exist; stability publishes at 3+ — pre-migration "
        "snapshots are un-diffable (candidate-id migration 2026-07-07)"
    )


def rank_stability_rows(
    snapshot_tops: Sequence[tuple[str, frozenset[str]]],
    snapshot_tiers: Sequence[tuple[str, Mapping[str, str]]],
    cfg: Mapping[str, object],
) -> pd.DataFrame:
    """Ranking-churn accumulator over COMPARABLE snapshots (the assembly layer already
    filtered on comparable_since + scorer_version). Per adjacent date-sorted pair:
    top-K overlap share and tier_migration_share (ids present in BOTH snapshots whose
    priority_tier changed). Publishes the medians once >= min_snapshots comparable
    snapshots exist; below that, gate_state="insufficient_snapshots" with the honest
    accumulator note."""
    sec = _section(cfg, "rank_stability")
    min_snaps = _cfg_int(sec, "min_snapshots")
    k = _cfg_int(sec, "top_k")

    tops = sorted(snapshot_tops, key=lambda t: t[0])
    tiers = {d: m for d, m in snapshot_tiers}
    n_snaps = len(tops)

    overlaps: list[float] = []
    migrations: list[float] = []
    for (d1, top1), (d2, top2) in zip(tops, tops[1:]):
        overlaps.append(len(top1 & top2) / k)
        t1, t2 = tiers.get(d1, {}), tiers.get(d2, {})
        common = set(t1) & set(t2)
        if common:
            migrations.append(sum(1 for cid in common if t1[cid] != t2[cid]) / len(common))

    rows: list[dict[str, object]] = []
    if n_snaps >= min_snaps and overlaps and migrations:
        rows.append(
            _row(
                "rank_stability_overlap_at_50",
                value=float(pd.Series(overlaps).median()),
                n=len(overlaps),
                ci=None,
                gate_state="published",
                note=_RANK_STABILITY_PUBLISHED_NOTE,
                surface="app",
            )
        )
        rows.append(
            _row(
                "rank_stability_tier_migration_share",
                value=float(pd.Series(migrations).median()),
                n=len(migrations),
                ci=None,
                gate_state="published",
                note=_RANK_STABILITY_PUBLISHED_NOTE,
                surface="app",
            )
        )
    else:
        note = _stability_gate_note(n_snaps)
        for metric in ("rank_stability_overlap_at_50", "rank_stability_tier_migration_share"):
            rows.append(
                _row(
                    metric,
                    value=None,
                    n=n_snaps,
                    ci=None,
                    gate_state="insufficient_snapshots",
                    note=note,
                    surface="app",
                )
            )
    return _frame(rows)


def _naive_50(candidates: pd.DataFrame, k: int) -> pd.DataFrame:
    active = candidates[candidates["candidate_status"].astype(str) == "active"].copy()
    active["_days"] = pd.to_numeric(active["days_until_expiration"], errors="coerce")
    active["_value"] = pd.to_numeric(active["potential_value"], errors="coerce")
    active = active.sort_values(["_days", "_value", "candidate_id"], ascending=[True, False, True], kind="mergesort")
    return active.head(k)


def _radar_50(candidates: pd.DataFrame, k: int) -> pd.DataFrame:
    pool = candidates[
        (candidates["candidate_status"].astype(str) == "active")
        & (candidates["priority_tier"].astype(str) != "Data Gap")
    ].copy()
    pool["_score"] = pd.to_numeric(pool["pursuit_score"], errors="coerce")
    pool = pool.sort_values(["_score", "candidate_id"], ascending=[False, True], kind="mergesort")
    return pool.head(k)


_FLAG_COLUMNS: tuple[str, ...] = (
    "flag_garbled_title",
    "flag_code_prefix",
    "flag_short_title",
    "flag_stale_expiration",
    "flag_missing_end_date",
)


def _flagged_share(rows: pd.DataFrame) -> float:
    flags = rows[list(_FLAG_COLUMNS)].astype(str).apply(lambda s: s.str.lower() == "true")
    return float(flags.any(axis=1).mean())


def baseline_rows(candidates: pd.DataFrame, bridge: pd.DataFrame, cfg: Mapping[str, object]) -> pd.DataFrame:
    """Descriptive naive-vs-radar top-K comparison (surface="internal"; never an
    accuracy claim). naive_50: active sorted by days_until_expiration asc, ties
    potential_value desc then candidate_id. radar_50: active non-Data-Gap sorted by
    pursuit_score desc, ties candidate_id."""
    k = _cfg_int(_section(cfg, "outcome_labels"), "top_k")
    if candidates.empty:
        return _frame(
            [
                _row(
                    m,
                    value=None,
                    n=0,
                    ci=None,
                    gate_state="not_yet_measured",
                    note="no candidates in the prepared frame",
                    surface="internal",
                )
                for m in (
                    "baseline_overlap_at_50",
                    "baseline_naive_flagged_rows",
                    "baseline_radar_flagged_rows",
                    "baseline_naive_linked_rows",
                    "baseline_radar_linked_rows",
                )
            ]
        )
    naive = _naive_50(candidates, k)
    radar = _radar_50(candidates, k)
    linked_ids = (
        set(bridge.loc[bridge["link_confidence"].astype(str).isin(("High", "Medium")), "candidate_id"].astype(str))
        if len(bridge)
        else set()
    )

    def linked_share(rows: pd.DataFrame) -> float:
        return float(rows["candidate_id"].astype(str).isin(linked_ids).mean())

    overlap = len(set(naive["candidate_id"].astype(str)) & set(radar["candidate_id"].astype(str)))
    rows_out = [
        _row(
            "baseline_overlap_at_50",
            value=overlap / k,
            n=k,
            ci=None,
            gate_state="published",
            note=_BASELINE_NOTE,
            surface="internal",
        ),
        _row(
            "baseline_naive_flagged_rows",
            value=_flagged_share(naive),
            n=len(naive),
            ci=None,
            gate_state="published",
            note=_BASELINE_NOTE,
            surface="internal",
        ),
        _row(
            "baseline_radar_flagged_rows",
            value=_flagged_share(radar),
            n=len(radar),
            ci=None,
            gate_state="published",
            note=_BASELINE_NOTE,
            surface="internal",
        ),
        _row(
            "baseline_naive_linked_rows",
            value=linked_share(naive),
            n=len(naive),
            ci=None,
            gate_state="published",
            note=_BASELINE_NOTE,
            surface="internal",
        ),
        _row(
            "baseline_radar_linked_rows",
            value=linked_share(radar),
            n=len(radar),
            ci=None,
            gate_state="published",
            note=_BASELINE_NOTE,
            surface="internal",
        ),
    ]
    return _frame(rows_out)
