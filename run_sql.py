#!/usr/bin/env python
"""run_sql.py — run a query from the sql/ pack against the Recompete Radar star
schema with DuckDB.

    py run_sql.py sql/<file>.sql [--csv]

The star schema is loaded exactly the way the Streamlit app resolves it
(mirrors streamlit_app/components/data.py::resolve_data_dir), so this works on a
fresh clone that only has the committed sample:

    $RADAR_DATA_DIR  ->  data/powerbi/ (full snapshot, if present locally)
                     ->  data/sample/  (committed seeded subsample — the default)
                     ->  streamlit_app/assets/sample_data/ (legacy synthetic bundle)

Every table file in the resolved directory is registered as a DuckDB view
(Parquet preferred over CSV when both exist), the SQL file is executed, and the
result is pretty-printed — or emitted as CSV to stdout with --csv.

Diagnostics (which data dir / mode was used) go to stderr, so `--csv` stdout
stays clean and pipeable.
"""

import argparse
import os
import sys
from pathlib import Path

import duckdb

# DuckDB's pretty-printer emits Unicode box-drawing characters; force UTF-8 so the
# table renders on the Windows console (default cp1252) instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parent
# Mirror resolve_data_dir()'s search order. The full snapshot (data/powerbi/) is
# NOT committed (data DIET); a fresh clone runs on data/sample/.
LIVE_DIR = REPO_ROOT / "data" / "powerbi"
DEFAULT_SAMPLE_DIR = REPO_ROOT / "data" / "sample"
LEGACY_SAMPLE_DIR = REPO_ROOT / "streamlit_app" / "assets" / "sample_data"
PRIMARY = "fact_recompete_candidates"


def _usable(d: Path) -> bool:
    """A data dir is usable if its primary fact table is present and non-empty
    as either Parquet or CSV."""
    for ext in ("parquet", "csv"):
        p = d / f"{PRIMARY}.{ext}"
        if p.exists() and p.stat().st_size > 0:
            return True
    return False


def resolve_data_dir() -> tuple[Path, str]:
    """Resolve the star-schema data directory and a short mode label, in the same
    order as streamlit_app/components/data.py::resolve_data_dir."""
    env = os.environ.get("RADAR_DATA_DIR")
    if env:
        return Path(env), "custom"
    if _usable(LIVE_DIR):
        return LIVE_DIR, "live"
    if _usable(DEFAULT_SAMPLE_DIR):
        return DEFAULT_SAMPLE_DIR, "sample"
    if _usable(LEGACY_SAMPLE_DIR):
        return LEGACY_SAMPLE_DIR, "sample"
    raise SystemExit(
        "run_sql.py: no usable star-schema data dir found. Expected "
        f"{PRIMARY}.(parquet|csv) under one of: $RADAR_DATA_DIR, "
        f"{LIVE_DIR}, {DEFAULT_SAMPLE_DIR}, {LEGACY_SAMPLE_DIR}. "
        "On a fresh clone the committed data/sample/ should be present."
    )


def register_views(con: duckdb.DuckDBPyConnection, data_dir: Path) -> list[str]:
    """Register every table file in data_dir as a DuckDB view. Parquet wins over
    CSV when both are present (Parquet carries dtypes; CSV needs sniffing)."""
    registered: dict[str, str] = {}
    # Parquet first so it is chosen over a same-named CSV.
    for p in sorted(data_dir.glob("*.parquet")) + sorted(data_dir.glob("*.csv")):
        name = p.stem
        if name in registered:
            continue
        path_lit = str(p.resolve()).replace("'", "''")
        if p.suffix == ".parquet":
            reader = f"read_parquet('{path_lit}')"
        else:
            reader = f"read_csv_auto('{path_lit}', header=true, sample_size=-1)"
        con.execute(f'CREATE OR REPLACE VIEW "{name}" AS SELECT * FROM {reader}')
        registered[name] = p.suffix.lstrip(".")
    return sorted(registered)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="run_sql.py",
        description="Run a sql/ query against the Recompete Radar star schema with DuckDB.",
    )
    ap.add_argument("sql_file", help="path to a .sql file (e.g. sql/01_recompete_expiring_next_12mo_by_naics.sql)")
    ap.add_argument("--csv", action="store_true", help="emit CSV to stdout instead of a pretty table")
    ap.add_argument("--max-rows", type=int, default=200, help="max rows to pretty-print (ignored with --csv)")
    args = ap.parse_args(argv)

    sql_path = Path(args.sql_file)
    if not sql_path.exists():
        ap.error(f"SQL file not found: {sql_path}")
    if sql_path.suffix.lower() != ".sql":
        ap.error(f"expected a .sql file, got: {sql_path}")

    sql_text = sql_path.read_text(encoding="utf-8").strip()
    if not sql_text:
        ap.error(f"SQL file is empty: {sql_path}")

    data_dir, mode = resolve_data_dir()
    if not data_dir.exists():
        ap.error(f"resolved data dir does not exist: {data_dir} (mode={mode})")

    con = duckdb.connect(database=":memory:")
    try:
        tables = register_views(con, data_dir)
        print(
            f"[run_sql] {sql_path.name}  |  data={data_dir}  (mode={mode})  |  {len(tables)} tables registered",
            file=sys.stderr,
        )
        try:
            result = con.sql(sql_text)
        except duckdb.Error as exc:
            print(f"[run_sql] query failed in {sql_path.name}:\n{exc}", file=sys.stderr)
            return 1

        if args.csv:
            result.df().to_csv(sys.stdout, index=False)
        else:
            result.show(max_rows=args.max_rows, max_width=200)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
