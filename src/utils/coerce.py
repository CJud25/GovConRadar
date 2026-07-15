"""
coerce — NaN/None-safe scalar normalization shared across the pipeline.

Consolidates the several near-identical "return '' for None/NaN else a clean string" helpers
that had accreted across transform/scoring/profile modules. A missing value must normalize to
"" (never the literal 'nan'), so two missing values never compare equal or fuzzy-match.

(The app twin `streamlit_app/components/rescore.py` keeps its own `_s` copy on purpose —
the deploy ships without `src/`; a parity test enforces they agree. See that file's header.)
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

_ID_JUNK = re.compile(r"[^A-Za-z0-9]")


def _is_missing(value: Any) -> bool:
    # None, float NaN, pd.NaT, and pd.NA are all "missing". Guard pd.isna against
    # non-scalars (it returns an array for a list/Series -> ambiguous bool -> ValueError).
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def nan_str(value: Any) -> str:
    """Stripped string for a scalar; "" for None/NaN."""
    return "" if _is_missing(value) else str(value).strip()


def clean_code(value: Any) -> str:
    """A NAICS/PSC code as a bare string, dropping float artifacts
    (``541512.0`` -> ``541512``; NAICS/PSC never contain a literal period). "" for None/NaN."""
    return "" if _is_missing(value) else str(value).strip().split(".")[0]


def norm_id(value: Any) -> str:
    """A PIID / solicitation number normalized for comparison: alphanumerics only, uppercased
    (``'SP-1 2024'`` -> ``'SP12024'``). "" for None/NaN."""
    return "" if _is_missing(value) else _ID_JUNK.sub("", str(value)).upper()
