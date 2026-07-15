"""
build_reference_tables.py — Builds psc_lookup.csv and naics_lookup.csv.

PSC relevance flags are hand-curated against the FPDS Product and Service Code
Manual (https://www.acquisition.gov/psc-manual) for IT/cyber/telecom relevance.
Descriptions for every other code come from the committed manual vintages under
data/reference/psc_manual/ (see build_psc_lookup); codes in neither source keep
the powerbi_export auto-add path. verification_status flags which relevance
calls have and have not been re-checked against the live manual; re-verify
before using in a real client-facing deliverable.
"""

from pathlib import Path

import pandas as pd

from utils.config import NAICS_ALWAYS_RELEVANT, NAICS_SEED

PSC_LOOKUP = [
    # (psc_code, psc_description, psc_group, cyber_it_relevance_flag, cyber_it_relevance_reason, verification_status)
    ("D302", "IT Systems Development Services", "IT and Telecom", True, "Core software/systems development PSC", "verified_against_manual"),
    ("D307", "IT Strategy and Architecture Services", "IT and Telecom", True, "IT architecture/consulting PSC", "verified_against_manual"),
    ("D310", "IT Data Entry Services", "IT and Telecom", True, "Data services PSC", "needs_manual_verification"),
    ("D316", "IT Network Management Services", "IT and Telecom", True, "Network operations PSC", "verified_against_manual"),
    ("D317", "IT Programming Services", "IT and Telecom", True, "Software programming PSC", "verified_against_manual"),
    ("D399", "Other IT and Telecommunications", "IT and Telecom", True, "Catch-all IT/telecom PSC used broadly by DoD IT contracts", "verified_against_manual"),
    ("DA01", "IT Systems Analysis Services (National Security)", "IT and Telecom", True, "National-security-flagged IT PSC", "needs_manual_verification"),
    ("DF01", "IT Cyber Security and Data Backup Services", "IT and Telecom", True, "Explicit cybersecurity PSC", "verified_against_manual"),
    ("DH01", "IT Cloud Computing Services", "IT and Telecom", True, "Cloud services PSC", "needs_manual_verification"),
    ("R425", "Engineering and Technical Services", "Professional/Support Services", False, "Broad engineering PSC — only cyber/IT-relevant with a keyword match", "needs_manual_verification"),
    ("R408", "Program Management/Support Services", "Professional/Support Services", False, "Broad program-management PSC — only cyber/IT-relevant with a keyword match", "needs_manual_verification"),
    ("1260", "Fire Control Systems Equipment", "Product", False, "Weapons-system PSC, not IT/cyber", "verified_against_manual"),
]

PSC_COLUMNS = [
    "psc_code", "psc_description", "psc_group",
    "cyber_it_relevance_flag", "cyber_it_relevance_reason", "verification_status",
]

# The committed manual vintages' CSV schema (see data/reference/psc_manual/*.csv).
MANUAL_COLUMNS = ["psc_code", "psc_description", "psc_group", "start_date", "end_date", "vintage_label"]
MANUAL_ONLY_REASON = "Described from the GSA PSC manual; not in the curated cyber/IT-relevant list"


def _read_manual_csv(path: Path) -> pd.DataFrame:
    """Reads one committed manual vintage, skipping its leading `#` provenance
    comment lines (a bare comment= arg would also strip `#` inside descriptions)."""
    with open(path, encoding="utf-8") as f:
        skip = 0
        for line in f:
            if not line.startswith("#"):
                break
            skip += 1
    df = pd.read_csv(path, skiprows=skip, dtype=str, encoding="utf-8").fillna("")
    missing = [c for c in MANUAL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"PSC manual CSV {path.name} is missing columns {missing}")
    df["psc_code"] = df["psc_code"].str.strip().str.upper()
    dupes = df.loc[df["psc_code"].duplicated(), "psc_code"].tolist()
    if dupes:
        raise ValueError(f"PSC manual CSV {path.name} carries duplicate psc_code(s): {sorted(set(dupes))}")
    # A manual row without the manual's own description contributes nothing —
    # descriptions are never fabricated, so such rows are dropped, not defaulted.
    return df[df["psc_description"].str.strip() != ""]


def build_psc_lookup(manual_dir: Path | None = None) -> pd.DataFrame:
    """The curated 12-row relevance list, unioned with every committed GSA PSC
    manual vintage found in manual_dir (default: utils.config.PSC_MANUAL_DIR,
    resolved at call time). Missing/empty dir -> curated-12-only, exactly the
    pre-manual behavior. Union rules, all deterministic:
      1. newest vintage wins per psc_code (vintage_label sorts lexicographically);
      2. curated rows ALWAYS keep their curated cyber_it_relevance_flag / reason /
         verification_status, adopting the manual's official psc_description when
         the manual carries the code;
      3. manual-only rows get flag=False + the described_from_manual status —
         classification (which reads only the flag) is unchanged by construction;
      4. psc_code is asserted unique post-union.
    Output columns are additive: PSC_COLUMNS + psc_manual_vintage ("" for
    curated-only rows and for the powerbi_export auto-add path downstream).
    """
    if manual_dir is None:
        from utils.config import PSC_MANUAL_DIR  # call-time resolution (import-walk-safe, patchable)
        manual_dir = PSC_MANUAL_DIR

    curated = pd.DataFrame(PSC_LOOKUP, columns=PSC_COLUMNS)
    curated["psc_manual_vintage"] = ""

    manual_dir = Path(manual_dir)
    vintage_files = sorted(manual_dir.glob("*.csv")) if manual_dir.is_dir() else []
    if not vintage_files:
        return curated

    frames = [_read_manual_csv(p) for p in vintage_files]
    manual = pd.concat(frames, ignore_index=True)
    # Newest vintage wins per code: stable-sort by vintage_label, keep the last.
    manual = manual.sort_values("vintage_label", kind="mergesort").drop_duplicates("psc_code", keep="last")

    curated_codes = set(curated["psc_code"])
    official = manual.set_index("psc_code")["psc_description"]
    adopted = curated["psc_code"].map(official)
    curated["psc_description"] = adopted.where(adopted.notna(), curated["psc_description"])

    manual_only = manual[~manual["psc_code"].isin(curated_codes)].copy()
    manual_only = pd.DataFrame({
        "psc_code": manual_only["psc_code"],
        "psc_description": manual_only["psc_description"],
        "psc_group": manual_only["psc_group"],
        "cyber_it_relevance_flag": False,
        "cyber_it_relevance_reason": MANUAL_ONLY_REASON,
        "verification_status": "described_from_manual",
        "psc_manual_vintage": manual_only["vintage_label"],
    })

    union = pd.concat(
        [curated, manual_only.sort_values("psc_code", kind="mergesort")], ignore_index=True
    )
    dupes = union.loc[union["psc_code"].duplicated(), "psc_code"].tolist()
    if dupes:
        raise ValueError(f"psc_lookup union produced duplicate psc_code(s): {sorted(set(dupes))}")
    return union


def build_naics_lookup() -> pd.DataFrame:
    rows = []
    for code, description in NAICS_SEED.items():
        always_relevant = code in NAICS_ALWAYS_RELEVANT
        rows.append({
            "naics_code": code,
            "naics_description": description,
            "cyber_it_relevance_flag": always_relevant,
            "cyber_it_relevance_reason": (
                "Unambiguously IT/cyber by NAICS definition" if always_relevant
                else "Ambiguous NAICS — requires a cyber/IT keyword match in the award description"
            ),
        })
    return pd.DataFrame(rows)


def write_reference_tables(out_dir: Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    build_psc_lookup().to_csv(out_dir / "psc_lookup.csv", index=False)
    build_naics_lookup().to_csv(out_dir / "naics_lookup.csv", index=False)
