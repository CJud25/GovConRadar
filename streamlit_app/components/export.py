"""
export.py — one shared "export the rows in view" control, so every table across
the app downloads consistently (same filename convention, same honest help text:
you get exactly the rows currently on screen, filters + search applied).

Today only CSV is wired; the `_FORMATS` registry leaves room to add xlsx/json
later without touching every call site.
"""
import pandas as pd
import streamlit as st

# src/ is on sys.path (app.py) — import the ONE canonical mod-column order (A2/A7); never
# hand-list a second copy that could drift from src/scoring/mods_signal.py's MOD_COLUMNS.
from scoring.mods_signal import MOD_COLUMNS

# Excel/Sheets execute a cell that begins with = + - @ as a formula, and FPDS-sourced
# text really does start with these (vendor "@MIRE, INC.", titles like "- MPS FOR ...").
# Defuse string cells with a leading apostrophe so a downloaded CSV can never execute
# on open (CWE-1236). Numeric columns are untouched — a negative number is data.
_FORMULA_CHARS = ("=", "+", "-", "@")


def _defuse_formulas(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if s.dtype == object or str(s.dtype).startswith("str"):
            mask = s.notna() & s.astype(str).str.startswith(_FORMULA_CHARS)
            if mask.any():
                out.loc[mask, col] = "'" + s[mask].astype(str)
    return out


# Registry of exportable formats. Each entry: (mime, encoder(df) -> bytes).
# Only CSV is wired today; xlsx/json can register here later.
_FORMATS = {
    "csv": ("text/csv", lambda df: _defuse_formulas(df).to_csv(index=False).encode("utf-8")),
}

_DEFAULT_HELP = "Exports the rows currently in view — filters and search applied."

# Curated, ordered, human-meaningful export columns. Default downloads use these so a
# BD analyst gets a clean, self-explanatory CSV — not surrogate keys (*_key), demo
# baselines (*_demo), or sort helpers. `title_display` (never the raw record) is used.
EXPORT_COLUMNS = [
    "candidate_id", "piid", "referenced_idv_piid", "title_display", "subagency",
    "incumbent_vendor", "incumbent_uei", "naics", "psc", "candidate_status",
    "priority_tier", "pursuit_score", "selected_expiration_date", "days_until_expiration",
    "expiration_bucket", "total_obligated_amount", "potential_value",
    "ceiling_burn_ratio", "burn_pressure", "burn_band", "burn_basis",
    "flag_garbled_title", "flag_code_prefix", "flag_short_title", "flag_stale_expiration",
    "flag_missing_end_date", "source_url",
] + list(MOD_COLUMNS)  # A7: the 15 mod-signal columns, appended in their canonical order —
# existing entries above are untouched; each estimate here already sits immediately
# before its own basis column (termination_kind/termination_basis; ceiling_growth_ratio/
# ceiling_balloon_flag/ceiling_basis; bridge_flag/bridge_basis) by construction of
# MOD_COLUMNS itself.


def curate(df: pd.DataFrame, include_internal: bool = False) -> pd.DataFrame:
    """Project to the curated EXPORT_COLUMNS (default) or return the full frame minus
    obvious internal helpers when the user opts into internal columns.

    Even the internal export NEVER ships the raw `contract_title`: for one record it is
    an ~800-char FPDS dump containing a vendor street address. A downloadable CSV is a
    display surface — only the cleaned `title_display` leaves the building."""
    if include_internal:
        drop = [c for c in df.columns if c.endswith("_demo")]  # baselines are noise even here
        drop += [c for c in ("contract_title",) if c in df.columns]  # never leak the raw record
        drop += [c for c in ("reasons",) if c in df.columns]  # UI chip projection, not a data column
        return df.drop(columns=drop, errors="ignore")
    cols = [c for c in EXPORT_COLUMNS if c in df.columns]
    return df[cols] if cols else df


def export_bar(df, filename_stem, *, label=None, key=None, help=None):
    """Render ONE CSV download button for the rows currently in `df`, using the curated
    column set by default with an "include internal columns" opt-in.

    If `df` is empty, render a caption instead of a dead button. `filename_stem`
    is date-stamped (e.g. recompete_candidates_20260703.csv).
    """
    if df is None or df.empty:
        st.caption("Nothing to export for the current filters.")
        return

    include_internal = st.checkbox("Include internal columns", value=False,
                                   key=f"{key or filename_stem}_internal",
                                   help="Adds surrogate keys, sort helpers, and raw fields. "
                                        "Off by default for a clean, shareable export.")
    out = curate(df, include_internal)
    mime, encode = _FORMATS["csv"]
    label = label or f"⬇ Export {len(out):,} rows (CSV)"
    filename = f"{filename_stem}_{pd.Timestamp.now().strftime('%Y%m%d')}.csv"
    st.download_button(
        label,
        encode(out),
        file_name=filename,
        mime=mime,
        key=key,
        help=help or _DEFAULT_HELP,
    )
