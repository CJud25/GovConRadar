"""
run_all.py — pipeline_demo orchestrator: extract -> transform -> load -> validate.

Mirrors the production run_pipeline.py at tiny, deterministic scale. With --offline
(the default demo/CI mode) it reads a committed 100%%-synthetic fixture and touches no
network, so a CI run is fully reproducible. Designed to finish in well under 60 seconds.

Run:
  python pipeline_demo/run_all.py --offline   # deterministic, network-free (CI mode)
  python pipeline_demo/run_all.py --online    # best-effort live USAspending pull (not CI)
"""

import argparse
import sys
import time
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

import load  # noqa: E402
import validate  # noqa: E402

import extract  # noqa: E402
import transform  # noqa: E402

TIME_BUDGET_SECONDS = 60


def run(offline: bool = True) -> int:
    t0 = time.perf_counter()
    mode = "offline (committed synthetic fixture)" if offline else "online (live USAspending)"
    print(f"pipeline_demo — extract -> transform -> load -> validate  [{mode}]\n")

    t = time.perf_counter()
    records = extract.run_extract(offline=offline)
    print(f"[1/4] extract   : {len(records)} raw award records ({time.perf_counter() - t:.2f}s)")

    t = time.perf_counter()
    fact = transform.run_transform(records)
    tier1 = int((fact["priority_tier"] == "Tier 1: Pursue Now").sum())
    gap = int((fact["priority_tier"] == "Data Gap").sum())
    print(f"[2/4] transform : {len(fact)} recompete candidates "
          f"({tier1} Tier 1, {gap} Data Gap) ({time.perf_counter() - t:.2f}s)")

    t = time.perf_counter()
    result = load.run_load(fact)
    print(f"[3/4] load      : star schema -> {result['output_dir'].relative_to(DEMO_DIR.parent)}/ "
          f"(fact={result['fact_recompete_candidates']}, dim_agency={result['dim_agency']}, "
          f"dim_naics={result['dim_naics']}, kpi={result['dashboard_kpi_summary']}) "
          f"({time.perf_counter() - t:.2f}s)")

    t = time.perf_counter()
    failures = validate.validate(load.STAR_DIR)
    print(f"[4/4] validate  : {len(failures)} failure(s) ({time.perf_counter() - t:.2f}s)")

    elapsed = time.perf_counter() - t0
    print()
    if failures:
        print(f"PIPELINE FAILED — {len(failures)} invariant(s) violated:")
        for f in failures:
            print(f"  - {f}")
        return 1
    within = "OK" if elapsed < TIME_BUDGET_SECONDS else f"OVER {TIME_BUDGET_SECONDS}s BUDGET"
    print(f"PIPELINE PASSED — all invariants hold. Total {elapsed:.2f}s "
          f"(budget {TIME_BUDGET_SECONDS}s: {within}).")
    return 0 if elapsed < TIME_BUDGET_SECONDS else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the pipeline_demo end to end.")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--offline", action="store_true", help="deterministic, network-free (default)")
    grp.add_argument("--online", action="store_true", help="best-effort live USAspending pull (not for CI)")
    args = ap.parse_args(argv)
    return run(offline=not args.online)


if __name__ == "__main__":
    sys.exit(main())
