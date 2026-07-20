"""
daily_pull.py — one-shot daily pull of raw data from BOTH public sources
(USAspending.gov awards + SAM.gov opportunity notices).

This is a thin, runnable wrapper around the extraction layer that already ships
in this repo (src/extract/awards.run_extraction). It performs a SINGLE pull and
writes timestamped raw JSON envelopes; a scheduler (GitHub Actions cron, systemd
timer, Windows Task Scheduler, plain cron) is what makes it "daily". This script
is intentionally a starting point — see docs/daily_pulls.md for the full runbook,
the source-freshness caveats, and how to wire a scheduler around it.

What it does NOT do (by design — see docs/daily_pulls.md):
  * It does not transform/rebake/validate or rebuild the app's snapshot. That
    driver (run_pipeline.py / rebake_data.py) lives in the private project repo,
    not here. This script only produces the raw pull envelopes.
  * It does not commit or publish anything. Where the raw pulls should land
    (git-ignored dir, build artifact, release asset) is a decision left to you.

Source selection is intentionally "both": run_extraction pulls awards and notices
together, preferring the local bulk exports (no key, no rate limits) and falling
back to the live APIs when a bulk file is absent — exactly as a manual refresh does.
  * USAspending: keyless live API fallback (reliable).
  * SAM.gov: live API needs SAM_GOV_API_KEY and api.sam.gov was unreachable from
    the original build sandbox — so the dependable daily path is the public
    "Contract Opportunities" bulk CSV (published daily). Point sources.yaml at a
    freshly-downloaded copy before each run for a true daily SAM pull.

Usage:
  py scripts/daily_pull.py                     # -> data/raw/ (default)
  py scripts/daily_pull.py --out data/raw      # explicit output dir
  py scripts/daily_pull.py --date 2026-07-20   # override the pull's "today"
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from extract.awards import run_extraction  # noqa: E402 — needs the sys.path insert above

logger = logging.getLogger("daily_pull")


def main() -> int:
    ap = argparse.ArgumentParser(description="One-shot daily raw pull from USAspending + SAM.gov.")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "raw",
                    help="destination dir for the raw pull envelopes (default: data/raw/)")
    ap.add_argument("--date", dest="pull_date", default=None,
                    help="override the pull's reference date (YYYY-MM-DD); default is today")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    today = date.fromisoformat(args.pull_date) if args.pull_date else date.today()
    out_dir = args.out.resolve()

    logger.info(f"Daily pull starting — reference date {today.isoformat()}, output {out_dir}")
    result = run_extraction(data_raw_dir=out_dir, today=today)

    logger.info(
        "Daily pull complete — "
        f"USAspending: {result['search_count']} awards ({result['detail_count']} detailed), "
        f"SAM.gov: {result['sam_count']} notices."
    )
    logger.info(f"  awards search  -> {result['search_raw_path']}")
    logger.info(f"  awards detail  -> {result['detail_raw_path']}")
    logger.info(f"  SAM notices    -> {result['sam_raw_path']}")
    if result.get("transactions_digest_path"):
        logger.info(f"  mods digests   -> {result['transactions_digest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
