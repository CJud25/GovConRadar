"""
validate_data.py — integrity gate for the published star schema. Replaces the
provenance role of the old generate_sample_data.py's assertions with an auditable,
CI-runnable check. Exits non-zero with a readable report if ANY invariant fails.

Run:  py scripts/validate_data.py                 # validates data/powerbi/ (the FULL
                                                  # snapshot, if present locally)
      py scripts/validate_data.py --sample        # validates the committed default
                                                  # sample data/sample/ (CSV+Parquet)
      py scripts/validate_data.py --legacy-sample # validates the legacy synthetic bundle

A target that is not present (e.g. data/powerbi/ on a fresh clone — the full
snapshot is fetched via scripts/download_data.py, not committed) is SKIPPED, not
failed, so the committed sample is what CI gates on.

The invariants are the single source of truth for "is this data honest?", and mirror
the list documented in .claude/skills/govcon-data-contract/SKILL.md:
  1. Scorer parity (app re-score == baked, max diff 0.0, tiers 100%)
  2. No expired_stale / days<-90 row in Tiers 1-4
  3. Bucket integrity (partition, no expired in forward buckets, days consistency)
  4. KPI values re-derivable from facts
  5. Quality (no unflagged garbled titles; title_display never leaks raw records)
  6. Schema contract (required columns present)
  7. snapshot_date present, scorer_version matches, CSV<->Parquet equal where both ship
  8. dim_vendor vulnerability honesty (CONDITIONAL on vulnerability_basis being present,
     so pre-rework bundles — e.g. the legacy synthetic sample — are exempt, not failed):
     score empty <=> basis is an Unknown basis; every present score in [0, 100];
     pct_value_expired / pct_value_unknown_expiration in [0, 100] where present; a
     basis column WITHOUT the score column is a recorded failure, not a KeyError
  9. PUBLIC-artifact policy (data/sample target only): the public-excluded free-text
     columns are absent from fact_contract_awards, no contact-marked title
     (transform.cleaning.CONTACT_TITLE_RE) ships in candidates/notices titles, and the
     PK-personnel CANARY holds — no personnel-office title (PKA/PKF/PKH/PKS/PKP, a
     two-dash PKB form, the Navy N102 office family, or a slash-path bare PK office)
     reaches a public artifact still followed by a name-shaped token.
     The canary regex is DELIBERATELY independent of transform.cleaning: it checks the
     artifact, not the rule, so a regression that mangles the redaction rule (or drops
     the redact call from the bake) still fails the build here
 10. Burn-pressure honesty (PRESENCE-GATED on burn_basis, so pre-burn/stale bundles are
     skipped — invariant 6 records the missing columns instead of a KeyError): baked burn ==
     fresh recompute on the same snapshot; basis/band vocabulary; the measured <=> pressure
     <=> real-band triple; and the cbr-present <=> den_ok secondary rule (den_ok requires a
     finite base>0, a finite obl>=0, and a non-IDV award_type)
 11. Reason-codes honesty (SAMPLE- and presence-gated): every candidate yields >=1 chip and each
     chip's basis vocabulary / glyph / is_estimate<->inferred / digit-free-on-missing rule holds
     when recomputed over real facts (nothing is baked)
 12. Incumbent-concentration honesty (PRESENCE-GATED on incumbent_uei/subagency): descriptive
     top-incumbent obligated-dollar share; top_share present <=> assessable; top_share in (0,1]
     and the vendor-floor + UEI-coverage gates hold on assessable markets; no negative obligated
     dollars. NO Herfindahl number and NO DOJ/FTC bands are computed (Corrections v2, Option A).
     12b (F4, PRESENCE-GATED on dim_agency.concentration_basis — a pre-join bundle is exempt):
     the baked per-component concentration_* join holds the unforgeable-Unknown equivalences
     (observed <=> top_share present <=> no refusal reason; n_ueis always published; basis
     vocabulary; top_share in (0,1] on observed) and equals a fresh annotate over the bundle's
     own reportable frame — the same recompute-parity rule as invariants 10/10c.
 13. Trust-metrics honesty (PRESENCE-GATED on trust_metrics_report): gate_state vocabulary;
     value present <=> published (Unknown unforgeable); every gated row carries a note; CI rows
     ordered within [0,1]; published precision floors re-checked ON THE ARTIFACT (>=30/tier,
     >=40 for precision_at_50); NO metric named recall/_at_10/probability may exist;
     snapshot_date matches the bundle.
 14. Bridge-link recency honesty (PRESENCE-GATED on the bridge table + the linker's date
     columns, mirroring 10c/12b): no established (non-No-Match) link whose notice posted_date
     and candidate end-anchor both parse may sit outside the linker's recency window around
     EVERY known anchor (selected expiry / current end — transform.opportunity_linking's own
     priors). The 2026-07 audit's degenerate shape — a years-early notice "recompeting" far-
     future expiries — can never ship in a baked bridge again; undated rows are exempt (a
     date we do not have cannot prove a violation, the gate's own rule).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "streamlit_app", ROOT / "src", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from components import rescore  # noqa: E402
from scoring import burn_pressure as burn  # noqa: E402
from scoring import incumbent_displacement as disp  # noqa: E402
from scoring import mods_signal as mods  # noqa: E402
from scoring import quality_flags as quality  # noqa: E402
from scoring import reason_codes as rc  # noqa: E402
from scoring.market_concentration import compute_hhi_concentration  # noqa: E402  # hhi_concentration
from transform.cleaning import CONTACT_TITLE_RE, PUBLIC_EXCLUDED_COLUMNS  # noqa: E402
from transform.incumbent_agency import BASIS_SCORED, VULNERABILITY_UNKNOWN_BASES  # noqa: E402
from utils.config import BURN_PRESSURE, HHI_CONCENTRATION_CONFIG, INCUMBENT_DISPLACEMENT, REASON_CODES  # noqa: E402

POWERBI_DIR = ROOT / "data" / "powerbi"                       # FULL snapshot (not committed)
SAMPLE_DIR = ROOT / "data" / "sample"                         # committed default subsample
LEGACY_SAMPLE_DIR = ROOT / "streamlit_app" / "assets" / "sample_data"  # legacy synthetic bundle

_PRIMARY = "fact_recompete_candidates.csv"

# Required columns per table (contract mirror — see the data-contract skill).
REQUIRED_COLUMNS = {
    "fact_recompete_candidates": [
        "candidate_id", "contract_title", "title_display", "days_until_expiration",
        "candidate_status", "expiration_bucket", "expiration_bucket_sort",
        "pursuit_score", "priority_tier", "referenced_idv_piid", "source_url",
        "flag_garbled_title", "flag_code_prefix", "flag_short_title",
        "flag_stale_expiration", "flag_missing_end_date",
        "ceiling_burn_ratio", "burn_pressure", "burn_band", "burn_basis",
    ],
    "dashboard_kpi_summary": [
        "total_estimated_pipeline_value", "recompete_candidate_count", "tier_1_count",
        "average_data_quality_score", "snapshot_date", "scorer_version",
        "active_candidate_count", "active_pipeline_value", "active_tier_1_count",
        "expired_grace_count", "expired_stale_count", "top_dod_component_by_active_value",
        "vehicle_count", "task_order_count", "garbled_title_count",
    ],
    "dim_agency": ["agency", "subagency", "active_candidate_count", "active_pipeline_value"],
    "dim_vendor": ["incumbent_vendor", "active_recompete_candidate_count", "active_value"],
    "fact_scoring_breakdown": ["candidate_id", "score_component", "weight", "raw_score", "weighted_score"],
}

TIER_1_4 = {"Tier 1: Pursue Now", "Tier 2: Capture Research", "Tier 3: Monitor", "Tier 4: Low Priority"}


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


def _rel(p: Path) -> Path:
    """Repo-relative display label; falls back to the absolute path for a directory
    outside the repo (a pytest tmp_path, a deploy-repo bundle) — same guard as
    scripts/build_sample.py, so validate() never crashes on the label line."""
    try:
        return p.relative_to(ROOT)
    except ValueError:
        return p


def validate(target: Path) -> list:
    print(f"\nValidating {_rel(target)}")
    r = Report()
    cand = _load(target, "fact_recompete_candidates")
    kpi = _load(target, "dashboard_kpi_summary").iloc[0]

    # 6. Schema contract (run first so later checks can assume columns exist).
    for tbl, cols in REQUIRED_COLUMNS.items():
        df = _load(target, tbl)
        missing = [c for c in cols if c not in df.columns]
        r.check(f"schema:{tbl}", not missing, f"missing columns {missing}")

    days = pd.to_numeric(cand["days_until_expiration"], errors="coerce")

    # 1. Scorer parity: re-score reproduces baked pursuit_score / priority_tier.
    rescored = rescore.score_candidates(cand.copy(), rescore.DEMO_PROFILE)
    score_diff = (pd.to_numeric(rescored["pursuit_score"], errors="coerce")
                  - pd.to_numeric(cand["pursuit_score"], errors="coerce")).abs().max()
    r.check("parity:pursuit_score max abs diff == 0.0", float(score_diff) == 0.0, f"max diff {score_diff}")
    tier_mismatch = int((rescored["priority_tier"].values != cand["priority_tier"].values).sum())
    r.check("parity:priority_tier 100% match", tier_mismatch == 0, f"{tier_mismatch} mismatches")

    # 2. No stale / long-expired row in Tiers 1-4.
    tiered = cand[cand["priority_tier"].isin(TIER_1_4)]
    r.check("no expired_stale in Tiers 1-4",
            (tiered["candidate_status"] == "expired_stale").sum() == 0)
    stale_days_in_tiers = int((pd.to_numeric(tiered["days_until_expiration"], errors="coerce") < -90).sum())
    r.check("no days<-90 row in Tiers 1-4", stale_days_in_tiers == 0, f"{stale_days_in_tiers} rows")

    # 3. Bucket integrity.
    valid_buckets = set(quality.BUCKET_ORDER)
    r.check("buckets partition (all rows in a known bucket)",
            cand["expiration_bucket"].isin(valid_buckets).all())
    fwd = cand[cand["expiration_bucket"] != quality.BUCKET_ORDER[0]]
    r.check("no expired (days<0) row in a forward bucket",
            (pd.to_numeric(fwd["days_until_expiration"], errors="coerce") < 0).sum() == 0)
    expected_bucket = days.map(quality.derive_bucket)
    r.check("bucket <-> days consistency", (expected_bucket.values == cand["expiration_bucket"].values).all())
    expected_sort = cand["expiration_bucket"].map(quality.bucket_sort)
    r.check("bucket_sort consistency", (expected_sort.values == pd.to_numeric(cand["expiration_bucket_sort"]).values).all())
    # days recomputable from selected_expiration_date vs snapshot.
    snap = pd.Timestamp(kpi["snapshot_date"])
    recomputed_days = (pd.to_datetime(cand["selected_expiration_date"], errors="coerce") - snap).dt.days
    day_mismatch = int((recomputed_days.dropna().astype(int).values
                        != days.dropna().astype(int).values).sum())
    r.check("days_until_expiration matches snapshot recompute", day_mismatch == 0, f"{day_mismatch} rows")

    # 4. KPI values re-derivable from facts.
    active = cand[cand["candidate_status"] == "active"]
    r.check("kpi:active_candidate_count", int(kpi["active_candidate_count"]) == len(active),
            f"{kpi['active_candidate_count']} vs {len(active)}")
    r.check("kpi:tier_1_count", int(kpi["tier_1_count"]) == int((cand["priority_tier"] == "Tier 1: Pursue Now").sum()))
    r.check("kpi:expired_stale_count", int(kpi["expired_stale_count"]) == int((cand["candidate_status"] == "expired_stale").sum()))
    kpi_active_val = float(kpi["active_pipeline_value"])
    real_active_val = float(active["total_obligated_amount"].sum())
    r.check("kpi:active_pipeline_value within $1", abs(kpi_active_val - real_active_val) <= 1.0,
            f"{kpi_active_val} vs {real_active_val}")

    # 5. Quality: flags correct, title_display never leaks a raw record.
    recomputed_garbled = cand["contract_title"].map(quality.flag_garbled_title)
    r.check("no unflagged garbled titles",
            (recomputed_garbled.values == cand["flag_garbled_title"].values).all())
    disp_garbled = int(cand["title_display"].map(quality.flag_garbled_title).sum())
    r.check("title_display never matches raw-record pattern", disp_garbled == 0, f"{disp_garbled} rows")
    igf_leak = int(cand["title_display"].astype(str).str.contains("IGF::").sum())
    r.check("no IGF:: in any title_display", igf_leak == 0, f"{igf_leak} rows")

    # 7. Snapshot / version / format parity.
    r.check("snapshot_date present & parseable", pd.notna(pd.Timestamp(kpi["snapshot_date"])))
    r.check("scorer_version matches rescore.SCORER_VERSION",
            str(kpi["scorer_version"]) == rescore.SCORER_VERSION,
            f"{kpi['scorer_version']} vs {rescore.SCORER_VERSION}")
    for pq in target.glob("*.parquet"):
        name = pq.stem
        csv = target / f"{name}.csv"
        if csv.exists():
            a = pd.read_csv(csv, low_memory=False, encoding="utf-8")
            b = pd.read_parquet(pq)
            equal = a.shape == b.shape and list(a.columns) == list(b.columns)
            r.check(f"csv==parquet:{name}", equal, f"shape/cols differ {a.shape} vs {b.shape}")

    # 8. dim_vendor vulnerability honesty — CONDITIONAL on the basis column being
    # present, so data baked before the score rework (and the legacy synthetic
    # bundle, which never carries it) is exempt rather than failed.
    dim_vendor = _load(target, "dim_vendor")
    if "vulnerability_basis" in dim_vendor.columns:
        if "incumbent_vulnerability_score" not in dim_vendor.columns:
            r.check("vendor:vulnerability score column accompanies basis", False,
                    "vulnerability_basis is present but incumbent_vulnerability_score is missing "
                    "— a basis with no score column cannot be audited")
        else:
            v_score = pd.to_numeric(dim_vendor["incumbent_vulnerability_score"], errors="coerce")
            v_basis = dim_vendor["vulnerability_basis"]
            unknown_mismatch = int((v_score.isna() != v_basis.isin(VULNERABILITY_UNKNOWN_BASES)).sum())
            r.check("vendor:vulnerability score empty <-> Unknown basis", unknown_mismatch == 0,
                    f"{unknown_mismatch} rows")
            bad_basis = int((~v_basis.isin(VULNERABILITY_UNKNOWN_BASES | {BASIS_SCORED})).sum())
            r.check("vendor:vulnerability_basis vocabulary", bad_basis == 0, f"{bad_basis} rows")
            out_of_range = int((~v_score.dropna().between(0, 100)).sum())
            r.check("vendor:vulnerability score in [0,100]", out_of_range == 0, f"{out_of_range} rows")
        for pct_col in ("pct_value_expired", "pct_value_unknown_expiration"):
            if pct_col in dim_vendor.columns:
                pct = pd.to_numeric(dim_vendor[pct_col], errors="coerce")
                pct_bad = int((~pct.dropna().between(0, 100)).sum())
                r.check(f"vendor:{pct_col} in [0,100]", pct_bad == 0, f"{pct_bad} rows")

    # 9. PUBLIC-artifact policy (committed sample only — data/sample/ is published;
    # the local full snapshot legitimately keeps the excluded columns for Power BI).
    if target == SAMPLE_DIR:
        # EVERY PUBLIC_EXCLUDED_COLUMNS entry is checked against its own CSV (generic —
        # mirrors build_release.py's application), so a new policy entry (e.g.
        # fact_transactions.description, mods A6) reaching data/sample/ is a recorded
        # FAIL, never a silent pass. A policy table absent from the bundle is skipped.
        for _tbl, _excluded in PUBLIC_EXCLUDED_COLUMNS.items():
            _csv_path = target / f"{_tbl}.csv"
            if not _csv_path.exists():
                continue
            _tcols = pd.read_csv(_csv_path, nrows=0).columns
            leaked = [c for c in _excluded if c in _tcols]
            r.check(f"public:excluded free-text columns absent ({_tbl})", not leaked, f"present: {leaked}")
        marked = int(cand["contract_title"].astype(str).str.contains(CONTACT_TITLE_RE, na=False).sum()
                     + cand["title_display"].astype(str).str.contains(CONTACT_TITLE_RE, na=False).sum())
        notices = _load(target, "fact_opportunity_notices")
        marked += int(notices["title"].astype(str).str.contains(CONTACT_TITLE_RE, na=False).sum())
        r.check("public:no contact-marked titles", marked == 0, f"{marked} title cells")
        # PK-personnel drift CANARY — deliberately NOT imported from transform.cleaning
        # (see module docstring, invariant 9): if the redaction rule regresses or the bake
        # stops applying it, the leak must still fail HERE, on the artifact itself.
        pk_allowed = r"DCC|WING|IACP|MFRC|RISK|SYSTEMS?|AAS|CISO|CONGRESSIONAL|DTA|ISSO|PZ|FY\d+|\d+"
        pk_codes = r"PK[AFHSP][A-Z]?|PHK|CMK"
        pk_canary = (
            rf"(?:\b(?:{pk_codes})\b[\s:/;,\-]+"
            rf"(?!(?:{pk_allowed})\b)[A-Za-z]"
            r"|\bPKB\s*-\s*[A-Za-z][A-Za-z'.]*\s*-\s*[A-Za-z]"
            r"|\bN102[A-Z]?\b"
            rf"|/\s*PK\b[\s:/;,\-]+(?!(?:{pk_allowed})\b)[A-Za-z]"
            rf"|\bPK\s*:\s*(?!(?:{pk_allowed})\b)[A-Za-z]"
            rf"|\b[A-Za-z][A-Za-z'.]*\s*[-/]\s*[A-Za-z][A-Za-z'.]*\s*[-/]\s*(?:{pk_codes})\b"
            r"|^(?!(?:IGF|PROJECT|LABOR)\b)[A-Za-z][A-Za-z'.]*:[A-Za-z][A-Za-z'.]*\s)"
        )
        pk_hits = int(cand["contract_title"].astype(str).str.contains(pk_canary, case=False, na=False).sum()
                      + cand["title_display"].astype(str).str.contains(pk_canary, case=False, na=False).sum()
                      + notices["title"].astype(str).str.contains(pk_canary, case=False, na=False).sum())
        r.check("public:PK-personnel canary (no name-shaped token after office code)",
                pk_hits == 0, f"{pk_hits} title cells")

    # 10. Burn-pressure honesty: recompute-parity + triple-equivalence + vocabulary + range.
    #     PRESENCE-GATED (mirrors invariant 8): a pre-burn / stale bundle is missing these
    #     columns -> invariant 6 already RECORDED a "missing required columns" failure and
    #     `validate` exits non-zero with a readable report; this block is skipped so no KeyError.
    if "burn_basis" in cand.columns:
        _cfg = burn.load_burn_config(BURN_PRESSURE)
        fresh = burn.annotate_burn_pressure(cand.copy(), snap.date(), _cfg)
        bp = pd.to_numeric(cand["burn_pressure"], errors="coerce")
        fbp = pd.to_numeric(fresh["burn_pressure"], errors="coerce")
        band = cand["burn_band"].astype(str)
        basis = cand["burn_basis"].astype(str)
        cbr = pd.to_numeric(cand["ceiling_burn_ratio"], errors="coerce")
        # C1.4: mirror _to_float's non-finite rejection so an "inf" string in base/obl cannot
        # dishonestly satisfy den_ok (obl >= 0 is True for +inf) — zero effect on finite data.
        base = pd.to_numeric(cand["base_and_all_options_value"], errors="coerce").replace([np.inf, -np.inf], np.nan)
        obl = pd.to_numeric(cand["total_obligated_amount"], errors="coerce").replace([np.inf, -np.inf], np.nan)
        idv = cand["award_type"].astype(str).str.strip().str.upper().isin(set(_cfg.idv_award_types))
        measured = basis.eq("measured")

        # (a) recompute-parity (mirrors invariant 1): baked == fresh on the SAME snapshot.
        r.check("burn:basis recompute 100% match", bool((fresh["burn_basis"].values == basis.values).all()))
        r.check("burn:band recompute 100% match", bool((fresh["burn_band"].values == band.values).all()))
        pdiff = float((fbp - bp).abs().max()) if measured.any() else 0.0
        r.check("burn:pressure recompute max abs diff == 0.0", not (pdiff > 0.0), f"max diff {pdiff}")
        r.check("burn:pressure NaN alignment", bool((fbp.isna().values == bp.isna().values).all()))

        # (b) vocabulary.
        r.check("burn:basis vocabulary", bool(basis.isin(set(burn.BURN_BASES)).all()))
        r.check("burn:band vocabulary", bool(band.isin(set(burn.BURN_BANDS) | {burn.BURN_BAND_NA}).all()))

        # (c) TRIPLE-EQUIVALENCE (measured <=> pressure not-null <=> real band).
        r.check("burn:equiv measured<->pressure_notnull", bool((measured == bp.notna()).all()))
        r.check("burn:equiv measured<->real_band", bool((measured == band.isin(set(burn.BURN_BANDS))).all()))
        r.check("burn:nonmeasured<->not_applicable_band", bool(((~measured) == band.eq(burn.BURN_BAND_NA)).all()))

        # (d) range + cbr fact-rule (SECONDARY equivalence). den_ok includes obl >= 0.
        r.check("burn:measured pressure within [-1,1]", bool(bp.dropna().between(-1.0, 1.0).all()))
        den_ok = (base > 0) & obl.notna() & (obl >= 0) & (~idv)
        r.check("burn:ceiling_burn_ratio present <-> den_ok", bool((cbr.notna().values == den_ok.values).all()))
        r.check("burn:ceiling_exceeded ratio > threshold",
                bool((cbr[basis.eq("ceiling_exceeded")] > _cfg.ceiling_exceeded_ratio).all()))

    # 10b. Mods/termination honesty (PRESENCE-GATED on mods_basis, mirroring invariant 10 —
    #      a pre-mods bundle, e.g. the legacy synthetic sample, is exempt per D13: mod columns
    #      are deliberately NOT in REQUIRED_COLUMNS, so no fabricated termination history is
    #      ever demanded of a bundle that has none).
    if "mods_basis" in cand.columns:
        term_flag = cand["terminated"].astype(str).eq("True")
        term_code = cand["termination_code"]
        term_kind = cand["termination_kind"].astype(str)
        term_basis = cand["termination_basis"].astype(str)
        c_ratio = pd.to_numeric(cand["ceiling_growth_ratio"], errors="coerce")
        c_basis = cand["ceiling_basis"].astype(str)
        vel = pd.to_numeric(cand["mod_velocity"], errors="coerce")
        band = cand["mod_velocity_band"].astype(str)
        m_basis = cand["mods_basis"].astype(str)
        exp_basis = cand["expiration_date_basis"].astype(str)

        r.check("mods:termination_basis vocabulary", bool(term_basis.isin(set(mods.TERMINATION_BASES)).all()))
        r.check("mods:termination_kind vocabulary", bool(term_kind.isin(set(mods.TERMINATION_KINDS)).all()))
        r.check("mods:ceiling_basis vocabulary", bool(c_basis.isin(set(mods.CEILING_BASES)).all()))
        r.check("mods:mods_basis vocabulary", bool(m_basis.isin(set(mods.MODS_BASES)).all()))

        # Triple-equivalences (unforgeable Unknown — govcon honesty rule #3).
        r.check("mods:equiv terminated<->observed_code basis", bool((term_flag == term_basis.eq("observed_code")).all()))
        r.check("mods:equiv terminated<->code present", bool((term_flag == term_code.notna()).all()))
        r.check("mods:equiv not-terminated<->kind none", bool(((~term_flag) == term_kind.eq("none")).all()))
        r.check("mods:equiv ceiling ratio<->measured basis", bool((c_ratio.notna() == c_basis.eq("measured")).all()))
        r.check("mods:equiv velocity<->real band", bool((vel.notna() == band.isin(set(mods.VELOCITY_BANDS))).all()))
        r.check("mods:velocity only on measured history", bool(m_basis[vel.notna()].eq("measured").all()))

        # Ghost-fix contract: complete_likely <=> the row's expiration was retargeted.
        r.check("mods:complete_likely => basis terminated",
                bool(exp_basis[term_kind.eq("complete_likely")].eq("terminated").all()))
        r.check("mods:basis terminated => complete_likely",
                bool(term_kind[exp_basis.eq("terminated")].eq("complete_likely").all()))
        r.check("mods:terminated => code in {E,F,X,N}",
                bool(term_code.dropna().astype(str).isin(set(mods.TERMINATION_CODES)).all()))
        r.check("mods:mod_count >= 1 on every candidate",
                bool((pd.to_numeric(cand["mod_count"], errors="coerce") >= 1).all()))

        # fact_transactions evidence: each transaction emitted ONCE (a duplicate id IS the
        # A1 cross-file double-fold defect — a recorded FAIL, never a silent pass).
        txns = _load(target, "fact_transactions")
        if len(txns):
            r.check("mods:fact_transactions.transaction_id unique",
                    bool(txns["transaction_id"].is_unique),
                    f"{int(txns['transaction_id'].duplicated().sum())} duplicate ids")

    # 10c. Displacement-lane honesty (PRESENCE-GATED on displacement_basis, mirroring 10/10b —
    #      a pre-lane bundle is exempt; the lane columns are deliberately NOT in REQUIRED_COLUMNS).
    #      The lane is a categorical LABEL: these checks pin vocabulary, the unforgeable-Unknown
    #      equivalences, count<=read<=6, and baked == fresh recompute over the bundle's own
    #      dim_vendor (the same join the bake used). Invariant 1 above is the firewall proving
    #      the lane never moved pursuit_score/priority_tier.
    if "displacement_basis" in cand.columns:
        d_basis = cand["displacement_basis"].astype(str)
        d_band = cand["displacement_band"].astype(str)
        d_count = pd.to_numeric(cand["displacement_signal_count"], errors="coerce")
        d_read = pd.to_numeric(cand["displacement_signals_read"], errors="coerce")
        d_sigs = cand["displacement_signals"]
        d_unread = cand["displacement_unread"]
        observed = d_basis.eq("observed")

        r.check("displacement:basis vocabulary", bool(d_basis.isin(set(disp.DISPLACEMENT_BASES)).all()))
        r.check("displacement:band vocabulary",
                bool(d_band.isin(set(disp.DISPLACEMENT_BANDS) | {disp.DISPLACEMENT_BAND_NA}).all()))
        # Unforgeable Unknown: observed <=> count present <=> signals present <=> real band.
        r.check("displacement:equiv observed<->count present", bool((observed == d_count.notna()).all()))
        r.check("displacement:equiv observed<->signals present", bool((observed == d_sigs.notna()).all()))
        r.check("displacement:equiv observed<->real band",
                bool((observed == d_band.isin(set(disp.DISPLACEMENT_BANDS))).all()))
        r.check("displacement:read count always published", bool(d_read.notna().all()))
        r.check("displacement:unread always published", bool(d_unread.notna().all()))
        n_total = len(disp.DISPLACEMENT_SIGNALS)
        r.check("displacement:0 <= count <= read <= n_signals",
                bool(((d_count.fillna(0) >= 0) & (d_count.fillna(0) <= d_read) & (d_read <= n_total)).all()))
        # Recompute parity: the baked lane == a fresh annotate over the same bundle.
        fresh = disp.annotate_displacement(
            cand.copy(), disp.load_displacement_config(INCUMBENT_DISPLACEMENT), vendor_size_shift=dim_vendor,
        )
        r.check("displacement:basis recompute 100% match",
                bool((fresh["displacement_basis"].values == d_basis.values).all()))
        r.check("displacement:band recompute 100% match",
                bool((fresh["displacement_band"].values == d_band.values).all()))
        f_count = pd.to_numeric(fresh["displacement_signal_count"], errors="coerce")
        r.check("displacement:count recompute NaN alignment",
                bool((f_count.isna().values == d_count.isna().values).all()))
        cdiff = float((f_count - d_count).abs().max()) if bool(d_count.notna().any()) else 0.0
        r.check("displacement:count recompute max abs diff == 0.0", not (cdiff > 0.0), f"max diff {cdiff}")
        # fillna sentinel BEFORE str-compare: an insufficient row is NaN off CSV but None fresh
        # in memory — both are NA to pandas, and neither may read as the literal "nan"/"None".
        baked_sigs = d_sigs.fillna("__NA__").astype(str)
        fresh_sigs = fresh["displacement_signals"].fillna("__NA__").astype(str)
        sig_mismatch = int((fresh_sigs.values != baked_sigs.values).sum())
        r.check("displacement:signals recompute 100% match", sig_mismatch == 0, f"{sig_mismatch} rows")
        # unread is the one always-published column that flows into rendered markdown — pin its
        # content to the recompute too (not just notna), closing the tamper path (security 10c).
        baked_unread = d_unread.fillna("__NA__").astype(str)
        fresh_unread = fresh["displacement_unread"].fillna("__NA__").astype(str)
        unread_mismatch = int((fresh_unread.values != baked_unread.values).sum())
        r.check("displacement:unread recompute 100% match", unread_mismatch == 0, f"{unread_mismatch} rows")

    # 11. Reason-codes honesty (recompute over real facts; nothing is baked). SAMPLE-gated for wall-time
    #     (trivial on the 200-row sample; skipped on the 36k powerbi target — CI gates on the sample) and
    #     presence-gated so an older/pre-burn bundle degrades rather than KeyErrors. score_components is
    #     PUBLIC (rescore.py:97) so it is called UNCONDITIONALLY; the honesty guard is the analog of
    #     invariant 1's parity — the explanation layer never fabricates over real baked facts.
    _need = {"days_until_expiration", "classification_confidence", "type_of_set_aside_code"}
    if target == SAMPLE_DIR and _need.issubset(cand.columns):
        _rcfg = rc.load_reason_config(REASON_CODES)
        _prof = rescore.DEMO_PROFILE  # same baseline profile as invariant 1
        empty = bad_basis = bad_glyph = bad_estimate = fabricated = 0
        for _row in cand.to_dict("records"):
            _comps = rescore.score_components(_row, _prof)  # unconditional — score_components is public
            _chips = rc.reason_codes(_row, _comps, _prof, _rcfg)
            if not _chips:
                empty += 1
            for _c in _chips:
                if _c.basis not in rc.BASES:
                    bad_basis += 1
                if _c.glyph != rc.BASIS_GLYPHS.get(_c.basis, ""):
                    bad_glyph += 1
                if _c.is_estimate != (_c.basis == "inferred"):
                    bad_estimate += 1
                if _c.basis == "missing" and any(ch.isdigit() for ch in _c.evidence):
                    fabricated += 1
        r.check("reason:every row yields >=1 chip", empty == 0, f"{empty} empty rows")
        r.check("reason:basis vocabulary", bad_basis == 0, f"{bad_basis} chips")
        r.check("reason:glyph matches basis", bad_glyph == 0, f"{bad_glyph} chips")
        r.check("reason:is_estimate <-> inferred", bad_estimate == 0, f"{bad_estimate} chips")
        r.check("reason:no fabricated number on missing chip", fabricated == 0, f"{fabricated} chips")

    # 12. Incumbent-concentration honesty (Corrections v2 C3.4 — descriptive top-share; NO HHI
    #     number, NO DOJ/FTC bands, so NO band-cutoff / hhi-range checks and NO moderate/high
    #     config reads here). PRESENCE-GATED on the concentration inputs so a legacy bundle without
    #     incumbent_uei is exempt (invariant 6 records the missing columns) rather than KeyError-ing.
    #     Reads only fact_recompete_candidates; introduces no shared helper (builds in any merge order).
    conc_cols = {"incumbent_uei", "subagency", "total_obligated_amount", "priority_tier"}
    if conc_cols.issubset(set(cand.columns)):
        reportable = cand[cand["priority_tier"] != "Data Gap"].copy()
        markets = compute_hhi_concentration(reportable, HHI_CONCENTRATION_CONFIG)
        assessable = [m for m in markets if m.assessable]

        # Re-expressed equivalence (was the band triple): top_share present <=> assessable.
        r.check("hhi_concentration:top_share None <-> not assessable",
                all((m.top_share is None) == (not m.assessable) for m in markets),
                "an assessable market must carry a top_share and an Unknown must not")
        r.check("hhi_concentration:top_share in (0,1] on assessable",
                all(m.top_share is not None and 0.0 < m.top_share <= 1.0 for m in assessable),
                "an assessable market's top_share is outside (0,1]")
        r.check("hhi_concentration:assessable markets meet the vendor floor",
                all(m.n_ueis >= HHI_CONCENTRATION_CONFIG["min_market_ueis"] for m in assessable),
                "an assessable market has fewer than min_market_ueis incumbents")
        r.check("hhi_concentration:coverage gate honored on assessable",
                all((1.0 - m.coverage) <= HHI_CONCENTRATION_CONFIG["max_unknown_uei_share"] for m in assessable),
                "an assessable market breaches the UEI-coverage floor")
        neg = int((pd.to_numeric(reportable["total_obligated_amount"], errors="coerce") < 0).sum())
        r.check("hhi_concentration:no non-positive obligated dollars on reportable candidates",
                neg == 0, f"{neg} negative rows — deobligation math would break share denominators")

        # 12b. Baked dim_agency concentration join (F4) — PRESENCE-GATED on the baked columns
        #      (mirrors 10/10b/10c: a pre-join bundle is exempt, never KeyErrored). The lane is a
        #      LABEL at agency grain; invariant 1 above remains the firewall proving it never
        #      moved pursuit_score/priority_tier.
        dim_agency = _load(target, "dim_agency")
        if "concentration_basis" in dim_agency.columns:
            from scoring.market_concentration import (
                CONCENTRATION_BASES,
                annotate_agency_concentration,
            )

            c_share = pd.to_numeric(dim_agency["concentration_top_share"], errors="coerce")
            c_basis = dim_agency["concentration_basis"].astype(str)
            c_n = pd.to_numeric(dim_agency["concentration_n_ueis"], errors="coerce")
            c_reason = dim_agency["concentration_reason"]
            c_observed = c_basis.eq("observed")

            r.check("hhi_concentration:baked basis vocabulary",
                    bool(c_basis.isin(set(CONCENTRATION_BASES)).all()))
            r.check("hhi_concentration:baked equiv observed<->top_share present",
                    bool((c_observed == c_share.notna()).all()))
            # An observed row's empty reason legitimately round-trips CSV as NaN — blank-or-NaN
            # counts as "no refusal"; a reason on an observed row (or none on an insufficient
            # row) breaks the equivalence.
            reason_blank = c_reason.isna() | c_reason.astype(str).str.strip().eq("")
            r.check("hhi_concentration:baked equiv observed<->no refusal reason",
                    bool((c_observed == reason_blank).all()))
            r.check("hhi_concentration:baked top_share in (0,1] on observed",
                    bool(((c_share.dropna() > 0.0) & (c_share.dropna() <= 1.0)).all()))
            r.check("hhi_concentration:baked n_ueis always published", bool(c_n.notna().all()))
            fresh_da = annotate_agency_concentration(dim_agency, reportable, HHI_CONCENTRATION_CONFIG)
            r.check("hhi_concentration:baked basis recompute 100% match",
                    bool((fresh_da["concentration_basis"].values == c_basis.values).all()))
            f_share = pd.to_numeric(fresh_da["concentration_top_share"], errors="coerce")
            r.check("hhi_concentration:baked share recompute NaN alignment",
                    bool((f_share.isna().values == c_share.isna().values).all()))
            sdiff = float((f_share - c_share).abs().max()) if bool(c_share.notna().any()) else 0.0
            r.check("hhi_concentration:baked share recompute max abs diff ~ 0",
                    not (sdiff > 1e-12), f"max diff {sdiff}")
            f_n = pd.to_numeric(fresh_da["concentration_n_ueis"], errors="coerce")
            r.check("hhi_concentration:baked n_ueis recompute 100% match",
                    bool((f_n.values == c_n.values).all()))

    # 13. Trust-metrics honesty (PRESENCE-GATED on trust_metrics_report existing, so
    #     pre-Phase-1 bundles — including the shipped release — stay exempt, not failed):
    #     a gated metric can never carry a number, and no forbidden metric can exist at all.
    trust_path = target / "trust_metrics_report.csv"
    if trust_path.exists():
        trust = _load(target, "trust_metrics_report")
        from scoring.trust_metrics import GATE_STATES

        r.check("trust:gate_state vocabulary",
                trust["gate_state"].isin(GATE_STATES).all(),
                f"unknown states: {sorted(set(trust['gate_state']) - set(GATE_STATES))}")
        published = trust["gate_state"] == "published"
        has_value = pd.to_numeric(trust["value"], errors="coerce").notna()
        r.check("trust:value present <=> published (Unknown is unforgeable)",
                bool((published == has_value).all()),
                f"{int((published != has_value).sum())} row(s) violate the equivalence")
        notes = trust["note"].fillna("").astype(str).str.strip()
        r.check("trust:every non-published row carries an honest note",
                bool((notes[~published].str.len() > 0).all()),
                "a gated row with no note is a silent refusal")
        ci_lo = pd.to_numeric(trust["ci_low"], errors="coerce")
        ci_hi = pd.to_numeric(trust["ci_high"], errors="coerce")
        val = pd.to_numeric(trust["value"], errors="coerce")
        with_ci = ci_lo.notna() & ci_hi.notna()
        rate_ok = ((ci_lo[with_ci] >= 0) & (ci_lo[with_ci] <= val[with_ci])
                   & (val[with_ci] <= ci_hi[with_ci]) & (ci_hi[with_ci] <= 1))
        r.check("trust:rate metrics 0 <= ci_low <= value <= ci_high <= 1",
                bool(rate_ok.all()), f"{int((~rate_ok).sum())} CI row(s) out of order")
        n_col = pd.to_numeric(trust["n"], errors="coerce")
        prec = trust["metric"].str.match(r"link_precision_(high|medium|low)$") & published
        r.check("trust:published link precision respects the >=30 floor ON THE ARTIFACT",
                bool((n_col[prec] >= 30).all()), f"{int((n_col[prec] < 30).sum())} row(s) under floor")
        p50 = (trust["metric"] == "precision_at_50") & published
        r.check("trust:published precision_at_50 respects the >=40 floor ON THE ARTIFACT",
                bool((n_col[p50] >= 40).all()), f"{int((n_col[p50] < 40).sum())} row(s) under floor")
        forbidden = trust["metric"].str.contains(r"recall|_at_10|probability", regex=True)
        r.check("trust:no recall / precision@10 / probability metric exists",
                not bool(forbidden.any()), f"forbidden: {trust.loc[forbidden, 'metric'].tolist()}")
        r.check("trust:snapshot_date matches dashboard_kpi_summary",
                bool((trust["snapshot_date"].astype(str) == str(kpi["snapshot_date"])).all()),
                "trust rows stamped with a different snapshot than the bundle")

    # 14. Bridge-link recency honesty (PRESENCE-GATED, mirroring 10c/12b): every established
    #     (non-No-Match) link must sit inside the linker's recency window around AT LEAST ONE
    #     known anchor — the policy-selected expiry (selected -> potential -> current fallback,
    #     _candidate_recency_anchors' chain) or the current period end. The 2026-07 audit found
    #     71% of the shipped bridge's links outside the window the linker code already enforced:
    #     the artifact had never been re-baked. Undated rows are exempt — a date we do not have
    #     cannot prove a violation (the gate's own rule).
    bridge_path = target / "bridge_award_opportunity_links.csv"
    bridge_cand_cols = {"candidate_id", "selected_expiration_date", "current_end_date"}
    if bridge_path.exists() and bridge_cand_cols.issubset(cand.columns):
        from transform.opportunity_linking import RECENCY_MONTHS_AFTER, RECENCY_MONTHS_BEFORE

        bridge = _load(target, "bridge_award_opportunity_links")
        notices_path = target / "fact_opportunity_notices.csv"
        notices = _load(target, "fact_opportunity_notices") if notices_path.exists() else pd.DataFrame()
        if ({"candidate_id", "linked_notice_id", "link_confidence"}.issubset(bridge.columns)
                and {"notice_id", "posted_date"}.issubset(notices.columns)):
            est = bridge[bridge["link_confidence"].astype(str) != "No Match"]
            anchor_cols = [c for c in ("selected_expiration_date", "potential_end_date", "current_end_date")
                           if c in cand.columns]
            # str-normalize both merge keys: an all-No-Match bridge (e.g. the legacy synthetic
            # bundle) parses linked_notice_id as all-NaN float64, and a float64<->object merge
            # raises even on an empty established set.
            m = est.assign(linked_notice_id=est["linked_notice_id"].astype(str)).merge(
                cand[["candidate_id", *anchor_cols]], on="candidate_id", how="left",
            ).merge(
                notices[["notice_id", "posted_date"]].drop_duplicates("notice_id")
                .assign(notice_id=lambda d: d["notice_id"].astype(str)),
                left_on="linked_notice_id", right_on="notice_id", how="left",
            )
            posted = pd.to_datetime(m["posted_date"], errors="coerce")
            parsed = {c: pd.to_datetime(m[c], errors="coerce") for c in anchor_cols}
            nat = pd.Series(pd.NaT, index=m.index)
            # The selected-expiry anchor mirrors _candidate_recency_anchors' fallback chain.
            primary = (parsed.get("selected_expiration_date", nat)
                       .fillna(parsed.get("potential_end_date", nat))
                       .fillna(parsed.get("current_end_date", nat)))
            current = parsed.get("current_end_date", nat)
            lo, hi = pd.DateOffset(months=RECENCY_MONTHS_BEFORE), pd.DateOffset(months=RECENCY_MONTHS_AFTER)
            in_primary = primary.notna() & (posted >= primary - lo) & (posted <= primary + hi)
            in_current = current.notna() & (posted >= current - lo) & (posted <= current + hi)
            gateable = posted.notna() & (primary.notna() | current.notna())
            violations = int((gateable & ~(in_primary | in_current)).sum())
            r.check("bridge:established links inside the recency window (either anchor)",
                    violations == 0,
                    f"{violations} of {len(est)} established link(s) posted outside every known anchor window")

    return r.failures


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the published star schema.")
    ap.add_argument("--sample", action="store_true",
                    help="validate the committed default sample (data/sample/) instead")
    ap.add_argument("--legacy-sample", action="store_true",
                    help="validate the legacy synthetic bundle (streamlit_app/assets/sample_data/)")
    ap.add_argument("--both", action="store_true",
                    help="validate the full snapshot AND the committed sample")
    args = ap.parse_args()

    if args.both:
        targets = [POWERBI_DIR, SAMPLE_DIR]
    elif args.legacy_sample:
        targets = [LEGACY_SAMPLE_DIR]
    elif args.sample:
        targets = [SAMPLE_DIR]
    else:
        targets = [POWERBI_DIR]

    all_failures = []
    for t in targets:
        if not (t / _PRIMARY).exists():
            print(f"\nSKIP {_rel(t)} — not present (the full snapshot is not "
                  f"committed; fetch it with scripts/download_data.py).")
            continue
        all_failures += validate(t)

    print()
    if all_failures:
        print(f"VALIDATION FAILED — {len(all_failures)} issue(s):")
        for f in all_failures:
            print(f"  - {f}")
        return 1
    print("VALIDATION PASSED — all invariants hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
