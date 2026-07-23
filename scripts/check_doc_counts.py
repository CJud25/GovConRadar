"""
check_doc_counts.py — assert pinned prose numbers against generated values.

Prose drifts; data regenerates. This guard greps the pinned numbers out of
README/docs and compares each to the value the shipped artifacts actually
produce, mirroring validate_data.py's conventions: [PASS]/[FAIL] per pin,
[SKIP] when a pin's file/pattern/source is absent in this tree, exit non-zero
on any FAIL. It only reads — it can never weaken the validator.

Usage:  py scripts/check_doc_counts.py [--data <dir>]
        Default checks sample-scoped pins only (full-data pins SKIP);
        the deploy SOP runs it with --data data/powerbi after every rebake.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

# cp1252 Windows consoles cannot print the '≥' inside pin labels — reconfigure the
# print edge (same fix the digest CLI needed; harmless where stdout is already UTF-8).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]

# Pin registry: (relative file, regex with ONE capture group, source key, scope).
# scope "sample" pins check on every run; "full" pins SKIP unless --data is given.
# (S20 appends the measurement-threshold pins here.)
PINS = [
    ("README.md", r"(\d+) data-integrity checks", "validator_sample_pass_count", "sample"),
    ("README.md", r"([\d,]+)-row sample", "sample_row_count", "sample"),
    ("docs/DEMO_SCRIPT.md", r"([\d,]+)-candidate seeded", "sample_row_count", "sample"),
    ("README.md", r"([\d,]+) active recompete candidates", "active_candidate_count", "full"),
    ("README.md", r"plus \*\*([\d,]+) recently-expired", "expired_grace_count", "full"),
    ("README.md", r"and \*\*([\d,]+) historical/long-expired", "expired_stale_count", "full"),
    ("README.md", r"([\d,]+) contract vehicles", "vehicle_count", "full"),
    ("README.md", r"Data-Gap \*tier\* holds ([\d,]+) rows", "data_gap_tier_rows", "full"),
    ("README.md", r"public snapshot \((\d{4}-\d{2}-\d{2})", "snapshot_date", "full"),
    ("docs/DEMO_SCRIPT.md", r"the full (\d{4}-\d{2}-\d{2}) snapshot", "snapshot_date", "full"),
    ("docs/DEMO_SCRIPT.md", r"snapshot: ([\d,]+) active", "active_candidate_count", "full"),
    ("docs/DEMO_SCRIPT.md", r"([\d,]+) quarantined", "expired_stale_count", "full"),
    # Private-tree DEMO_SCRIPT phrasing (the deploy copy uses the three pins above;
    # each pattern is tree-discriminating so the other tree's copy SKIPs, never false-fails):
    ("docs/DEMO_SCRIPT.md", r"headline: \*\*~([\d,]+) active candidates", "active_candidate_count", "full"),
    ("docs/DEMO_SCRIPT.md", r"\*([\d,]+) expired records", "expired_stale_count", "full"),
    ("docs/DEMO_SCRIPT.md", r"across ([\d,]+) vehicles", "vehicle_count", "full"),
    ("docs/methodology_notes.md", r"measured: ([\d,]+) of", "linked_candidate_count", "full"),
    ("docs/methodology_notes.md", r"of ([\d,]+); the app", "recompete_candidate_count", "full"),
    ("docs/methodology_notes.md", r"notice on the (\d{4}-\d{2}-\d{2}) snapshot", "snapshot_date", "full"),
    # S18: the README "How we compare" restatement of the same coverage figure wasn't
    # pinned — without this it could silently drift out of sync with methodology_notes.md.
    ("README.md", r"dropped \*\*4,163 → ([\d,]+)\*\*", "linked_candidate_count", "full"),
    # Measurement-threshold pins (S20): the README promise must state the SAME floors
    # config/measurement.yaml pins — prose drift here would misstate the publication gate.
    ("README.md", r"≥(\d+) labels per link tier", "min_labels_per_tier", "sample"),
    ("README.md", r"≥(\d+) determinable outcome labels", "min_determinable_for_precision", "sample"),
]

# The data-snapshot release-tag sweep: every tag in README.md + docs/ must equal
# the tag inside download_data.SNAPSHOT_URL. Scope pinned: NOT CHANGELOG.md
# (records superseded tags) and NOT scripts/download_data.py (whose comment
# narrates tag history and whose constant IS the source of truth).
TAG_RE = re.compile(r"data-snapshot-(\d{4}-\d{2}-\d{2})")


def _num(text: str) -> str:
    return text.replace(",", "")


def build_sources(data_dir: Path | None) -> dict:
    src: dict[str, str] = {}
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from utils.config import MEASUREMENT

        src["min_labels_per_tier"] = str(MEASUREMENT["link_labels"]["min_labels_per_tier"])
        src["min_determinable_for_precision"] = str(
            MEASUREMENT["outcome_labels"]["min_determinable_for_precision"])
    except Exception:
        pass  # a tree without src/ or the YAML SKIPs the threshold pins honestly
    sample_fact = ROOT / "data" / "sample" / "fact_recompete_candidates.csv"
    if sample_fact.exists():
        src["sample_row_count"] = str(len(pd.read_csv(sample_fact, usecols=[0], low_memory=False)))
    validator = ROOT / "scripts" / "validate_data.py"
    if validator.exists() and sample_fact.exists():
        out = subprocess.run([sys.executable, str(validator), "--sample"],
                             capture_output=True, text=True, cwd=ROOT)
        src["validator_sample_pass_count"] = str(out.stdout.count("[PASS]"))
    if data_dir is not None:
        kpi = pd.read_csv(data_dir / "dashboard_kpi_summary.csv").iloc[0]
        for key in ("active_candidate_count", "expired_grace_count", "expired_stale_count",
                    "vehicle_count", "recompete_candidate_count"):
            src[key] = str(int(kpi[key]))
        src["snapshot_date"] = str(kpi["snapshot_date"])
        tiers = pd.read_csv(data_dir / "fact_recompete_candidates.csv",
                            usecols=["priority_tier"], low_memory=False)["priority_tier"]
        src["data_gap_tier_rows"] = str(int((tiers == "Data Gap").sum()))
        bridge = pd.read_csv(data_dir / "bridge_award_opportunity_links.csv",
                             usecols=["candidate_id", "link_confidence"], low_memory=False)
        src["linked_candidate_count"] = str(
            bridge.loc[bridge["link_confidence"] != "No Match", "candidate_id"].nunique())
    return src


def main() -> int:
    ap = argparse.ArgumentParser(description="Assert pinned prose numbers against generated values.")
    ap.add_argument("--data", type=Path, default=None,
                    help="full-data dir (e.g. data/powerbi) — enables the full-data pins")
    args = ap.parse_args()

    sources = build_sources(args.data)
    failures = 0

    for rel, pattern, key, scope in PINS:
        label = f"{rel} ~ /{pattern}/"
        path = ROOT / rel
        if not path.exists():
            print(f"[SKIP] {label} — file absent in this tree")
            continue
        if scope == "full" and args.data is None:
            print(f"[SKIP] {label} — full-data pin (--data not given)")
            continue
        if key not in sources:
            print(f"[SKIP] {label} — source '{key}' not measurable in this tree")
            continue
        matches = re.findall(pattern, path.read_text(encoding="utf-8"))
        if not matches:
            print(f"[SKIP] {label} — pattern not present")
            continue
        expected = _num(sources[key])
        bad = [m for m in matches if _num(m) != expected]
        if bad:
            print(f"[FAIL] {label} — pinned {bad} != generated {expected} (source: {key})")
            failures += 1
        else:
            print(f"[PASS] {label} — {len(matches)} occurrence(s) == {expected}")

    # Release-tag sweep (sample-scoped: the constant ships with every tree).
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import download_data
        url_tag = TAG_RE.search(download_data.SNAPSHOT_URL).group(1)
        tag_files = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("**/*.md"))
        stray = []
        for f in tag_files:
            if not f.exists():
                continue
            for m in TAG_RE.finditer(f.read_text(encoding="utf-8")):
                if m.group(1) != url_tag:
                    stray.append(f"{f.relative_to(ROOT)}: data-snapshot-{m.group(1)}")
        if stray:
            print(f"[FAIL] release-tag sweep — tags != SNAPSHOT_URL's {url_tag}: {stray}")
            failures += 1
        else:
            print(f"[PASS] release-tag sweep — every README/docs tag == {url_tag}")
    except ModuleNotFoundError:
        print("[SKIP] release-tag sweep — scripts/download_data.py absent in this tree")

    if failures:
        print(f"\nDOC-COUNT GUARD FAILED — {failures} pin(s) drifted from generated values.")
        return 1
    print("\nDoc-count guard: all present pins match generated values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
