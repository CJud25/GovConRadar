"""
transform.py — pipeline_demo stage 2 of 4.

Typing + cleaning + a SIMPLIFIED recompete classification and pursuit score that
mirror the production logic, kept deliberately tiny and self-contained (no src/ or
config/ imports) so the demo is deterministic and network-free.

What it mirrors, and where the real thing lives:
  * quality primitives (bucket / status / title flags / clean_title)
        -> src/scoring/quality_flags.py
  * cyber/IT multi-signal classification (NAICS + PSC + keyword)
        -> src/transform/classification.py
  * expiration bucketing + recompete-candidate build
        -> src/transform/recompete.py
  * 8-component weighted Pursuit Score (v2.0.0) + Data-Gap quarantine override
        -> src/scoring/pursuit_score.py  (== streamlit_app/components/rescore.py)

Every score/window here is an ESTIMATE, never an official government prediction —
same contract the production docs state. `score_frame` is exported so validate.py
can RE-SCORE the loaded output and assert it reproduces the baked values (the
scorer-parity invariant), exactly as scripts/validate_data.py does in production.

Run:  python pipeline_demo/transform.py --offline   # extract -> transform, report candidates
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

DEMO_DIR = Path(__file__).resolve().parent
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

import extract  # noqa: E402  (sibling module; sys.path adjusted above)

SNAPSHOT_DATE = extract.SNAPSHOT_DATE
MIN_AWARD_VALUE = 250_000                       # mirrors config/sources.yaml search.min_award_value
SCORER_VERSION = "2.0.0"                        # lockstep with src/scoring/pursuit_score.py

INTERIM_OUT_PATH = DEMO_DIR / "output" / "interim" / "recompete_candidates.parquet"

# ─── quality primitives (mirror of src/scoring/quality_flags.py) ──────────────
STALE_DAYS = -90
SHORT_TITLE_CHARS = 10
GARBLED_MAX_LEN = 400
MIN_MEANINGFUL_CHARS = 4
UNTITLED = "[Untitled award — see source record]"

BUCKET_ORDER = [
    "Expired — verify", "0-6 Months", "6-12 Months",
    "12-18 Months", "18-24 Months", "24+ Months",
]
_BUCKET_SORT = {name: i for i, name in enumerate(BUCKET_ORDER)}
_BUCKET_BOUNDS = [(182, "0-6 Months"), (365, "6-12 Months"), (548, "12-18 Months"), (730, "18-24 Months")]
_GARBLED_PREFIX = re.compile(r"^\d{6}!")
_IGF_CODE = re.compile(r"IGF::[A-Z]{2}::")


def _s(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def _meaningful_len(text: str) -> int:
    return len(re.sub(r"[^A-Za-z0-9]", "", text))


def flag_garbled_title(title) -> bool:
    t = _s(title)
    if _GARBLED_PREFIX.match(t):
        return True
    if t.count("!") >= 2:
        return True
    if len(t) > GARBLED_MAX_LEN:
        return True
    if _meaningful_len(t) < MIN_MEANINGFUL_CHARS:
        return True
    return False


def flag_code_prefix(title) -> bool:
    return "IGF::" in _s(title)


def flag_short_title(title) -> bool:
    return len(_s(title).strip()) < SHORT_TITLE_CHARS


def flag_stale_expiration(days) -> bool:
    if pd.isna(days):
        return False
    return days < STALE_DAYS


def flag_missing_end_date(days) -> bool:
    return pd.isna(days)


def quality_flags(title, days) -> list:
    flags = []
    if flag_garbled_title(title):
        flags.append("garbled_title")
    if flag_code_prefix(title):
        flags.append("code_prefix")
    if flag_short_title(title):
        flags.append("short_title")
    if flag_stale_expiration(days):
        flags.append("stale_expiration")
    if flag_missing_end_date(days):
        flags.append("missing_end_date")
    return flags


def clean_title(title) -> str:
    t = _s(title).strip()
    if not t or flag_garbled_title(t):
        return UNTITLED
    t = _IGF_CODE.sub("", t).strip()
    if not t or flag_garbled_title(t):
        return UNTITLED
    return t


def derive_status(days) -> str:
    if pd.isna(days):
        return "expired_stale"
    if days >= 0:
        return "active"
    if days >= STALE_DAYS:
        return "expired_grace"
    return "expired_stale"


def derive_bucket(days) -> str:
    if pd.isna(days) or days < 0:
        return "Expired — verify"
    for bound, name in _BUCKET_BOUNDS:
        if days <= bound:
            return name
    return "24+ Months"


def bucket_sort(bucket) -> int:
    return _BUCKET_SORT.get(_s(bucket), len(BUCKET_ORDER))


def is_quarantined(title, days) -> bool:
    return (derive_status(days) == "expired_stale"
            or flag_missing_end_date(days)
            or flag_garbled_title(title))


# ─── classification (simplified mirror of src/transform/classification.py) ────
# Production drives NAICS/PSC relevance from lookup tables; here the small relevant
# sets are inlined. High confidence still requires all three signal types together.
_NAICS_RELEVANT = {"541511", "541512", "541513", "541519", "518210", "541330", "541611", "541690"}
_PSC_RELEVANT = {"D307", "D399", "DA01", "D302", "D310"}
_KEYWORDS = [
    "cyber", "cybersecurity", "information assurance", "rmf", "nist", "cmmc", "fisma",
    "fedramp", "zero trust", "soc", "incident response", "vulnerability", "penetration testing",
    "endpoint detection", "edr", "identity and access management", "iam", "cyber defense",
    "help desk", "service desk", "network operations", "cloud migration", "devsecops",
    "data analytics", "database administration", "information technology", "it ",
]


def _keyword_hits(text: str) -> list:
    tl = _s(text).lower()
    return [kw for kw in _KEYWORDS if re.search(r"\b" + re.escape(kw.strip()) + r"\b", tl)]


def classify(naics, psc, description) -> tuple:
    """Return (cyber_it_flag, confidence). Mirrors classification.py's signal count."""
    naics_relevant = _s(naics).split(".")[0] in _NAICS_RELEVANT
    psc_relevant = _s(psc) in _PSC_RELEVANT
    keyword_match = bool(_keyword_hits(description))
    signal_count = sum([naics_relevant, psc_relevant, keyword_match])
    if naics_relevant and psc_relevant and keyword_match:
        confidence = "High"
    elif signal_count >= 2:
        confidence = "Medium"
    elif signal_count == 1:
        confidence = "Low"
    else:
        confidence = "Not Classified"
    return confidence != "Not Classified", confidence


# ─── scorer (mirror of src/scoring/pursuit_score.py v2.0.0) ───────────────────
_WEIGHTS = {
    "capability_match": 0.25, "expiration_urgency": 0.20, "estimated_value": 0.15,
    "agency_fit": 0.10, "set_aside_fit": 0.10, "recompete_confidence": 0.10,
    "location_fit": 0.05, "data_quality": 0.05,
}
_TIER_THRESHOLDS = [(80, "Tier 1: Pursue Now"), (65, "Tier 2: Capture Research"),
                    (50, "Tier 3: Monitor"), (0, "Tier 4: Low Priority")]
_COMPETITIVE_EXTENT_CODES = {"A", "D", "F"}

# Shipped synthetic demo profile (mirrors config/vendor_profile_mock.yaml). Fictional.
DEMO_PROFILE = {
    "capabilities": ["cybersecurity", "cmmc", "nist", "rmf", "help desk", "network operations",
                     "cloud migration", "data analytics", "devsecops"],
    "preferred_naics": ["541512", "541519", "541611"],
    "preferred_psc": ["D307", "D399", "DA01"],
    "agencies_with_past_performance": ["Department of the Army", "Defense Health Agency"],
    "max_comfortable_contract_value": 25_000_000,
    "states_served": ["VA", "MD", "DC", "TX", "CO", "AL", "GA"],
}


def _capability_match(naics, psc, title, p) -> float:
    score = 0
    if _s(naics).split(".")[0] in {str(x) for x in p["preferred_naics"]}:
        score += 40
    if _s(psc) in {str(x) for x in p["preferred_psc"]}:
        score += 30
    tl = _s(title).lower()
    hits = sum(1 for cap in p["capabilities"] if cap.lower() in tl)
    return min(score + min(hits * 15, 30), 100)


def _urgency(days) -> float:
    if pd.isna(days):
        return 0
    if days >= 730:
        return 10
    if days >= 0:
        return round(100 - (days / 730 * 90), 1)
    if days >= -90:
        return round(95 - ((-days) - 1) * (35 / 89), 1)
    return 0


def _value(total, p) -> float:
    if not total or pd.isna(total):
        return 0
    ratio = total / p["max_comfortable_contract_value"]
    if ratio < 0.1:
        return 30
    if ratio <= 1.0:
        return 100
    if ratio <= 3.0:
        return 60
    return 25


def _agency_fit(subagency, p) -> float:
    a = _s(subagency).strip().upper()
    past = {x.strip().upper() for x in p["agencies_with_past_performance"]}
    return 100 if a and a in past else 50


def _set_aside_fit(extent_competed_code, type_of_set_aside_code) -> float:
    sa = _s(type_of_set_aside_code).strip().upper()
    if sa and sa != "NONE":
        return 70
    if _s(extent_competed_code).strip().upper() in _COMPETITIVE_EXTENT_CODES:
        return 55
    return 50


def _recompete_confidence(basis, conf) -> float:
    b = {"potential_end_date": 100, "current_end_date": 75, "unknown": 0}.get(basis, 0)
    c = {"High": 100, "Medium": 65, "Low": 35}.get(conf, 0)
    return round((b + c) / 2, 1)


def _location_fit(state, p) -> float:
    return 100 if _s(state) in {str(x) for x in p["states_served"]} else 30


def _data_quality(flag_count: int) -> float:
    """Neutral 70 for unknown; -15 per quality flag; floor 20. (Demo rows carry no
    free-text notes, so this reduces to the flag-penalty arm of the production rule.)"""
    return max(70 - flag_count * 15, 20)


def _tier(score: float) -> str:
    for threshold, label in _TIER_THRESHOLDS:
        if score >= threshold:
            return label
    return "Tier 4: Low Priority"


def score_row(row, profile=DEMO_PROFILE) -> tuple:
    """Return (pursuit_score, priority_tier) for one candidate (dict/Series-like)."""
    title = row.get("contract_title")
    days = row.get("days_until_expiration")
    flag_count = len(quality_flags(title, days))
    components = {
        "capability_match": _capability_match(row.get("naics"), row.get("psc"), title, profile),
        "expiration_urgency": _urgency(days),
        "estimated_value": _value(row.get("total_obligated_amount"), profile),
        "agency_fit": _agency_fit(row.get("subagency"), profile),
        "set_aside_fit": _set_aside_fit(row.get("extent_competed_code"), row.get("type_of_set_aside_code")),
        "recompete_confidence": _recompete_confidence(row.get("expiration_date_basis"),
                                                      row.get("classification_confidence")),
        "location_fit": _location_fit(row.get("place_of_performance_state"), profile),
        "data_quality": _data_quality(flag_count),
    }
    total = round(sum(components[k] * _WEIGHTS[k] for k in components), 1)
    tier = "Data Gap" if is_quarantined(title, days) else _tier(total)
    return total, tier


def score_frame(df: pd.DataFrame, profile=DEMO_PROFILE) -> pd.DataFrame:
    """Return a copy of df with pursuit_score/priority_tier (re)computed. Used by
    both transform (to bake) and validate (to prove re-score == baked)."""
    if df.empty:
        out = df.copy()
        out["pursuit_score"] = pd.Series(dtype=float)
        out["priority_tier"] = pd.Series(dtype=object)
        return out
    out = df.copy()
    scored = out.apply(lambda r: score_row(r, profile), axis=1)
    out["pursuit_score"] = [s[0] for s in scored]
    out["priority_tier"] = [s[1] for s in scored]
    return out


# ─── transform orchestration ──────────────────────────────────────────────────
def _to_num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_date(value):
    if not value:
        return None
    return pd.to_datetime(value, errors="coerce").date() if not pd.isna(pd.to_datetime(value, errors="coerce")) else None


def run_transform(raw_records: list, snapshot: date = SNAPSHOT_DATE) -> pd.DataFrame:
    """Type/clean each raw record, classify cyber/IT, keep in-scope candidates
    (cyber_it_flag AND obligated >= MIN_AWARD_VALUE), derive expiration bucketing,
    apply quality flags, and bake the pursuit score. Returns the modeled fact frame."""
    rows = []
    for rec in raw_records:
        naics = _s(rec.get("naics_code")).strip()
        psc = _s(rec.get("psc_code")).strip()
        description = rec.get("Description")
        cyber_it_flag, confidence = classify(naics, psc, description)

        amount = _to_num(rec.get("Award Amount"))
        if not cyber_it_flag:
            continue
        if amount is None or amount < MIN_AWARD_VALUE:
            continue

        current_end = _to_date(rec.get("End Date"))
        potential_end = _to_date(rec.get("potential_end_date"))
        if potential_end is not None:
            selected, basis = potential_end, "potential_end_date"
        elif current_end is not None:
            selected, basis = current_end, "current_end_date"
        else:
            selected, basis = None, "unknown"

        if selected is None:
            days = None
        else:
            days = (selected - snapshot).days

        title = description
        piid = rec.get("Award ID")
        rows.append({
            "candidate_id": f"RC-{piid}",
            "piid": piid,
            "referenced_idv_piid": rec.get("referenced_idv_piid"),
            "contract_title": title,
            "title_display": clean_title(title),
            "agency": "DEPARTMENT OF DEFENSE",
            "subagency": _s(rec.get("Awarding Sub Agency")).strip().upper(),
            "incumbent_vendor": rec.get("Recipient Name"),
            "naics": naics,
            "psc": psc,
            "award_type": rec.get("award_type"),
            "selected_expiration_date": selected.isoformat() if selected else None,
            "expiration_date_basis": basis,
            "days_until_expiration": days,
            "candidate_status": derive_status(days),
            "expiration_bucket": derive_bucket(days),
            "expiration_bucket_sort": bucket_sort(derive_bucket(days)),
            "total_obligated_amount": amount,
            "place_of_performance_state": rec.get("Place of Performance State Code"),
            "extent_competed_code": rec.get("extent_competed_code"),
            "type_of_set_aside_code": rec.get("type_of_set_aside_code"),
            "classification_confidence": confidence,
            "number_of_offers_received": rec.get("number_of_offers_received"),
            "source_url": f"https://www.usaspending.gov/award/{piid}" if piid else None,
            "flag_garbled_title": flag_garbled_title(title),
            "flag_code_prefix": flag_code_prefix(title),
            "flag_short_title": flag_short_title(title),
            "flag_stale_expiration": flag_stale_expiration(days),
            "flag_missing_end_date": flag_missing_end_date(days),
        })

    df = pd.DataFrame(rows)
    df = score_frame(df)
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pipeline_demo transform stage.")
    ap.add_argument("--offline", action="store_true", help="read the committed synthetic fixture (default)")
    ap.add_argument("--online", action="store_true", help="best-effort live pull (not for CI)")
    args = ap.parse_args(argv)

    records = extract.run_extract(offline=not args.online)
    df = run_transform(records)
    INTERIM_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(INTERIM_OUT_PATH, index=False)
    print(f"transform: {len(records)} raw -> {len(df)} recompete candidates "
          f"({int((df['priority_tier'] == 'Tier 1: Pursue Now').sum())} Tier 1, "
          f"{int((df['priority_tier'] == 'Data Gap').sum())} Data Gap).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
