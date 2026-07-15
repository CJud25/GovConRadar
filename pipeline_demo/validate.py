"""
validate.py — pipeline_demo stage 4 of 4. Integrity gate for the mini star schema.

Runs a SUBSET of the production invariants (scripts/validate_data.py) against the
demo output and EXITS NON-ZERO if any invariant fails. Same auditable Report style;
same "re-score must reproduce the baked score" contract. The subset covered here:

  1. Scorer parity      — re-score reproduces baked pursuit_score (max abs diff 0.0)
                          and priority_tier (100%% match).                [prod inv. 1]
  2. Quarantine         — no expired_stale / days<-90 row in Tiers 1-4.  [prod inv. 2]
  3. Bucket integrity   — known-bucket partition; no expired row in a forward bucket;
                          bucket<->days and bucket_sort consistency; days recomputable
                          from selected_expiration_date vs snapshot.     [prod inv. 3]
  4. KPI re-derivable   — headline KPIs recompute from the fact table.   [prod inv. 4]
  5. Quality            — garbled flags recompute; title_display never garbled / leaks
                          an IGF:: code.                                 [prod inv. 5]
  6. Schema contract    — required columns present per table.            [prod inv. 6]
  7. Snapshot/version/format — snapshot_date parseable; scorer_version matches; CSV
                          and Parquet agree where both ship.             [prod inv. 7]

Run:  python pipeline_demo/validate.py   # validates pipeline_demo/output/star/
"""

import sys
from pathlib import Path

import pandas as pd

DEMO_DIR = Path(__file__).resolve().parent
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

import transform  # noqa: E402  (sibling module; reuse its scorer + quality primitives)

STAR_DIR = DEMO_DIR / "output" / "star"

TIER_1_4 = {"Tier 1: Pursue Now", "Tier 2: Capture Research", "Tier 3: Monitor", "Tier 4: Low Priority"}

REQUIRED_COLUMNS = {
    "fact_recompete_candidates": [
        "candidate_id", "contract_title", "title_display", "days_until_expiration",
        "candidate_status", "expiration_bucket", "expiration_bucket_sort", "pursuit_score",
        "priority_tier", "selected_expiration_date", "total_obligated_amount",
        "flag_garbled_title", "agency_key", "naics_key",
    ],
    "dashboard_kpi_summary": [
        "total_estimated_pipeline_value", "recompete_candidate_count", "tier_1_count",
        "active_candidate_count", "active_pipeline_value", "snapshot_date", "scorer_version",
    ],
    "dim_agency": ["agency_key", "agency", "subagency", "active_candidate_count", "active_pipeline_value"],
    "dim_naics": ["naics_key", "naics", "candidate_count"],
}


class Report:
    def __init__(self):
        self.failures = []

    def check(self, name, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(f"{name}: {detail}")


def _load(target: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(target / f"{name}.csv", low_memory=False, encoding="utf-8")


def _bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.map(lambda v: str(v).strip().lower() in ("true", "1", "1.0"))


def validate(target: Path) -> list:
    print(f"\nValidating {target.relative_to(DEMO_DIR.parent)}")
    r = Report()
    cand = _load(target, "fact_recompete_candidates")
    kpi = _load(target, "dashboard_kpi_summary").iloc[0]

    # 6. Schema contract (first, so later checks can assume columns exist).
    for tbl, cols in REQUIRED_COLUMNS.items():
        df = _load(target, tbl)
        missing = [c for c in cols if c not in df.columns]
        r.check(f"schema:{tbl}", not missing, f"missing columns {missing}")

    r.check("fact is non-empty", len(cand) > 0, f"{len(cand)} rows")
    days = pd.to_numeric(cand["days_until_expiration"], errors="coerce")

    # 1. Scorer parity: re-score reproduces the baked pursuit_score / priority_tier.
    rescored = transform.score_frame(cand.copy())
    score_diff = (pd.to_numeric(rescored["pursuit_score"], errors="coerce")
                  - pd.to_numeric(cand["pursuit_score"], errors="coerce")).abs().max()
    r.check("parity:pursuit_score max abs diff == 0.0", float(score_diff) == 0.0, f"max diff {score_diff}")
    tier_mismatch = int((rescored["priority_tier"].values != cand["priority_tier"].values).sum())
    r.check("parity:priority_tier 100% match", tier_mismatch == 0, f"{tier_mismatch} mismatches")

    # 2. No stale / long-expired row in Tiers 1-4.
    tiered = cand[cand["priority_tier"].isin(TIER_1_4)]
    r.check("no expired_stale in Tiers 1-4", (tiered["candidate_status"] == "expired_stale").sum() == 0)
    stale_in_tiers = int((pd.to_numeric(tiered["days_until_expiration"], errors="coerce") < -90).sum())
    r.check("no days<-90 row in Tiers 1-4", stale_in_tiers == 0, f"{stale_in_tiers} rows")

    # 3. Bucket integrity.
    valid_buckets = set(transform.BUCKET_ORDER)
    r.check("buckets partition (all rows in a known bucket)", cand["expiration_bucket"].isin(valid_buckets).all())
    fwd = cand[cand["expiration_bucket"] != transform.BUCKET_ORDER[0]]
    r.check("no expired (days<0) row in a forward bucket",
            (pd.to_numeric(fwd["days_until_expiration"], errors="coerce") < 0).sum() == 0)
    expected_bucket = days.map(transform.derive_bucket)
    r.check("bucket <-> days consistency", (expected_bucket.values == cand["expiration_bucket"].values).all())
    expected_sort = cand["expiration_bucket"].map(transform.bucket_sort)
    r.check("bucket_sort consistency",
            (expected_sort.values == pd.to_numeric(cand["expiration_bucket_sort"]).values).all())
    snap = pd.Timestamp(kpi["snapshot_date"])
    recomputed_days = (pd.to_datetime(cand["selected_expiration_date"], errors="coerce") - snap).dt.days
    mask = recomputed_days.notna() & days.notna()
    day_mismatch = int((recomputed_days[mask].astype(int).values != days[mask].astype(int).values).sum())
    r.check("days_until_expiration matches snapshot recompute", day_mismatch == 0, f"{day_mismatch} rows")

    # 4. KPI values re-derivable from the fact table.
    active = cand[cand["candidate_status"] == "active"]
    r.check("kpi:recompete_candidate_count", int(kpi["recompete_candidate_count"]) == len(cand),
            f"{kpi['recompete_candidate_count']} vs {len(cand)}")
    r.check("kpi:active_candidate_count", int(kpi["active_candidate_count"]) == len(active),
            f"{kpi['active_candidate_count']} vs {len(active)}")
    r.check("kpi:tier_1_count",
            int(kpi["tier_1_count"]) == int((cand["priority_tier"] == "Tier 1: Pursue Now").sum()))
    r.check("kpi:expired_stale_count",
            int(kpi["expired_stale_count"]) == int((cand["candidate_status"] == "expired_stale").sum()))
    kpi_active_val = float(kpi["active_pipeline_value"])
    real_active_val = float(active["total_obligated_amount"].sum())
    r.check("kpi:active_pipeline_value within $1", abs(kpi_active_val - real_active_val) <= 1.0,
            f"{kpi_active_val} vs {real_active_val}")
    kpi_total = float(kpi["total_estimated_pipeline_value"])
    real_total = float(cand["total_obligated_amount"].sum())
    r.check("kpi:total_estimated_pipeline_value within $1", abs(kpi_total - real_total) <= 1.0,
            f"{kpi_total} vs {real_total}")

    # 5. Quality: flags recompute; title_display never garbled / leaks a code.
    recomputed_garbled = cand["contract_title"].map(transform.flag_garbled_title)
    r.check("no unflagged garbled titles",
            (recomputed_garbled.values == _bool_series(cand["flag_garbled_title"]).values).all())
    disp_garbled = int(cand["title_display"].map(transform.flag_garbled_title).sum())
    r.check("title_display never matches garbled pattern", disp_garbled == 0, f"{disp_garbled} rows")
    igf_leak = int(cand["title_display"].astype(str).str.contains("IGF::").sum())
    r.check("no IGF:: in any title_display", igf_leak == 0, f"{igf_leak} rows")

    # 7. Snapshot / version / format parity.
    r.check("snapshot_date present & parseable", pd.notna(pd.Timestamp(kpi["snapshot_date"])))
    r.check("scorer_version matches transform.SCORER_VERSION",
            str(kpi["scorer_version"]) == transform.SCORER_VERSION,
            f"{kpi['scorer_version']} vs {transform.SCORER_VERSION}")
    for pq in sorted(target.glob("*.parquet")):
        name = pq.stem
        csv = target / f"{name}.csv"
        if csv.exists():
            a = pd.read_csv(csv, low_memory=False, encoding="utf-8")
            b = pd.read_parquet(pq)
            equal = a.shape == b.shape and list(a.columns) == list(b.columns)
            r.check(f"csv==parquet:{name}", equal, f"shape/cols differ {a.shape} vs {b.shape}")

    return r.failures


def main(argv=None) -> int:
    if not (STAR_DIR / "fact_recompete_candidates.csv").exists():
        print(f"validate: no output found at {STAR_DIR}. Run the pipeline first "
              f"(python pipeline_demo/run_all.py --offline).", file=sys.stderr)
        return 1
    failures = validate(STAR_DIR)
    print()
    if failures:
        print(f"VALIDATION FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("VALIDATION PASSED — all mini-pipeline invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
