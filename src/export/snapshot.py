"""
snapshot — retain each pipeline run's star schema.

The pipeline overwrites ``data/powerbi/`` every run, which loses the history the weekly
digest diffs against. ``archive_snapshot`` copies the star-schema exports into
``data/snapshots/<YYYY-MM-DD>/`` keyed on an explicit ``snapshot_date`` (so re-runs are
deterministic and idempotent). The snapshots root defaults to a sibling of the powerbi dir,
so patching the powerbi dir (e.g. in tests) carries the snapshot along with it.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

# Star-schema artifacts to retain (CSV + Parquet if present).
SNAPSHOT_GLOBS = ("*.csv", "*.parquet")
MANIFEST_NAME = "SNAPSHOT_MANIFEST.json"


def _date_str(snapshot_date: date | str) -> str:
    return snapshot_date.isoformat() if isinstance(snapshot_date, date) else str(snapshot_date)


def archive_snapshot(
    powerbi_dir: str | Path,
    snapshot_date: date | str,
    snapshots_root: str | Path | None = None,
) -> Path:
    """Copy the star-schema exports from ``powerbi_dir`` into
    ``<snapshots_root>/<snapshot_date>/`` and return that directory.

    Deterministic: the destination path and manifest are keyed on ``snapshot_date`` only (no
    wall-clock). Re-running the same date overwrites in place (idempotent). ``snapshots_root``
    defaults to ``<powerbi_dir>/../snapshots``.
    """
    powerbi = Path(powerbi_dir)
    root = Path(snapshots_root) if snapshots_root is not None else powerbi.parent / "snapshots"
    dest = root / _date_str(snapshot_date)
    dest.mkdir(parents=True, exist_ok=True)

    # Re-archiving a date is a clean replace: drop prior star-schema files first so a run
    # that emits fewer tables never leaves stale ones behind (the manifest stays truthful).
    for pattern in SNAPSHOT_GLOBS:
        for stale in dest.glob(pattern):
            stale.unlink()

    # SNAPSHOT_GLOBS is CSV/Parquet only, so the JSON manifest is never globbed as data —
    # no manifest guard needed here (test_manifest_not_re_copied_as_a_file locks that in).
    copied: list[str] = []
    for pattern in SNAPSHOT_GLOBS:
        for src in sorted(powerbi.glob(pattern)):
            shutil.copy2(src, dest / src.name)
            copied.append(src.name)

    manifest = {"snapshot_date": _date_str(snapshot_date), "files": sorted(copied)}
    (dest / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return dest
