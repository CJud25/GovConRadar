"""
download_data.py — fetch the FULL published star-schema snapshot (~476 MB as CSV;
ships as a much smaller Parquet zip) that is intentionally NOT committed as part of
the data DIET. The app and tests fall back to the committed data/sample/ subsample
when this is absent; run this to pull the full dataset for local full-data work.
The published snapshot excludes fact_contract_awards.description_raw /
classification_reason (the 2026-07-06 public-artifact security decision).

The snapshot ships as Parquet inside a single .zip asset on a GitHub Release. This
script downloads + extracts it into data/powerbi/ and, because the Streamlit app
and scripts/validate_data.py read CSV, writes a CSV sibling next to any Parquet that
does not already have one. Nothing here is committed — data/powerbi/ is git-ignored.

Every asset built by scripts/build_release.py carries a provenance manifest.json
(SHA-256 + row count per table). Extraction verifies each member against it and
refuses an asset without one — an unverifiable snapshot never lands quietly.

Usage:  py scripts/download_data.py [--dest data/powerbi] [--url URL]
"""
import argparse
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pyarrow_parquet

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "data" / "powerbi"

# Published GitHub Release asset: a .zip of the 16-table Parquet star schema.
# Override with --url. NOTE: the published snapshot EXCLUDES
# fact_contract_awards.description_raw/classification_reason, but the local Power BI
# model (powerbi/*.tmdl) binds both columns — a Release-fetched data/powerbi/ can feed
# the app + validator but NOT a Power BI refresh; use a locally-baked full export for
# Power BI.
# data-snapshot-2026-07-15 is the CURRENT release tag, cut on the clean-history republish.
# Earlier tags are gone by design: data-snapshot-2026-07-07 pointed at a pre-redaction commit
# that still exposed personnel titles by SHA (security review 2026-07-13), and
# data-snapshot-2026-07-13 was superseded when the repo history was recreated as a single
# clean commit (delete+recreate is GitHub's only true purge). Neither old tag is ever re-pushed.
SNAPSHOT_URL = "https://github.com/CJud25/GovConRadar/releases/download/data-snapshot-2026-07-15/powerbi-snapshot.zip"


def verify_extracted(manifest: dict, dest: Path) -> list[str]:
    """Checks every manifest table against the extracted copy in dest — presence,
    SHA-256 of the parquet bytes, and row count via the parquet metadata. Returns
    failure strings (expected vs actual); an empty list is a pass. Pure: reads
    only manifest + dest, no network, no clock."""
    failures = []
    for table, meta in sorted(manifest.get("tables", {}).items()):
        pq = Path(dest) / f"{table}.parquet"
        if not pq.exists():
            failures.append(f"{table}: missing from the extracted snapshot")
            continue
        actual_sha = hashlib.sha256(pq.read_bytes()).hexdigest()
        if actual_sha != meta.get("sha256"):
            failures.append(f"{table}: sha256 mismatch (expected {meta.get('sha256')}, got {actual_sha})")
        actual_rows = int(pyarrow_parquet.read_metadata(pq).num_rows)
        if actual_rows != int(meta.get("rows", -1)):
            failures.append(f"{table}: row count mismatch (expected {meta.get('rows')}, got {actual_rows})")
    return failures


def extract_verified(blob: bytes, dest: Path) -> int:
    """Extracts a release zip into dest, verifying against its embedded
    manifest.json: the manifest is read FIRST (an asset without one is refused),
    each parquet member's sha256 is checked BEFORE its bytes are written, and the
    extracted copy is re-verified (sha + row counts). Returns the verified table
    count; any failure is a SystemExit listing every failing table."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if "manifest.json" not in {Path(n).name for n in names}:
            raise SystemExit("asset has no provenance manifest — rebuild the release with build_release.py")
        manifest = json.loads(zf.read(next(n for n in names if Path(n).name == "manifest.json")))
        tables = manifest.get("tables", {})

        # Pass 1 — verify EVERY member in-archive; nothing is written until all pass,
        # so a mid-zip failure can never leave dest as a mixed old/new snapshot.
        pre_failures = []
        for member in names:
            name = Path(member).name
            if not name.endswith(".parquet"):
                continue
            expected = tables.get(Path(name).stem, {}).get("sha256")
            if expected is None:
                pre_failures.append(f"{Path(name).stem}: not in the manifest")
                continue
            actual = hashlib.sha256(zf.read(member)).hexdigest()
            if actual != expected:
                pre_failures.append(f"{Path(name).stem}: sha256 mismatch (expected {expected}, got {actual})")
        if pre_failures:
            raise SystemExit("manifest verification FAILED before extraction:\n  " + "\n  ".join(pre_failures))

        # Pass 2 — all verified; write.
        for member in names:
            (dest / Path(member).name).write_bytes(zf.read(member))

    failures = verify_extracted(manifest, dest)
    if failures:
        raise SystemExit("manifest verification FAILED on the extracted snapshot:\n  " + "\n  ".join(failures))
    print(f"verified {len(tables)} tables (sha256 + row counts) against manifest")
    return len(tables)


def download(url: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    print(f"Downloading snapshot from {url} ...")
    with urllib.request.urlopen(url) as resp:  # trusted GitHub Release asset URL
        blob = resp.read()
    print(f"  fetched {len(blob):,} bytes")

    n_tables = extract_verified(blob, dest)
    print(f"  extracted {n_tables} verified table(s) + manifest.json into {dest}")

    # The app + validator read CSV; materialize a CSV next to any Parquet-only table.
    made = 0
    for pq in sorted(dest.glob("*.parquet")):
        csv = dest / f"{pq.stem}.csv"
        if not csv.exists():
            pd.read_parquet(pq).to_csv(csv, index=False, encoding="utf-8")
            made += 1
    if made:
        print(f"  wrote {made} CSV sibling(s) for Parquet-only tables")
    print(f"Done. Full snapshot is in {dest} (git-ignored; not committed).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Download the full (non-committed) star-schema snapshot.")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                    help="destination directory (default: data/powerbi/)")
    ap.add_argument("--url", default=SNAPSHOT_URL,
                    help="snapshot .zip URL (defaults to the published Release asset)")
    args = ap.parse_args()

    download(args.url, args.dest.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
