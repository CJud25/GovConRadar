"""
powerbi_export.py — Writes the Power BI-ready star schema to data/powerbi/ as
UTF-8 CSV (ISO 8601 dates, no thousands separators) plus a Parquet copy of each
table for import-mode performance.

Design for Power BI import mode with single-direction, one-to-many relationships
from dimensions to facts:

  fact_recompete_candidates ── agency_key ─────► dim_agency
                            ── vendor_key ─────► dim_vendor
                            ── naics ──────────► dim_naics
                            ── psc ────────────► dim_psc
                            ── expiration_date_key / window_*_date_key ► dim_date
                            ── priority_tier ──► dim_priority_tier
                            ── capture_phase ──► dim_capture_phase
  fact_scoring_breakdown    ── candidate_id ───► fact_recompete_candidates (long, for waterfalls)
  bridge_award_opportunity_links ── candidate_id ► fact_recompete_candidates

Buckets, tiers, and capture phases ship as Python-computed columns WITH sort-order
companions (not DAX SWITCH logic) so the model stays thin and visuals sort/color
consistently off the dims. See powerbi/report_spec.md.
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scoring.price_to_win import PTW_COMPARABLES_COLUMNS
from scoring.pursuit_score import _WEIGHT_KEY_MAP
from scoring.quality_flags import derive_capture_phase as _capture_phase
from transform.cleaning import scrub_free_text_columns
from utils.config import SCORING_WEIGHTS

# Free-text columns per shipped table that may carry contracting-officer / POC
# contact info (emails, phone numbers). These are scrubbed on write so no PII is
# ever exported. contract_title is populated directly from description_raw
# (see transform.recompete), so it carries the same risk.
FREE_TEXT_PII_COLUMNS = {
    "fact_contract_awards": ["description_raw"],
    "fact_recompete_candidates": ["contract_title"],
    "fact_opportunity_notices": ["title"],
    # Transaction descriptions (mods-signal evidence) are FPDS free text that can carry
    # POC contact info — scrubbed on the LOCAL export like every other free-text column.
    # The mod signals themselves never read this free text; it ships only as evidence.
    "fact_transactions": ["description"],
}

# ─── SORT / COLOR REFERENCE (single source of truth, mirrored in the theme) ───
EXPIRATION_BUCKET_SORT = {
    "0-6 Months": 1, "6-12 Months": 2, "12-18 Months": 3,
    "18-24 Months": 4, "24+ Months": 5, "Unknown": 9,
}

# tier_name must exactly match scoring.pursuit_score.priority_tier() output.
PRIORITY_TIER_DIM = [
    {"priority_tier": "Tier 1: Pursue Now", "tier_short": "Pursue Now", "tier_sort_order": 1, "tier_hex_color": "#E4572E"},
    {"priority_tier": "Tier 2: Capture Research", "tier_short": "Capture Research", "tier_sort_order": 2, "tier_hex_color": "#F2A900"},
    {"priority_tier": "Tier 3: Monitor", "tier_short": "Monitor", "tier_sort_order": 3, "tier_hex_color": "#3E7CB1"},
    {"priority_tier": "Tier 4: Low Priority", "tier_short": "Low Priority", "tier_sort_order": 4, "tier_hex_color": "#8A8D91"},
    {"priority_tier": "Data Gap", "tier_short": "Data Gap", "tier_sort_order": 5, "tier_hex_color": "#C9CBCF"},
]

# (capture_phase, sort_order, hex) — sorted along the capture lifecycle.
CAPTURE_PHASE_DIM = [
    {"capture_phase": "Early Watch", "capture_phase_sort": 0, "phase_hex_color": "#6C8EBF"},
    {"capture_phase": "Pre-RFP Shaping", "capture_phase_sort": 1, "phase_hex_color": "#3E7CB1"},
    {"capture_phase": "Capture Planning", "capture_phase_sort": 2, "phase_hex_color": "#2E8B76"},
    {"capture_phase": "Proposal Prep", "capture_phase_sort": 3, "phase_hex_color": "#F2A900"},
    {"capture_phase": "Proposal / Submit", "capture_phase_sort": 4, "phase_hex_color": "#E4572E"},
    {"capture_phase": "Expired", "capture_phase_sort": 5, "phase_hex_color": "#8A8D91"},
    {"capture_phase": "Unknown / Data Gap", "capture_phase_sort": 9, "phase_hex_color": "#C9CBCF"},
]

EMPTY_SCHEMAS = {
    "fact_opportunity_notices": [
        "notice_id", "solicitation_number", "title", "notice_type", "posted_date",
        "response_deadline", "archive_date", "agency", "subagency", "office",
        "naics", "psc", "set_aside", "set_aside_code", "place_of_performance_state",
        "source_url", "pull_timestamp_utc",
    ],
    "fact_transactions": [
        "transaction_id", "award_id", "modification_number", "action_date",
        "action_type_code", "action_obligation", "description",
    ],
}


# ─── HELPERS ──────────────────────────────────────────────────────────────────
# _capture_phase is imported above from scoring.quality_flags (the one copy) so the
# per-row fact column, dim_capture_phase, and the app's live recompute never drift.


def _date_key(value):
    """ISO date -> integer yyyymmdd surrogate key for dim_date; pd.NA if missing."""
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return pd.NA
    if isinstance(value, str):
        value = pd.to_datetime(value, errors="coerce")
        if pd.isna(value):
            return pd.NA
    return int(pd.Timestamp(value).strftime("%Y%m%d"))


def _date_key_series(series: pd.Series) -> pd.Series:
    return pd.array([_date_key(v) for v in series], dtype="Int64")


def _write(df: pd.DataFrame, out_dir: Path, name: str) -> Path:
    # CSV is the single published format: both consumers — the Power BI semantic
    # model (Csv.Document per table) and the Streamlit app (load_table reads .csv)
    # — read CSV. A parquet copy was previously written too but nothing read it,
    # so it was pure duplication (~half of data/powerbi/); dropped.
    csv_path = out_dir / f"{name}.csv"
    df.to_csv(csv_path, index=False)
    return csv_path


def _collect_date_bounds(candidates: pd.DataFrame, awards: pd.DataFrame):
    """Min/max across every real date column so dim_date fully covers the model."""
    cand_cols = [
        "selected_expiration_date", "estimated_recompete_window_start",
        "estimated_recompete_window_end", "current_end_date", "potential_end_date",
    ]
    award_cols = ["pop_start_date", "pop_current_end_date", "pop_potential_end_date", "date_signed"]
    values = []
    for df, cols in ((candidates, cand_cols), (awards, award_cols)):
        for c in cols:
            if not df.empty and c in df.columns:
                s = pd.to_datetime(df[c], errors="coerce").dropna()
                values.extend(s.tolist())
    if not values:
        return None, None
    return min(values).date(), max(values).date()


# ─── DIMENSIONS ───────────────────────────────────────────────────────────────
def build_dim_date(start: date, end: date, today: date = None) -> pd.DataFrame:
    """Continuous federal-fiscal-year date table. FY starts Oct 1 (FY2027 = Oct 2026)."""
    today = today or date.today()
    days = pd.date_range(start, end, freq="D")
    fiscal_year = [d.year + 1 if d.month >= 10 else d.year for d in days]
    fiscal_quarter = [((d.month - 10) % 12) // 3 + 1 for d in days]  # Oct-Dec=Q1 ... Jul-Sep=Q4
    today_ts = pd.Timestamp(today)
    return pd.DataFrame({
        "date": days,
        "date_key": [int(d.strftime("%Y%m%d")) for d in days],
        "year": days.year,
        "quarter": days.quarter,
        "month": days.month,
        "month_name": days.strftime("%B"),
        "month_short": days.strftime("%b"),
        "day_of_month": days.day,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "fiscal_period_label": [f"FY{fy} Q{fq}" for fy, fq in zip(fiscal_year, fiscal_quarter)],
        "is_within_next_12_months": [(0 <= (d - today_ts).days <= 365) for d in days],
        "is_past": [d < today_ts for d in days],
    })


def build_dim_priority_tier() -> pd.DataFrame:
    return pd.DataFrame(PRIORITY_TIER_DIM)


def build_dim_capture_phase() -> pd.DataFrame:
    return pd.DataFrame(CAPTURE_PHASE_DIM)


def _augment_reference(lookup: pd.DataFrame, present_codes, code_col: str, defaults: dict) -> pd.DataFrame:
    """Ensures every code that appears in the facts also exists as a dimension row
    (so every FK resolves). Codes not in the curated lookup are appended with
    conservative, clearly-labeled 'unverified' defaults rather than dropped."""
    if lookup is None or lookup.empty:
        base = pd.DataFrame(columns=[code_col] + list(defaults.keys()))
        known = set()
    else:
        base = lookup.copy()
        known = set(base[code_col].astype(str))
    extras = []
    for code in sorted({str(c) for c in present_codes if c is not None and not (isinstance(c, float) and pd.isna(c))}):
        if code and code not in known:
            extras.append({code_col: code, **defaults})
    if extras:
        base = pd.concat([base, pd.DataFrame(extras)], ignore_index=True)
    return base


# ─── FACTS ────────────────────────────────────────────────────────────────────
SCORING_COMPONENT_LABELS = {
    "capability_match_score": "Capability Match", "urgency_score": "Expiration Urgency",
    "value_score": "Estimated Value", "agency_fit_score": "Agency Fit",
    "set_aside_fit_score": "Set-Aside / Competition Fit", "recompete_confidence_score": "Recompete Confidence",
    "location_fit_score": "Location Fit", "data_quality_score": "Data Quality",
}
FACT_SCORING_BREAKDOWN_COLUMNS = ["candidate_id", "score_component", "weight", "raw_score", "weighted_score"]


def build_fact_scoring_breakdown(scoring_breakdown: pd.DataFrame) -> pd.DataFrame:
    """Unpivots the 8 component scores into long form for Power BI waterfalls /
    decomposition trees: one row per (candidate, component)."""
    if scoring_breakdown.empty:
        return pd.DataFrame(columns=FACT_SCORING_BREAKDOWN_COLUMNS)
    rows = []
    for _, r in scoring_breakdown.iterrows():
        for comp, label in SCORING_COMPONENT_LABELS.items():
            if comp not in scoring_breakdown.columns:
                continue
            weight = SCORING_WEIGHTS[_WEIGHT_KEY_MAP[comp]]
            raw = r[comp]
            rows.append({
                "candidate_id": r["candidate_id"],
                "score_component": label,
                "weight": weight,
                "raw_score": raw,
                "weighted_score": round(raw * weight, 2) if pd.notna(raw) else pd.NA,
            })
    return pd.DataFrame(rows, columns=FACT_SCORING_BREAKDOWN_COLUMNS)


def build_fact_opportunity_notices(sam_records: list, pull_timestamp_utc: str = None) -> pd.DataFrame:
    """Builds the fact_opportunity_notices table from normalized SAM.gov bulk-export
    records (see api_clients.sam_bulk). Reindexes to the canonical schema so column
    order/presence is stable regardless of which fields a given export carries.
    Returns a well-formed empty frame when no notices are present."""
    cols = EMPTY_SCHEMAS["fact_opportunity_notices"]
    if not sam_records:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(sam_records)
    df["pull_timestamp_utc"] = pull_timestamp_utc
    return df.reindex(columns=cols)


def build_dashboard_kpi_summary(recompete_candidates: pd.DataFrame, scoring_breakdown: pd.DataFrame) -> pd.DataFrame:
    if recompete_candidates.empty:
        return pd.DataFrame([{
            "total_estimated_pipeline_value": 0, "recompete_candidate_count": 0,
            "tier_1_count": 0, "expiring_within_12_months_count": 0,
            "top_agency_by_pipeline_value": None, "top_incumbent_by_expiring_value": None,
            "average_pursuit_score": 0, "average_data_quality_score": 0,
        }])
    # Merge in only the scoring_breakdown columns recompete_candidates doesn't
    # already carry: run_pipeline pre-merges pursuit_score/priority_tier before this
    # call, so a blanket merge would collide and suffix them to _x/_y.
    cols_to_merge = ["candidate_id"] + [
        c for c in scoring_breakdown.columns if c != "candidate_id" and c not in recompete_candidates.columns
    ]
    merged = recompete_candidates.merge(scoring_breakdown[cols_to_merge], on="candidate_id", how="left")
    tier1_count = (merged["priority_tier"] == "Tier 1: Pursue Now").sum()
    # Forward reportable frame (0 <= days): expired and undated value is not
    # pipeline. ONE definition, shared verbatim with scripts/rebake_data.py
    # build_kpi_summary so the published KPI means the same thing regardless of
    # which writer ran last.
    days = merged["days_until_expiration"]
    forward_mask = days.notna() & (days >= 0)
    expiring_mask = forward_mask & (days <= 365)
    within_12mo = expiring_mask.sum()
    # Ranked over the SAME forward frame the pipeline-value KPI sums — a blended
    # (expired + undated) ranking next to a forward-only total would be two
    # different books on one row.
    forward_candidates = merged.loc[forward_mask]
    top_agency = (
        forward_candidates.groupby("agency")["total_obligated_amount"].sum().idxmax()
        if not forward_candidates.empty else None
    )
    expiring_candidates = merged.loc[expiring_mask]
    top_incumbent = (
        expiring_candidates.groupby("incumbent_vendor")["total_obligated_amount"].sum().idxmax()
        if not expiring_candidates.empty else None
    )
    return pd.DataFrame([{
        "total_estimated_pipeline_value": merged.loc[forward_mask, "total_obligated_amount"].sum(),
        "recompete_candidate_count": len(merged),
        "tier_1_count": int(tier1_count),
        "expiring_within_12_months_count": int(within_12mo),
        "top_agency_by_pipeline_value": top_agency,
        "top_incumbent_by_expiring_value": top_incumbent,
        "average_pursuit_score": round(merged["pursuit_score"].mean(), 1),
        "average_data_quality_score": round(merged["data_quality_score"].mean(), 1),
    }])


def _enrich_candidates(recompete_candidates, agency_key_map, vendor_key_map) -> pd.DataFrame:
    """Adds foreign keys and the pre-computed bucket/phase/date-key columns Power BI
    relates and sorts on."""
    fact = recompete_candidates.copy()
    new_cols = [
        "agency_key", "vendor_key", "expiration_bucket_sort", "capture_phase",
        "capture_phase_sort", "expiration_date_key", "window_start_date_key", "window_end_date_key",
    ]
    if fact.empty:
        for c in new_cols:
            fact[c] = pd.Series(dtype="object")
        return fact
    fact["agency_key"] = [agency_key_map.get((a, s)) for a, s in zip(fact["agency"], fact["subagency"])]
    fact["vendor_key"] = fact["incumbent_vendor"].map(vendor_key_map)
    fact["expiration_bucket_sort"] = fact["expiration_bucket"].map(EXPIRATION_BUCKET_SORT).fillna(9).astype(int)
    phases = [_capture_phase(d) for d in fact["days_until_expiration"]]
    fact["capture_phase"] = [p[0] for p in phases]
    fact["capture_phase_sort"] = [p[1] for p in phases]
    fact["expiration_date_key"] = _date_key_series(fact["selected_expiration_date"])
    fact["window_start_date_key"] = _date_key_series(fact["estimated_recompete_window_start"])
    fact["window_end_date_key"] = _date_key_series(fact["estimated_recompete_window_end"])
    return fact


# ─── ORCHESTRATION ────────────────────────────────────────────────────────────
def write_powerbi_exports(
    powerbi_dir: Path,
    recompete_candidates: pd.DataFrame, classified_awards: pd.DataFrame,
    incumbent_summary: pd.DataFrame, agency_summary: pd.DataFrame,
    naics_lookup: pd.DataFrame, psc_lookup: pd.DataFrame,
    bridge_table: pd.DataFrame, scoring_breakdown: pd.DataFrame,
    data_quality_report: pd.DataFrame, today: date = None,
    opportunity_notices: pd.DataFrame = None,
    ptw_comparables: pd.DataFrame = None,
    transactions_evidence: pd.DataFrame | None = None,
) -> dict:
    today = today or date.today()
    powerbi_dir = Path(powerbi_dir)
    powerbi_dir.mkdir(parents=True, exist_ok=True)

    # Dimensions with surrogate keys.
    dim_agency = agency_summary.copy()
    agency_key_map = {}
    if not dim_agency.empty:
        dim_agency.insert(0, "agency_key", range(1, len(dim_agency) + 1))
        agency_key_map = {(r["agency"], r["subagency"]): r["agency_key"] for _, r in dim_agency.iterrows()}

    dim_vendor = incumbent_summary.copy()
    vendor_key_map = {}
    if not dim_vendor.empty:
        dim_vendor.insert(0, "vendor_key", range(1, len(dim_vendor) + 1))
        vendor_key_map = {r["incumbent_vendor"]: r["vendor_key"] for _, r in dim_vendor.iterrows()}

    # Facts.
    fact_candidates = _enrich_candidates(recompete_candidates, agency_key_map, vendor_key_map)

    # dim_naics / dim_psc augmented so every fact code resolves to a row.
    present_naics = set()
    present_psc = set()
    for df in (classified_awards, fact_candidates):
        if not df.empty and "naics" in df.columns:
            present_naics.update(df["naics"].dropna().astype(str))
        if not df.empty and "psc" in df.columns:
            present_psc.update(df["psc"].dropna().astype(str))
    dim_naics = _augment_reference(
        naics_lookup, present_naics, "naics_code",
        {"naics_description": "Unknown (auto-added from award data)",
         "cyber_it_relevance_flag": False,
         "cyber_it_relevance_reason": "Present in award data; not in the seed NAICS list — verify manually"},
    )
    dim_psc = _augment_reference(
        psc_lookup, present_psc, "psc_code",
        {"psc_description": "Unknown (auto-added from award data)", "psc_group": "Unknown",
         "cyber_it_relevance_flag": False,
         "cyber_it_relevance_reason": "Present in award data; not in the curated PSC list",
         "verification_status": "unverified_auto_added"},
    )

    # dim_date spanning all model dates + 24 months, always covering today.
    d_min, d_max = _collect_date_bounds(fact_candidates, classified_awards)
    span_start = min(d_min, today) if d_min else today - timedelta(days=730)
    span_end = max(d_max, today) if d_max else today
    span_end = span_end + timedelta(days=730)  # +24 months headroom for the capture calendar
    dim_date = build_dim_date(span_start, span_end, today=today)

    tables = {
        "fact_recompete_candidates": fact_candidates,
        "fact_contract_awards": classified_awards,
        "fact_ptw_comparables": ptw_comparables if ptw_comparables is not None
            else pd.DataFrame(columns=PTW_COMPARABLES_COLUMNS),
        "fact_scoring_breakdown": build_fact_scoring_breakdown(scoring_breakdown),
        "bridge_award_opportunity_links": bridge_table,
        "dim_agency": dim_agency,
        "dim_vendor": dim_vendor,
        "dim_naics": dim_naics,
        "dim_psc": dim_psc,
        "dim_date": dim_date,
        "dim_priority_tier": build_dim_priority_tier(),
        "dim_capture_phase": build_dim_capture_phase(),
        "data_quality_report": data_quality_report,
        "dashboard_kpi_summary": build_dashboard_kpi_summary(recompete_candidates, scoring_breakdown),
    }
    for name, columns in EMPTY_SCHEMAS.items():
        tables[name] = pd.DataFrame(columns=columns)

    # Populate fact_opportunity_notices from real SAM.gov data when provided.
    if opportunity_notices is not None and not opportunity_notices.empty:
        cols = EMPTY_SCHEMAS["fact_opportunity_notices"]
        tables["fact_opportunity_notices"] = opportunity_notices.reindex(columns=cols)

    # Populate fact_transactions from the mods-signal evidence frame when provided;
    # reindex to the schema constant so column order is deterministic (mirrors notices).
    # None (default) leaves the empty scaffold written above untouched.
    if transactions_evidence is not None and not transactions_evidence.empty:
        tables["fact_transactions"] = transactions_evidence.reindex(columns=EMPTY_SCHEMAS["fact_transactions"])

    written = {}
    for name, df in tables.items():
        pii_cols = FREE_TEXT_PII_COLUMNS.get(name)
        if pii_cols:
            df = scrub_free_text_columns(df, pii_cols)
        written[f"{name}.csv"] = _write(df, powerbi_dir, name)
    return written
