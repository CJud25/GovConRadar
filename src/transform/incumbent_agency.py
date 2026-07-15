"""
incumbent_agency_analysis.py — Incumbent and agency summary tables, built from
recompete_candidates.

incumbent_vulnerability_score is an ESTIMATE (0-100) derived from public signals
only: the value-weighted share of a vendor's FORWARD book — rows with
0 <= days_until_expiration — whose value expires within the next 180 days.
Expired history never enters the denominator, so loading more historical award
data cannot move a vendor's score unless their forward book changes. It is
never presented as a factual prediction that an incumbent will lose a recompete.

Unknown is unforgeable (score = None, never an imputed 0.0), and
vulnerability_basis names the reason:
  - BASIS_INSUFFICIENT_COVERAGE — known-dated value covers < 50% of the
    vendor's total obligated value. Checked FIRST: when most of a book is
    undated, claiming "no forward book" would be an overclaim. This gate is
    synthetic armor only — measured on the current full snapshot it is
    untriggerable (0 of the 3,774 vendors on the 35,964-candidate snapshot);
    it exists to stay honest if sparser data ever loads.
  - BASIS_NO_FORWARD_BOOK — the vendor's known-dated forward book is worth $0
    (includes vendors whose entire dated book is already expired).
  - BASIS_SCORED — the score is present.

Expected shape: the score distribution is bimodal (heavy at 0.0 and 100.0)
because ~42% of full-snapshot vendors (1,589 of 3,774) hold a single contract,
whose forward book is either entirely near-term or not at all. That is the true
shape of the portfolio, not a defect — always surface number_of_cyber_it_awards
(n) next to the score so a 100.0-with-n=1 cannot masquerade as a 100.0-with-n=12.
"""

import pandas as pd

BASIS_SCORED = "value_weighted_near_term_share"
BASIS_NO_FORWARD_BOOK = "no_forward_book"
BASIS_INSUFFICIENT_COVERAGE = "insufficient_expiration_coverage"
VULNERABILITY_UNKNOWN_BASES = frozenset({BASIS_NO_FORWARD_BOOK, BASIS_INSUFFICIENT_COVERAGE})

INCUMBENT_SCHEMA = [
    "incumbent_vendor", "incumbent_uei", "number_of_cyber_it_awards", "total_obligated_amount",
    "expiring_value_6_months", "expiring_value_12_months", "expiring_value_24_months",
    "average_contract_value", "recompete_candidate_count", "incumbent_concentration_score",
    "incumbent_vulnerability_score", "vulnerability_basis", "pct_value_expired",
    "pct_value_unknown_expiration",
]
AGENCY_SCHEMA = [
    "agency", "subagency", "total_cyber_it_obligations", "number_of_contracts",
    "expiring_contract_count_6_months", "expiring_contract_count_12_months",
    "expiring_contract_count_24_months", "expiring_pipeline_value", "average_award_size",
]


def _expiring_value(group, max_days):
    """FORWARD-ONLY: an already-expired contract is not "expiring" — counting it
    overstated expiring value 3.0x on the current full snapshot (40.6% of
    known-dated candidate value is already expired), and worse as history loads."""
    days = group["days_until_expiration"]
    mask = days.notna() & (days >= 0) & (days <= max_days)
    return group.loc[mask, "total_obligated_amount"].sum()


def _vulnerability(group):
    """Vulnerability ESTIMATE for one vendor's candidate rows.

    Returns (score, basis, pct_value_expired, pct_value_unknown_expiration).
    score is a float in [0, 100] when basis == BASIS_SCORED, else None (Unknown —
    see the module docstring for the basis precedence and why it is unforgeable).
    Deobligations can push any of these sums negative, so every sum is clamped at
    >= 0 before shares are computed and the outputs are clamped into [0, 100] — a
    deobligated book can never produce a negative score or a share above 100.
    """
    days = group["days_until_expiration"]
    value = group["total_obligated_amount"]
    known = days.notna()
    vendor_total = max(0.0, value.sum())
    known_value = max(0.0, value[known].sum())
    expired_value = max(0.0, value[known & (days < 0)].sum())
    unknown_value = max(0.0, value[~known].sum())
    forward_value = max(0.0, value[known & (days >= 0)].sum())
    near_term_value = max(0.0, value[known & (days >= 0) & (days <= 180)].sum())

    # Disclosure denominators (share of the vendor's TOTAL obligated value);
    # None when the vendor total is 0 — a share of nothing is not 0.0.
    pct_expired = round(min(100.0, (expired_value / vendor_total) * 100), 1) if vendor_total else None
    pct_unknown = round(min(100.0, (unknown_value / vendor_total) * 100), 1) if vendor_total else None

    # Coverage gate first: with most of the book undated, "no forward book"
    # would claim knowledge we don't have.
    if vendor_total and known_value < 0.5 * vendor_total:
        return None, BASIS_INSUFFICIENT_COVERAGE, pct_expired, pct_unknown
    if forward_value <= 0:
        return None, BASIS_NO_FORWARD_BOOK, pct_expired, pct_unknown
    score = round(min(100.0, (near_term_value / forward_value) * 100), 1)
    return score, BASIS_SCORED, pct_expired, pct_unknown


def build_incumbent_summary(recompete_candidates: pd.DataFrame) -> pd.DataFrame:
    if recompete_candidates.empty:
        return pd.DataFrame(columns=INCUMBENT_SCHEMA)

    total_market_value = recompete_candidates["total_obligated_amount"].sum()
    rows = []
    for vendor, group in recompete_candidates.groupby("incumbent_vendor"):
        vendor_total = group["total_obligated_amount"].sum()
        vulnerability_score, vulnerability_basis, pct_expired, pct_unknown = _vulnerability(group)
        rows.append({
            "incumbent_vendor": vendor,
            "incumbent_uei": group["incumbent_uei"].iloc[0],
            "number_of_cyber_it_awards": len(group),
            "total_obligated_amount": vendor_total,
            "expiring_value_6_months": _expiring_value(group, 180),
            "expiring_value_12_months": _expiring_value(group, 365),
            "expiring_value_24_months": _expiring_value(group, 730),
            "average_contract_value": round(vendor_total / len(group), 2),
            "recompete_candidate_count": len(group),
            "incumbent_concentration_score": round((vendor_total / total_market_value) * 100, 2) if total_market_value else 0,
            "incumbent_vulnerability_score": vulnerability_score,
            "vulnerability_basis": vulnerability_basis,
            "pct_value_expired": pct_expired,
            "pct_value_unknown_expiration": pct_unknown,
        })
    return pd.DataFrame(rows).sort_values("total_obligated_amount", ascending=False).reset_index(drop=True)


def build_agency_summary(recompete_candidates: pd.DataFrame) -> pd.DataFrame:
    if recompete_candidates.empty:
        return pd.DataFrame(columns=AGENCY_SCHEMA)

    rows = []
    for (agency, subagency), group in recompete_candidates.groupby(["agency", "subagency"]):
        # Expiring counts/values are FORWARD-ONLY (0 <= days) — see _expiring_value.
        days = group["days_until_expiration"]
        forward = days.notna() & (days >= 0)
        rows.append({
            "agency": agency, "subagency": subagency,
            "total_cyber_it_obligations": group["total_obligated_amount"].sum(),
            "number_of_contracts": len(group),
            "expiring_contract_count_6_months": int((forward & (days <= 180)).sum()),
            "expiring_contract_count_12_months": int((forward & (days <= 365)).sum()),
            "expiring_contract_count_24_months": int((forward & (days <= 730)).sum()),
            "expiring_pipeline_value": group.loc[forward & (days <= 730), "total_obligated_amount"].sum(),
            "average_award_size": round(group["total_obligated_amount"].mean(), 2),
        })
    return pd.DataFrame(rows).sort_values("total_cyber_it_obligations", ascending=False).reset_index(drop=True)
