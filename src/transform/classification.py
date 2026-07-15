"""
classification.py — Weighted cyber/IT classification using NAICS + PSC + keyword
signals. High confidence requires all three signal types together; a single
keyword or code alone is never enough for anything above Low confidence.
"""

import re

import pandas as pd

from utils.config import KEYWORD_TAXONOMY

ALL_KEYWORDS_LOWER = {kw.lower() for group in KEYWORD_TAXONOMY.values() for kw in group}
CYBER_KEYWORDS_LOWER = {kw.lower() for kw in KEYWORD_TAXONOMY["cybersecurity"]}
IT_KEYWORDS_LOWER = {kw.lower() for kw in KEYWORD_TAXONOMY["it_services"]}
COMPLIANCE_TERMS = ["cmmc", "nist", "fisma", "fedramp", "rmf", "ato", "authority to operate"]
SOFTWARE_TERMS = ["software development", "application development", "devsecops"]


def _keyword_hits(text, keyword_set) -> list:
    if not text:
        return []
    text_lower = str(text).lower()
    return [kw for kw in keyword_set if re.search(r'\b' + re.escape(kw) + r'\b', text_lower)]


def classify_record(naics, psc, description, naics_lookup: pd.DataFrame, psc_lookup: pd.DataFrame) -> dict:
    naics_row = naics_lookup[naics_lookup["naics_code"] == naics]
    psc_row = psc_lookup[psc_lookup["psc_code"] == psc]

    naics_relevant = bool(len(naics_row)) and bool(naics_row.iloc[0]["cyber_it_relevance_flag"])
    psc_relevant = bool(len(psc_row)) and bool(psc_row.iloc[0]["cyber_it_relevance_flag"])

    cyber_hits = _keyword_hits(description, CYBER_KEYWORDS_LOWER)
    any_hits = _keyword_hits(description, ALL_KEYWORDS_LOWER)
    keyword_match = bool(any_hits)

    signal_count = sum([naics_relevant, psc_relevant, keyword_match])

    if naics_relevant and psc_relevant and keyword_match:
        confidence = "High"
        reason = f"NAICS {naics} relevant + PSC {psc} relevant + keyword match ({any_hits[:3]})"
    elif signal_count >= 2:
        confidence = "Medium"
        signals = []
        if naics_relevant:
            signals.append(f"NAICS {naics} relevant")
        if psc_relevant:
            signals.append(f"PSC {psc} relevant")
        if keyword_match:
            signals.append(f"keyword match ({any_hits[:3]})")
        reason = " + ".join(signals)
    elif signal_count == 1:
        confidence = "Low"
        if naics_relevant:
            reason = f"NAICS {naics} relevant only — no PSC or keyword corroboration"
        elif psc_relevant:
            reason = f"PSC {psc} relevant only — no NAICS or keyword corroboration"
        else:
            reason = f"Keyword match only ({any_hits[:3]}) — no NAICS or PSC corroboration"
    else:
        confidence = "Not Classified"
        reason = "No NAICS, PSC, or keyword signal found"

    return {
        "cyber_it_flag": confidence != "Not Classified",
        "cyber_flag": bool(cyber_hits),
        "it_services_flag": bool(_keyword_hits(description, IT_KEYWORDS_LOWER)),
        "cloud_flag": bool(_keyword_hits(description, {"cloud"})),
        "data_analytics_flag": bool(_keyword_hits(description, {"data analytics", "database"})),
        "software_dev_flag": bool(_keyword_hits(description, set(SOFTWARE_TERMS))),
        "network_ops_flag": bool(_keyword_hits(description, {"network"})),
        "help_desk_flag": bool(_keyword_hits(description, {"help desk", "service desk"})),
        "compliance_security_flag": bool(_keyword_hits(description, set(COMPLIANCE_TERMS))),
        "classification_confidence": confidence,
        "classification_reason": reason,
    }


def classify_awards(awards_clean: pd.DataFrame, naics_lookup: pd.DataFrame, psc_lookup: pd.DataFrame) -> pd.DataFrame:
    """Applies classify_record to every row and appends the classification columns."""
    if awards_clean.empty:
        base_cols = list(awards_clean.columns)
        extra_cols = [
            "cyber_it_flag", "cyber_flag", "it_services_flag", "cloud_flag", "data_analytics_flag",
            "software_dev_flag", "network_ops_flag", "help_desk_flag", "compliance_security_flag",
            "classification_confidence", "classification_reason",
        ]
        return pd.DataFrame(columns=base_cols + extra_cols)

    result = awards_clean.copy()
    classifications = result.apply(
        lambda row: classify_record(row.get("naics"), row.get("psc"), row.get("description_raw"), naics_lookup, psc_lookup),
        axis=1, result_type="expand",
    )
    return pd.concat([result, classifications], axis=1)
