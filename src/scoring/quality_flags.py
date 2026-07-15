"""
quality_flags.py — pure, side-effect-free data-quality primitives for Recompete Radar v2.

Title flags, title cleaning, and status/bucket derivation. THE one copy: the app
(rescore.py, data.py), the rebake/validate scripts, and the pipeline exporter all
import from here (app.py puts src/ on sys.path — the same Option D pattern as the
scorer). The former byte-for-byte mirror at streamlit_app/components/quality.py was
collapsed into this module; tests/test_quality.py exercises it directly. Kept
deliberately dependency-light (pandas only).

Design: the product's brand is honesty. Unknown != good. A raw FPDS record dump or a
23-year-expired award is DATA GAP, never a Tier-1 lead. Nothing here ever renders the
raw ~800-char record or an embedded vendor address to a UI surface.
"""
import re

import pandas as pd

# --- constants ---------------------------------------------------------------
STALE_DAYS = -90          # expired more than this many days ago -> stale (quarantine)
SHORT_TITLE_CHARS = 10    # titles shorter than this are suspiciously terse
GARBLED_MAX_LEN = 400     # anything longer is a raw-record dump, not a title
MIN_MEANINGFUL_CHARS = 4  # fewer real alnum chars than this -> junk
UNTITLED = "[Untitled award — see source record]"

# Forward-looking runway buckets, in ascending order. `Expired — verify` is the
# quarantine bucket (rendered in Data-Gap gray, visually separated) and sorts first.
BUCKET_ORDER = [
    "Expired — verify", "0-6 Months", "6-12 Months",
    "12-18 Months", "18-24 Months", "24+ Months",
]
_BUCKET_SORT = {name: i for i, name in enumerate(BUCKET_ORDER)}

# Day upper-bounds (inclusive) for each forward bucket; > last -> "24+ Months".
_BUCKET_BOUNDS = [(182, "0-6 Months"), (365, "6-12 Months"),
                  (548, "12-18 Months"), (730, "18-24 Months")]

_GARBLED_PREFIX = re.compile(r"^\d{6}!")   # e.g. "200109!003581!..."
# The FPDS IGF tag family: colon/semicolon-joined chains of the IGF token and its
# code list (CT/CL/OT, mangled OTF, comma-joined lists), incl. single-colon,
# spaced, semicolon, reversed, and letter-fused ("MIGF::") variants. Prose uses of
# the word IGF ("non-IGF functions") have no adjacent colon run and are untouched.
# OTF is ordered before OT so the mangled 3-letter code wins over its 2-letter prefix.
_SEP = r"\s*[:;]{1,2}\s*"
_CODES = r"(?:CT|CL|OTF|OT)(?:\s*,\s*(?:CT|CL|OTF|OT))*"
_IGF_CODE = re.compile(
    rf"(?:\b[A-Z]?IGF{_SEP}(?:(?:IGF|{_CODES})(?:{_SEP})?)*"      # IGF-led chain (incl. fused XIGF::)
    rf"|(?:\b{_CODES}{_SEP})+IGF\b(?:{_SEP}(?:IGF|{_CODES})\b)*(?:{_SEP})?)"  # code-led chain ending/continuing through IGF
)


# --- helpers -----------------------------------------------------------------
def _s(value) -> str:
    """None/NaN -> '' (never the literal 'nan')."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def _meaningful_len(text: str) -> int:
    return len(re.sub(r"[^A-Za-z0-9]", "", text))


# --- title flags -------------------------------------------------------------
def flag_garbled_title(title) -> bool:
    """A raw pipe/bang-delimited FPDS record dump or otherwise unusable junk."""
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
    """Carries an FPDS inherently-governmental IGF tag (any _IGF_CODE variant), i.e.
    clean_title would alter this title — altered display is always flagged. Prose
    uses of the word IGF (no adjacent colon run) do not flag."""
    return bool(_IGF_CODE.search(_s(title)))


def flag_short_title(title) -> bool:
    """Suspiciously terse (e.g. 'LABOR', 'ORACLE') — real but low-information."""
    return len(_s(title).strip()) < SHORT_TITLE_CHARS


def flag_stale_expiration(days) -> bool:
    """Expired more than the grace window ago. Missing dates are NOT stale here."""
    if pd.isna(days):
        return False
    return days < STALE_DAYS


def flag_missing_end_date(days) -> bool:
    """No usable runway: the expiration date could not be derived."""
    return pd.isna(days)


def quality_flags(title, days) -> list:
    """All active flag names for one candidate (stable order)."""
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


# --- title cleaning ----------------------------------------------------------
def clean_title(title) -> str:
    """Display-safe title. Strips IGF codes; a garbled/raw record collapses to the
    UNTITLED placeholder so the raw payload and any embedded vendor address can never
    reach a UI surface. The raw value stays in `contract_title` and behind source_url."""
    t = _s(title).strip()
    if not t:
        return UNTITLED
    if flag_garbled_title(t):
        return UNTITLED
    t = _IGF_CODE.sub("", t).strip()
    # A mid-title strip can leave doubled whitespace behind — collapse it.
    t = re.sub(r"\s{2,}", " ", t).strip()
    # The tag family covers comma-separated code lists (IGF::CL,CT::IGF), the
    # malformed single-colon variant (IGF::OT:IGF), and the trailing IGF token;
    # a tag-only title strips to empty. Re-check so a stripped remnant never
    # reaches the UI as a "title".
    if not t or flag_garbled_title(t):
        return UNTITLED
    return t


# --- status / bucket derivation ----------------------------------------------
def derive_status(days) -> str:
    """One of: active / expired_grace / expired_stale. Missing runway is treated as
    stale (conservative quarantine — we can never assert an undated award is active)."""
    if pd.isna(days):
        return "expired_stale"
    if days >= 0:
        return "active"
    if days >= STALE_DAYS:
        return "expired_grace"
    return "expired_stale"


def derive_bucket(days) -> str:
    """Runway bucket. Expired/missing -> the 'Expired — verify' quarantine bucket."""
    if pd.isna(days) or days < 0:
        return "Expired — verify"
    for bound, name in _BUCKET_BOUNDS:
        if days <= bound:
            return name
    return "24+ Months"


def bucket_sort(bucket) -> int:
    """Stable sort index for a bucket name (Expired — verify sorts first)."""
    return _BUCKET_SORT.get(_s(bucket), len(BUCKET_ORDER))


def derive_capture_phase(days):
    """(phase, sort) along the capture lifecycle. The one copy —
    export.powerbi_export derives the per-row fact column and dim_capture_phase
    from this, and the app recomputes it against today at load."""
    if days is None or pd.isna(days):
        return "Unknown / Data Gap", 9
    if days < 0:
        return "Expired", 5
    if days <= 90:
        return "Proposal / Submit", 4
    if days <= 180:
        return "Proposal Prep", 3
    if days <= 365:
        return "Capture Planning", 2
    if days <= 540:
        return "Pre-RFP Shaping", 1
    return "Early Watch", 0


def is_quarantined(title, days) -> bool:
    """Rows forced to the Data Gap tier regardless of pursuit score: stale expiry,
    missing end date, or a garbled title with no reliable identity."""
    return (derive_status(days) == "expired_stale"
            or flag_missing_end_date(days)
            or flag_garbled_title(title))
