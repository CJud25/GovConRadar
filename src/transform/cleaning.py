"""
transformations.py — Parsing/normalization helpers and the awards_clean
table builder. Never drops a row for missing data — flags it instead.
"""

import logging
import re
from datetime import date, datetime

import pandas as pd

logger = logging.getLogger(__name__)

# Schema for the awards_clean table — must stay in sync with the row dict in build_awards_clean
AWARDS_CLEAN_COLUMNS = [
    "award_id",
    "piid",
    "referenced_idv_piid",
    "award_type",
    "awardee_name_raw",
    "awardee_name_clean",
    "awardee_uei",
    "awarding_agency_raw",
    "awarding_agency_clean",
    "awarding_subagency_raw",
    "awarding_subagency_clean",
    "funding_agency_clean",
    "date_signed",
    "total_obligated_amount",
    "potential_value",
    "pop_start_date",
    "pop_current_end_date",
    "pop_potential_end_date",
    "naics",
    "psc",
    "description_raw",
    "extent_competed",
    "extent_competed_code",
    "type_of_contract_pricing",
    "type_of_contract_pricing_code",
    "type_of_set_aside",
    "type_of_set_aside_code",
    "number_of_offers_received",
    "base_and_all_options_value",
    "place_of_performance_state",
    "missing_end_date_flag",
    "missing_vendor_flag",
    "missing_agency_flag",
    "data_quality_notes",
    "source_system",
    "business_size_determination_code",
]


# ─── PII SCRUBBING ────────────────────────────────────────────────────────────
# Contracting-officer / POC contact info (email, phone) must never ship in
# exported free-text columns (data-integrity rule). We redact emails and US phone
# numbers only — names are deliberately left intact (redacting names in isolation
# is error-prone and removing contact info already de-identifies the record).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Area code as either "(804)" (optionally followed by a space) OR three digits
# followed by a space/dot/dash separator, then 3 digits, a REQUIRED separator, and
# 4 digits. Covers every POC format in the data (illustrated with fictional
# 555-01xx numbers: 202-555-0134, "202 555-0137", 202.555-0138, (202) 555-0135,
# 202.555.0136). Separators are deliberately
# REQUIRED — matching bare 10-digit runs redacted legitimate requisition / PO /
# part numbers embedded in descriptions, so we stay conservative and never touch
# them. Digit look-behind/ahead stop the pattern from biting into longer numbers.
_PHONE_RE = re.compile(r"(?<!\d)(?:\(\d{3}\)\s?|\d{3}[\s.\-])\d{3}[\s.\-]\d{4}(?!\d)")


def scrub_free_text_pii(text):
    """Redacts email addresses and US phone numbers from free text.

    Emails -> '[EMAIL REDACTED]', phone numbers -> '[PHONE REDACTED]'. Non-string
    input (None, NaN, numbers) is returned unchanged so it is safe to .map over a
    pandas column that may contain missing values."""
    if not isinstance(text, str):
        return text
    scrubbed = _EMAIL_RE.sub("[EMAIL REDACTED]", text)
    scrubbed = _PHONE_RE.sub("[PHONE REDACTED]", scrubbed)
    return scrubbed


def scrub_free_text_columns(df: pd.DataFrame, columns) -> pd.DataFrame:
    """Returns a copy of df with scrub_free_text_pii applied to each named
    free-text column that is present. Missing columns are skipped silently."""
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = out[col].map(scrub_free_text_pii)
    return out


# ─── PUBLIC-ARTIFACT policy (2026-07-06 scrub-co-names decision) ───────────────
# Columns that must NEVER ship in a PUBLIC artifact (the committed deploy sample
# and the GitHub Release snapshot): free-text fields that can carry contracting-
# officer PERSON NAMES the regex scrub above cannot catch. The LOCAL full export
# keeps them — the Power BI model binds both columns.
PUBLIC_EXCLUDED_COLUMNS = {
    "fact_contract_awards": ["description_raw", "classification_reason"],
    # fact_transactions.description is FPDS transaction free text (mods-signal evidence)
    # that can carry CO person names the regex scrub cannot catch. The mod signals never
    # read the free text, so PUBLIC artifacts drop it entirely; the LOCAL export keeps it.
    "fact_transactions": ["description"],
}

# Titles that carry an explicit point-of-contact intro also carry person names
# ("POC: B SHIRLEY", "ATTN: JAMES WOGE", "CO: <name> - CS: <name>", "TECHNICAL POINT
# OF CONTACT (TPOC): <name>", "MOD TO CHANGE COR TO <name>"). Matching rows have the
# WHOLE title replaced (excising just the name is fragile). The key discriminator is a
# COLON: contact intros use "CO:"/"CS:"/"COR:"/"ATT:", while legitimate compounds use a
# hyphen/space ("CO-TERM", "CO-LOCATION", "PROOF OF CONCEPT (POC)", "ATT CELLULAR",
# "28 CS", "RED HAT ... 64 COR") that must NOT be touched. Verified adversarially against
# the FULL 2026-07-07 snapshot: 13 distinct titles / 20 cells redacted across the 3 shipping
# columns (fact_recompete_candidates.{contract_title,title_display}, notices.title), with
# 0 false positives / 0 false negatives. (A prior leading-"<Name>, COR" alternative was
# REMOVED — over the whole corpus it caught 0 real names and exactly 1 product SKU,
# "ATHOC IWSALERTS, COR PREMIUM CAL"; the new colon forms cover far more real names.)
CONTACT_TITLE_RE = re.compile(
    r"(?:\b(?:POC|TPOC|ATTN)\s*\)?\s*[:\-]"                        # POC: POC- TPOC: (TPOC): ATTN:
    r"|\bATT\s*\)?\s*:"                                            # ATT:  (COLON only — 'ATT-' / '(ATT)' are products)
    r"|\b(?:CO|CS|COR)\s*:\s*[A-Za-z]"                             # CO:/CS:/COR: <Name>  (colon separates from CO-/CS compounds)
    r"|\bCONTRACT(?:ING\s+OFFICER|\s+SPECIALIST)(?:'?S)?(?:\s+REPRESENTATIVE)?\s*:\s*[A-Za-z]"  # CONTRACTING OFFICER: / CONTRACT SPECIALIST: <Name>
    r"|\bPOINT\s+OF\s+CONTACT\b\s*(?:\(TPOC\))?\s*[:\-]"           # (TECHNICAL) POINT OF CONTACT (TPOC): <Name>
    r"|\bCOR\s+TO\b"                                               # CHANGE COR TO <Name>
    r"|\b(?:POC|TPOC)\s+(?:for|is)\b)",                            # POC for / POC is <Name>
    re.IGNORECASE,
)
CONTACT_TITLE_PLACEHOLDER = "[Title withheld — contained point-of-contact details; see source record]"

# A separate leak class: Air Force / AFDW / Space-Force contracting offices that embed
# CONTRACTING-PERSONNEL SURNAMES in the title itself, with NO contact-intro marker
# (shape, names fictionalized: "PKH-FAKESON-MOCKLEY-A1 GARTNER LICENSES", "PKF - SAMPLE -
# EXEMPLAR - ..."). A plain regex or a surname denylist cannot catch these safely (a
# denylist fails OPEN on the next new officer name, and a name list would ship real names
# in this public code), so this rule is STRUCTURAL and fail-SAFE: for the personnel-naming
# offices (PKA/PKF/PKH/PKS/PKP) it redacts the whole title UNLESS the token after the office
# code is a reviewed legit NON-name token — so a brand-new officer surname is withheld,
# never leaked. Excluded by design: PKI (Public Key Infrastructure), PKG ("package"), and
# PKB, which is a *product*-naming office (PKB IDIRECT, PKB-GIGAMON…) — its rare personnel
# titles use two dash-joined caps tokens, caught structurally without a name list.
# Verified over the full 2026-07-07 snapshot: 0 false positives (legit PKA-DCC/WING/RISK/
# PKB-product/PKI/PKG titles kept), 0 false negatives. Because the allowlist is snapshot-
# derived, scripts/validate_data.py carries a PK-personnel CANARY that FAILS the build if a
# personnel-office title reaches a public artifact still followed by a name-shaped token.
# 2026-07-11 (final-approver review): two MORE personnel-naming office classes, enumerated
# over the full snapshot before writing the rules. (a) The Navy MSC `N102` office FAMILY —
# N102A, N102B, and bare N102: ALL 40 enumerated titles embed a person ("N102A <SURNAME> -
# <program>", "IGF::OT::IGF N102B <SURNAME> - ...", "N102 / C. <SURNAME> / ..."), so the
# office code alone redacts, unconditionally (fail-safe: a name-free N102* title is
# over-redacted rather than a new officer name leaked). The re-review round proved why the
# family form matters: a rule hard-coding the literal N102A shipped N102B names straight
# through. `N102[A-Z]?\b` cannot touch product strings like N102KVM-UNN4 (no boundary).
# (b) BARE `PK` reached via an office path ("<ORG>/PK <SURNAME>/<SURNAME>") — same allowlist
# discipline as PK[AFHSP]; ISSO (a role) and PZ (a division suffix) are the two enumerated
# legit continuations.
# 2026-07-11 (release-roundtrip catches): the FULL snapshot (not the 5k sample) holds more
# siblings of the personnel-office family than the sample ever showed — the transposed code
# `PHK`, and FOUR-letter suffixed codes `PKAA`/`PKAB` ("PKAB//SILVA//AMOS - ...",
# "HUNTER/TOUSSAINT/PKAA/REDHAT..."). `PK[AFHSP][A-Z]?\b` covers the whole suffixed family
# while structurally excluding every enumerated legit token (PKG, PKI, PKT, PKR, PKW, PKWY,
# PKSOI — none has a class letter in position 3, or the boundary fails as in PKSOI). The
# leading-surnames branch (<NAME>-<NAME>-<code> / <NAME>/<NAME>/<code>) exists so a
# names-BEFORE-code title can never survive on an allowlisted following token.
# 2026-07-11 (independent-auditor catches, pre-Release): three MORE shapes, each enumerated
# over the full corpus before the rule was written — the `CMK` office (2 titles, both
# "CMK - <NAME>/<NAME>"), a colon-form bare PK ("PK: <NAME>/<NAME> - ...", 1 title), and a
# LEADING colon-joined surname pair with no office prefix at all ("WHITFIELD:WARREN SAF/CND
# ..." — the same officers appear as PKS-WARREN-WHITFIELD elsewhere). The leading-pair rule
# excludes the two enumerated legit heads (IGF FPDS tags, "PROJECT:") rather than trying to
# tell surnames from acronyms in an all-caps corpus.
PK_ALLOWED_NEXT = r"DCC|WING|IACP|MFRC|RISK|SYSTEMS?|AAS|CISO|CONGRESSIONAL|DTA|ISSO|PZ|FY\d+|\d+"
PK_OFFICE_CODES = r"PK[AFHSP][A-Z]?|PHK|CMK"
PK_PERSONNEL_TITLE_RE = re.compile(
    r"(?:"
    rf"\b(?:{PK_OFFICE_CODES})\b[\s:/;,\-]+"
    rf"(?!(?:{PK_ALLOWED_NEXT})\b)"
    r"[A-Za-z][A-Za-z'.\-]*"
    r"|\bPKB\s*-\s*[A-Za-z][A-Za-z'.]*\s*-\s*[A-Za-z]"
    r"|\bN102[A-Z]?\b"
    r"|/\s*PK\b[\s:/;,\-]+"
    rf"(?!(?:{PK_ALLOWED_NEXT})\b)"
    r"[A-Za-z]"
    rf"|\bPK\s*:\s*(?!(?:{PK_ALLOWED_NEXT})\b)[A-Za-z]"
    rf"|\b[A-Za-z][A-Za-z'.]*\s*[-/]\s*[A-Za-z][A-Za-z'.]*\s*[-/]\s*(?:{PK_OFFICE_CODES})\b"
    r"|^(?!(?:IGF|PROJECT|LABOR)\b)[A-Za-z][A-Za-z'.]*:[A-Za-z][A-Za-z'.]*\s"
    r")",
    re.IGNORECASE,
)


def redact_contact_titles(df: pd.DataFrame, columns) -> pd.DataFrame:
    """Returns a copy of df with any title matching CONTACT_TITLE_RE (explicit
    contact intros) OR PK_PERSONNEL_TITLE_RE (office-code personnel surnames) replaced
    whole by CONTACT_TITLE_PLACEHOLDER in each named column that is present.
    For PUBLIC artifacts only — the local full export keeps verbatim titles."""
    out = df.copy()
    for col in columns:
        if col in out.columns:
            titles = out[col].astype(str)
            hits = titles.str.contains(CONTACT_TITLE_RE, na=False) | titles.str.contains(
                PK_PERSONNEL_TITLE_RE, na=False
            )
            if hits.any():  # skip no-op assignment (a str into e.g. an all-NaN
                out.loc[hits, col] = CONTACT_TITLE_PLACEHOLDER  # float col raises)
    return out


def parse_dollar_amount(value):
    """Parses a dollar amount from numeric or string input (handles $, commas, blanks)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(value):
    """Parses USAspending's common date formats: 'YYYY-MM-DD' and 'YYYY-MM-DD HH:MM:SS'."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    logger.warning(f"Unparseable date value: {value!r}")
    return None


def normalize_agency_name(name) -> str:
    if not name or not str(name).strip():
        return "UNKNOWN"
    return " ".join(str(name).split()).strip().upper()


def normalize_vendor_name(name) -> str:
    if not name or not str(name).strip():
        return "UNKNOWN"
    cleaned = " ".join(str(name).split()).strip().upper()
    return cleaned.rstrip(".,")


def build_awards_clean(search_records: list, detail_by_id: dict) -> pd.DataFrame:
    rows = []
    for rec in search_records:
        gid = rec.get("generated_internal_id", "")
        detail = detail_by_id.get(gid, {})
        pop = detail.get("period_of_performance") or {}
        contract_data = detail.get("latest_transaction_contract_data") or {}
        parent_award = detail.get("parent_award") or {}

        pop_current_end = parse_date(rec.get("End Date"))
        pop_potential_end = parse_date(pop.get("potential_end_date"))
        awardee_name = rec.get("Recipient Name")
        awarding_agency = rec.get("Awarding Agency")

        notes = []
        missing_end_date = pop_current_end is None and pop_potential_end is None
        missing_vendor = not awardee_name or not str(awardee_name).strip()
        missing_agency = not awarding_agency or not str(awarding_agency).strip()
        if missing_end_date:
            notes.append("no current or potential end date available")
        if missing_vendor:
            notes.append("recipient name missing")
        if missing_agency:
            notes.append("awarding agency missing")
        if gid not in detail_by_id:
            notes.append("not hydrated via awards detail endpoint (below value/end-date threshold)")

        rows.append({
            "award_id": gid,
            "piid": rec.get("Award ID"),
            "referenced_idv_piid": parent_award.get("piid"),
            "award_type": rec.get("Contract Award Type"),
            "awardee_name_raw": awardee_name,
            "awardee_name_clean": normalize_vendor_name(awardee_name),
            "awardee_uei": rec.get("Recipient UEI"),
            "awarding_agency_raw": awarding_agency,
            "awarding_agency_clean": normalize_agency_name(awarding_agency),
            "awarding_subagency_raw": rec.get("Awarding Sub Agency"),
            "awarding_subagency_clean": normalize_agency_name(rec.get("Awarding Sub Agency")),
            "funding_agency_clean": normalize_agency_name(rec.get("Funding Agency")),
            "date_signed": parse_date(rec.get("Base Obligation Date")),
            "total_obligated_amount": parse_dollar_amount(rec.get("Award Amount")),
            "potential_value": parse_dollar_amount(detail.get("base_and_all_options")),
            "pop_start_date": parse_date(rec.get("Start Date")),
            "pop_current_end_date": pop_current_end,
            "pop_potential_end_date": pop_potential_end,
            "naics": rec.get("naics_code"),
            "psc": rec.get("psc_code"),
            "description_raw": rec.get("Description"),
            "extent_competed": contract_data.get("extent_competed"),
            "extent_competed_code": contract_data.get("extent_competed_code"),
            "type_of_contract_pricing": contract_data.get("type_of_contract_pricing"),
            "type_of_contract_pricing_code": contract_data.get("type_of_contract_pricing_code"),
            "type_of_set_aside": contract_data.get("type_of_set_aside"),
            "type_of_set_aside_code": contract_data.get("type_of_set_aside_code"),
            "number_of_offers_received": contract_data.get("number_of_offers_received"),
            "base_and_all_options_value": parse_dollar_amount(detail.get("base_and_all_options_value")),
            "place_of_performance_state": rec.get("Place of Performance State Code"),
            "missing_end_date_flag": missing_end_date,
            "missing_vendor_flag": missing_vendor,
            "missing_agency_flag": missing_agency,
            "data_quality_notes": "; ".join(notes) if notes else "",
            "source_system": "USAspending",
            # CO's per-procurement size determination CODE ("S"/"O"); carried through so the
            # incumbent size-shift directional flag can read codes, never the text column.
            "business_size_determination_code": detail.get("business_size_determination_code"),
        })

    if not rows:
        return pd.DataFrame(columns=AWARDS_CLEAN_COLUMNS)
    return pd.DataFrame(rows)
