"""
recompete_logic.py — Expiration bucketing and estimated recompete window
logic. Every date/window produced here is an ESTIMATE, never an official
government prediction — see documentation/scoring_methodology.md.
"""

import re
from datetime import date, timedelta

import pandas as pd

from scoring.mods_signal import MOD_COLUMNS, VELOCITY_BAND_NA
from scoring.quality_flags import derive_bucket
from utils.config import EXPIRATION_BASIS_POLICIES, EXPIRATION_BASIS_POLICY

# A USAspending award page resolves at /award/<generated_unique_award_id>, where the id
# is the "generated" natural key: CONT_AWD_… / CONT_IDV_… for contracts/IDVs, ASST_…
# for assistance (e.g. CONT_AWD_N0042125F0246_9700_N0042123D0007_9700). A BARE PIID
# (e.g. 47QTCA20D003A) is NOT that id and does not resolve — verified against the API's
# generated_unique_award_id scheme (usaspending.gov/award/<id>), not a live fetch.
_RESOLVABLE_AWARD_ID = re.compile(r"^(CONT|ASST)_[A-Z]+_")


def is_resolvable_usaspending_award_id(award_id) -> bool:
    """True only for a real USAspending generated_unique_award_id (CONT_AWD_…, CONT_IDV_…,
    ASST_NON_…), which resolves at /award/<id>. A bare PIID / blank / NaN returns False."""
    if award_id is None or (isinstance(award_id, float) and pd.isna(award_id)):
        return False
    return bool(_RESOLVABLE_AWARD_ID.match(str(award_id).strip()))


def build_source_url(award_id):
    """Resolving USAspending award-page URL, or ``None`` when the identifier is only a
    PIID (the schema-drift fallback / legacy case). No fabricated links (§8 honesty rule) —
    a None source_url is rendered as "no direct link" by the app, which already guards it.
    Both the API path and the bulk path now carry the real generated id
    (``contract_award_unique_key`` since the W0 identity fix), so this normally resolves."""
    if not is_resolvable_usaspending_award_id(award_id):
        return None
    return f"https://www.usaspending.gov/award/{str(award_id).strip()}"

RECOMPETE_CANDIDATE_COLUMNS = [
    "candidate_id", "source_award_id", "piid", "referenced_idv_piid", "contract_title",
    "agency", "subagency", "incumbent_vendor", "incumbent_uei", "naics", "psc", "award_type",
    "current_end_date", "potential_end_date", "selected_expiration_date", "expiration_date_basis",
    "days_until_expiration", "expiration_bucket", "total_obligated_amount", "potential_value",
    "base_and_all_options_value", "place_of_performance_state", "extent_competed", "extent_competed_code",
    "type_of_contract_pricing", "type_of_contract_pricing_code", "type_of_set_aside", "type_of_set_aside_code",
    "number_of_offers_received", "pop_start_date", "classification_confidence",
    "estimated_recompete_window_start", "estimated_recompete_window_end", "source_url", "data_quality_notes",
] + list(MOD_COLUMNS)  # 15 mod-signal columns (populated when a mod_summary is provided — [mods A3])

# Insufficient defaults for a candidate with NO digest in the provided mod_summary (defensive:
# on the bulk path every in-scope award folds >=1 txn). Honest "no history read" values —
# never a NaN-neutral or an imputed middle. None-valued keys stay NaN in the frame.
_NO_HISTORY_MOD_DEFAULTS = {
    "terminated": False, "termination_code": None, "termination_action_date": None,
    "termination_kind": "none", "termination_basis": "none",
    "mod_count": 0, "mod_velocity": None, "mod_velocity_band": VELOCITY_BAND_NA,
    "ceiling_growth_ratio": None, "ceiling_balloon_flag": False, "ceiling_basis": "insufficient",
    "has_deobligation": False, "bridge_flag": False, "bridge_basis": "insufficient",
    "mods_basis": "insufficient",
}


def expiration_bucket(days_until_expiration) -> str:
    """Single source of truth: delegates to quality_flags.derive_bucket so the
    182-day boundaries and the 'Expired — verify' handling for past-due rows match
    the published (rebaked) schema — a past-due contract must never land in the
    forward '0-6 Months' bucket. A missing expiration date (None) stays 'Unknown'
    (we do not know when it expires), which is distinct from a known-expired row.
    (A float NaN — which this pipeline never passes here, but a direct caller might —
    routes to 'Expired — verify' via derive_bucket's pd.isna check.)"""
    if days_until_expiration is None:
        return "Unknown"
    return derive_bucket(days_until_expiration)


def select_expiration_date(current_end, potential_end, policy: str = "potential"):
    """Pure: choose which date drives urgency, per `policy`, and name the basis.

    Returns ``(selected_date, basis)`` where basis is one of
    ``"potential_end_date"`` | ``"current_end_date"`` | ``"unknown"``.

    - ``potential``: prefer potential_end (base + all options), else current_end.
      This is the historical default (kept byte-identical).
    - ``current``: prefer current_end (base + exercised options), else potential_end.
    - ``earliest``: the earliest available of the two — the soonest a recompete
      could surface (the most conservative urgency signal).

    NaN/None dates are treated as "not available". If neither is available the
    result is ``(None, "unknown")``.
    """
    has_current = current_end is not None and not pd.isna(current_end)
    has_potential = potential_end is not None and not pd.isna(potential_end)

    if not has_current and not has_potential:
        return None, "unknown"

    if policy == "earliest":
        if has_current and has_potential:
            if current_end <= potential_end:
                return current_end, "current_end_date"
            return potential_end, "potential_end_date"
        if has_current:
            return current_end, "current_end_date"
        return potential_end, "potential_end_date"

    if policy == "current":
        if has_current:
            return current_end, "current_end_date"
        return potential_end, "potential_end_date"

    # default: "potential"
    if has_potential:
        return potential_end, "potential_end_date"
    return current_end, "current_end_date"


def estimate_recompete_window(selected_expiration_date: date, total_value, high_value_threshold: float = 10_000_000):
    """High-value contracts (>= threshold) widen to 18-6 months before expiration;
    smaller contracts narrow to 9-2 months before expiration. Always an estimate."""
    if total_value is not None and total_value >= high_value_threshold:
        start = selected_expiration_date - timedelta(days=548)
        end = selected_expiration_date - timedelta(days=180)
    else:
        start = selected_expiration_date - timedelta(days=270)
        end = selected_expiration_date - timedelta(days=60)
    return start, end


TRUSTWORTHY_CONFIDENCE = ("Medium", "High")


def default_pipeline_view(df: pd.DataFrame, include_all: bool = False) -> pd.DataFrame:
    """The canonical, trustworthy radar view (§3.3): forward-dated + confident + not
    quarantined. Returns a copy so callers cannot mutate the source frame.

    Kept out of the default view unless ``include_all=True``:
      - **expired / missing-date** rows: ``days_until_expiration < 0`` or NaN;
      - **low-confidence** rows: ``classification_confidence`` not in {Medium, High};
      - **Data-Gap quarantine** rows. NB: "Data Gap" is a ``priority_tier`` value (set by
        the scorer's quarantine of stale/missing/garbled rows), *not* an
        ``expiration_bucket`` — spec §3.3 names the bucket, but the live signal is the
        tier (cf. streamlit_app/components/data.py). We exclude it from whichever of
        ``priority_tier`` / ``expiration_bucket`` is present, so the view is correct on
        both the raw candidate frame and the scored frame.

    ``include_all=True`` returns every row (a copy), so nothing is hidden — the
    untrustworthy rows stay one toggle away (product rule: mark loudly, don't hide).
    """
    if include_all:
        return df.copy()

    if "days_until_expiration" not in df.columns or "classification_confidence" not in df.columns:
        raise KeyError(
            "default_pipeline_view requires 'days_until_expiration' and "
            "'classification_confidence' columns"
        )

    forward = pd.to_numeric(df["days_until_expiration"], errors="coerce") >= 0
    confident = df["classification_confidence"].isin(TRUSTWORTHY_CONFIDENCE)
    mask = forward & confident
    for col in ("priority_tier", "expiration_bucket"):
        if col in df.columns:
            mask &= df[col] != "Data Gap"
    return df[mask].copy()


def build_recompete_candidates(
    classified_awards: pd.DataFrame,
    today: date,
    min_award_value: float,
    expiration_basis_policy: str | None = None,
    mod_summary: pd.DataFrame | None = None,
) -> pd.DataFrame:
    # None => the configured default (config/recompete.yaml). Default "potential"
    # keeps historical output byte-identical; callers may override per run.
    policy = (expiration_basis_policy or EXPIRATION_BASIS_POLICY).strip().lower()
    if policy not in EXPIRATION_BASIS_POLICIES:
        # Fail loud on a typo rather than silently mislabel urgency basis (facts vs estimates).
        raise ValueError(f"expiration_basis_policy must be one of {EXPIRATION_BASIS_POLICIES}, got {policy!r}")
    rows = []
    for _, row in classified_awards.iterrows():
        if not row["cyber_it_flag"]:
            continue
        total_value = row.get("total_obligated_amount")
        if pd.isna(total_value) or total_value < min_award_value:
            continue

        current_end = row.get("pop_current_end_date")
        potential_end = row.get("pop_potential_end_date")
        selected_expiration, basis = select_expiration_date(current_end, potential_end, policy)

        if selected_expiration is None:
            days_until, bucket, window_start, window_end = None, "Unknown", None, None
        else:
            days_until = (selected_expiration - today).days
            bucket = expiration_bucket(days_until)
            window_start, window_end = estimate_recompete_window(selected_expiration, total_value)

        rows.append({
            "candidate_id": f"RC-{row['award_id']}",
            "source_award_id": row["award_id"],
            "piid": row["piid"],
            "referenced_idv_piid": row["referenced_idv_piid"],
            "contract_title": row.get("description_raw"),
            "agency": row["awarding_agency_clean"],
            "subagency": row["awarding_subagency_clean"],
            "incumbent_vendor": row["awardee_name_clean"],
            "incumbent_uei": row["awardee_uei"],
            "naics": row["naics"],
            "psc": row["psc"],
            "award_type": row["award_type"],
            "current_end_date": current_end,
            "potential_end_date": potential_end,
            "selected_expiration_date": selected_expiration,
            "expiration_date_basis": basis,
            "days_until_expiration": days_until,
            "expiration_bucket": bucket,
            "total_obligated_amount": total_value,
            "potential_value": row.get("potential_value"),
            "base_and_all_options_value": row.get("base_and_all_options_value"),
            "place_of_performance_state": row.get("place_of_performance_state"),
            "extent_competed": row.get("extent_competed"),
            "extent_competed_code": row.get("extent_competed_code"),
            "type_of_contract_pricing": row.get("type_of_contract_pricing"),
            "type_of_contract_pricing_code": row.get("type_of_contract_pricing_code"),
            "type_of_set_aside": row.get("type_of_set_aside"),
            "type_of_set_aside_code": row.get("type_of_set_aside_code"),
            "number_of_offers_received": row.get("number_of_offers_received"),
            "pop_start_date": row.get("pop_start_date"),
            "classification_confidence": row["classification_confidence"],
            "estimated_recompete_window_start": window_start,
            "estimated_recompete_window_end": window_end,
            "source_url": build_source_url(row.get("award_id")),
            "data_quality_notes": row.get("data_quality_notes", ""),
        })
    if not rows:
        return pd.DataFrame(columns=RECOMPETE_CANDIDATE_COLUMNS)
    frame = pd.DataFrame(rows)
    if mod_summary is None:
        # Backward-compatible default: without a mod_summary the output is byte-identical
        # to the pre-mods pipeline (no mod columns, no ghost-fix) — DO-NOT-TOUCH #8.
        return frame
    return _attach_mod_signals(frame, mod_summary, today)


def _attach_mod_signals(frame: pd.DataFrame, mod_summary: pd.DataFrame, today: date) -> pd.DataFrame:
    """Left-join the 15 mod-signal columns onto the candidates, then apply the GHOST-FIX:
    a `complete_likely` termination retargets the row's expiration to the termination date
    (basis "terminated" — a fourth expiration_date_basis value beside current_end_date /
    potential_end_date / unknown), so an ended contract falls out of the forward
    `default_pipeline_view` through the existing expired-row machinery. The row is KEPT and
    flagged, never dropped (product rule #5); partial_or_unclear terminations keep their
    computed expiration and ride as flags only. Non-terminated rows keep byte-identical
    expiration fields (DO-NOT-TOUCH #8)."""
    n_before = len(frame)
    key_dtype = frame["source_award_id"].dtype  # pandas-3 str dtype; the object-keyed merge would relax it
    frame = frame.merge(
        mod_summary.rename(columns={"award_id": "source_award_id"}),
        on="source_award_id", how="left", validate="many_to_one",
    )
    assert len(frame) == n_before, f"mod_summary join changed rows: {n_before} -> {len(frame)}"
    frame["source_award_id"] = frame["source_award_id"].astype(key_dtype)
    # Candidates absent from the digest bundle get honest insufficient defaults (the join's
    # NaNs are filled per column; None-valued defaults stay NaN — never a neutral impute).
    for col, default in _NO_HISTORY_MOD_DEFAULTS.items():
        if default is not None:
            frame[col] = frame[col].fillna(default)
    # Re-pin dtypes the NaN-bearing left join relaxed to object.
    for col in ("terminated", "ceiling_balloon_flag", "has_deobligation", "bridge_flag"):
        frame[col] = frame[col].astype(bool)
    frame["mod_count"] = frame["mod_count"].fillna(0).astype("Int64")

    # ── GHOST-FIX: only complete_likely terminations with a parseable termination date.
    kind = frame["termination_kind"].astype(str)
    term_dates = pd.to_datetime(frame["termination_action_date"], errors="coerce")
    mask = kind.eq("complete_likely") & term_dates.notna()
    for idx in frame.index[mask]:
        term_date = term_dates.loc[idx].date()
        total_value = frame.at[idx, "total_obligated_amount"]
        days_until = (term_date - today).days
        window_start, window_end = estimate_recompete_window(term_date, total_value)
        frame.at[idx, "selected_expiration_date"] = term_date
        frame.at[idx, "expiration_date_basis"] = "terminated"
        frame.at[idx, "days_until_expiration"] = days_until
        frame.at[idx, "expiration_bucket"] = expiration_bucket(days_until)
        frame.at[idx, "estimated_recompete_window_start"] = window_start
        frame.at[idx, "estimated_recompete_window_end"] = window_end
    return frame
