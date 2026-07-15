"""
data.py — single source of truth for loading the star schema, resolving live vs.
bundled-sample data, the shared sidebar filters, and the consistent
estimate-vs-fact / pull-date caption. Every page uses these so the app behaves
identically everywhere and never re-preps data.
"""

import base64
import html
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from components import rescore
from scoring import quality_flags as quality

# components -> streamlit_app -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
# The FULL published snapshot (~476 MB as CSV). NOT committed (exceeds GitHub file
# limits) — fetch it with `py scripts/download_data.py`. Absent on a fresh clone.
LIVE_DIR = REPO_ROOT / "data" / "powerbi"
# The committed default: a small, seeded, referentially-intact subsample of the full
# snapshot (built by scripts/build_sample.py). A fresh clone / Streamlit Community
# Cloud deploy has no data/powerbi/ and boots on this.
DEFAULT_SAMPLE_DIR = REPO_ROOT / "data" / "sample"
# Legacy last-resort: the tiny fully-synthetic bundle (streamlit_app/generate_sample_data.py).
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "assets" / "sample_data"

_PRIMARY = "fact_recompete_candidates.csv"


def _usable(d: Path) -> bool:
    """A data dir is usable if its primary fact table exists, is non-empty, and is
    readable as CSV."""
    p = d / _PRIMARY
    if not (p.exists() and p.stat().st_size > 0):
        return False
    try:
        pd.read_csv(p, nrows=1)  # readable
        return True
    except Exception:
        return False


def resolve_data_dir() -> tuple[Path, str]:
    """Resolve the star-schema data directory and a short mode label, in order:

      1. $RADAR_DATA_DIR ......................... explicit override (label "custom")
      2. data/powerbi/ .......................... the FULL published snapshot, if
                                                  present locally (label "live")
      3. data/sample/ ........................... the committed seeded subsample
                                                  default (label "sample")
      4. streamlit_app/assets/sample_data/ ...... legacy synthetic bundle (label "sample")

    The full snapshot (data/powerbi/) is not committed; fetch it with
    `py scripts/download_data.py`. A fresh clone therefore boots on data/sample/,
    so the app runs on Streamlit Community Cloud without the pipeline or any data
    download.
    """
    env = os.environ.get("RADAR_DATA_DIR")
    if env:
        return Path(env), "custom"
    if _usable(LIVE_DIR):
        return LIVE_DIR, "live"
    if _usable(DEFAULT_SAMPLE_DIR):
        return DEFAULT_SAMPLE_DIR, "sample"
    return SAMPLE_DIR, "sample"


@st.cache_data(show_spinner=False)
def load_table(data_dir_str: str, name: str) -> pd.DataFrame:
    path = Path(data_dir_str) / f"{name}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


@st.cache_data(show_spinner=False)
def pull_timestamp(data_dir_str: str) -> str:
    """Data as-of (snapshot) date. Read from the baked snapshot_date (the pipeline's
    own pull timestamp, stamped into dashboard_kpi_summary by scripts/rebake_data.py);
    fall back to the raw pull timestamp in fact_opportunity_notices. NEVER file mtime —
    on a fresh clone mtime is deploy time, not data age."""
    kpi = Path(data_dir_str) / "dashboard_kpi_summary.csv"
    if kpi.exists():
        try:
            s = pd.read_csv(kpi, usecols=["snapshot_date"])["snapshot_date"]
            if len(s) and pd.notna(s.iloc[0]):
                return str(pd.to_datetime(s.iloc[0]).date())
        except Exception:
            pass
    notices = Path(data_dir_str) / "fact_opportunity_notices.csv"
    if notices.exists():
        try:
            ts = pd.read_csv(notices, usecols=["pull_timestamp_utc"])["pull_timestamp_utc"]
            if len(ts):
                m = pd.to_datetime(ts, errors="coerce", utc=True).max()
                if pd.notna(m):
                    return str(m.date())
        except Exception:
            pass
    return "unknown"


def snapshot_age_days(as_of: str) -> int | None:
    """How stale the snapshot is vs today (for the freshness banner). None if unknown."""
    try:
        return (date.today() - pd.to_datetime(as_of).date()).days
    except Exception:
        return None


def notice_response_days(deadline, today: date) -> int | None:
    """PURE (D1): days from `today` to a linked SAM.gov notice's response_deadline —
    negative once the window has closed. None for missing/unparseable input. Never a
    strict parse: SAM/USAspending date columns round-trip in more than one shape (a
    known real column carries a trailing ' 00:00:00'), so always coerce via
    pd.to_datetime(errors="coerce") rather than assume a bare YYYY-MM-DD. `today` is
    caller-injected — no clock call in here — matching the app's existing live-
    recompute convention (see _recompute_runtime above): the caller passes date.today()
    so the countdown is recomputed fresh on every render, never baked."""
    ts = pd.to_datetime(deadline, errors="coerce")
    if pd.isna(ts):
        return None
    return (ts.date() - today).days


# ─── Company profile (score-as-YOUR-company) ──────────────────────────────────
# The profile lives in session_state AND the URL (base64 JSON `p=`), matching the
# app's "this view lives in the URL" ethic — configure once, bookmark, share, and
# every teammate sees the same company's board.
_PROFILE_QP = "p"


def _coerce_profile(p: dict) -> dict:
    """Normalize an untrusted profile (from the ?p= URL param) to the exact schema
    the scorer/renderer expect, so a crafted or partial payload can never trigger a
    KeyError/TypeError downstream (which would leak a stack trace)."""
    b = rescore.BLANK_PROFILE
    out = dict(b)
    for k in ("capabilities", "preferred_naics", "preferred_psc",
              "agencies_with_past_performance", "states_served"):
        v = p.get(k)
        out[k] = [str(x)[:120] for x in v][:100] if isinstance(v, list) else []
    mv = p.get("max_comfortable_contract_value")
    out["max_comfortable_contract_value"] = mv if isinstance(mv, (int, float)) and not isinstance(mv, bool) \
        else b["max_comfortable_contract_value"]
    out["nationwide"] = bool(p.get("nationwide"))
    out["is_demo"] = bool(p.get("is_demo"))
    out["company_name"] = str(p.get("company_name", ""))[:100]
    return out


def _profile_from_url():
    raw = st.query_params.get(_PROFILE_QP)
    if not raw or len(raw) > 4000:  # size bound (DoS guard) + presence check
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _coerce_profile(data)


def get_profile() -> dict:
    """The active company profile: a user's saved profile, else the labeled demo."""
    if "company_profile" not in st.session_state:
        p = _profile_from_url()
        if p:
            st.session_state["company_profile"] = p
    return st.session_state.get("company_profile", rescore.DEMO_PROFILE)


def profile_is_custom() -> bool:
    p = st.session_state.get("company_profile")
    return bool(p) and not p.get("is_demo")


def set_profile(profile: dict):
    st.session_state["company_profile"] = profile
    st.query_params[_PROFILE_QP] = base64.urlsafe_b64encode(
        json.dumps(profile, sort_keys=True).encode()).decode()


def clear_profile():
    st.session_state.pop("company_profile", None)
    st.query_params.pop(_PROFILE_QP, None)


def _recompute_runtime(df: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """Re-derive runway-dependent columns against TODAY (not the bake date) so an
    aging deploy never shows a lapsed contract as active. Title flags/title_display
    don't depend on runway, so the baked values are kept."""
    if df.empty or "selected_expiration_date" not in df.columns:
        return df
    exp = pd.to_datetime(df["selected_expiration_date"], errors="coerce")
    df["days_until_expiration"] = (exp - today).dt.days.astype("Int64")
    d = df["days_until_expiration"]
    df["candidate_status"] = [quality.derive_status(x) for x in d]
    df["expiration_bucket"] = [quality.derive_bucket(x) for x in d]
    df["expiration_bucket_sort"] = [quality.bucket_sort(b) for b in df["expiration_bucket"]]
    df["flag_stale_expiration"] = [quality.flag_stale_expiration(x) for x in d]
    df["flag_missing_end_date"] = [quality.flag_missing_end_date(x) for x in d]
    phases = [quality.derive_capture_phase(x) for x in d]  # runway-driven; track today too
    df["capture_phase"] = [p[0] for p in phases]
    df["capture_phase_sort"] = [p[1] for p in phases]
    return df


def reportable_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """The rows that belong in a HEADLINE: everything except the Data Gap quarantine
    (stale-expired, garbled, or missing-end-date). Use this for every headline KPI,
    chart, incumbent rollup, and DEFAULT export so quarantined records never inflate a
    number. The excluded rows are surfaced only via the Needs-Verification strip."""
    if df is None or df.empty or "priority_tier" not in df.columns:
        return df
    return df[df["priority_tier"] != "Data Gap"]


# ─── "Bridge watch" lens (B2): recently lapsed, no successor visible yet ───────
# THE LENS (product definition): candidate_status == "expired_grace" AND
# successor_visible_basis == "none_visible" (both baked by scoring.successor_proxy).
# Column-guarded — the currently-committed sample bundle predates that bake, so both
# columns are absent until a later full bake; the mask degrades to all-False rather
# than raising, exactly like the mods/burn column guards elsewhere in this file.
#
# CRITICAL: filter on the STRING successor_visible_basis column, NEVER on
# `successor_visible == False`. successor_visible is a tri-state object column
# (True / False / None) that round-trips through CSV as the STRING "False" — a
# boolean compare silently matches nothing once the frame has been through a CSV
# reload (the app's real load path), while an in-memory fixture with a real Python
# `False` stays green. That divergence is exactly the bug class this lens must not
# reintroduce; successor_visible_basis is a plain string in every code path.
#
# Single-sourced here (not duplicated per-view) so the predicate AND the fixed copy
# are named once and reused identically by the Explorer checkbox and the Home KPI.
BRIDGE_WATCH_LABEL = "Recently lapsed, no successor visible yet"
BRIDGE_WATCH_COPY = "no successor visible in public data yet (DoD reporting lags ~90 days)"


def bridge_watch_mask(df: pd.DataFrame) -> pd.Series:
    """Pure boolean mask for the bridge-watch lens (see module note above). Column-guarded:
    if either baked column is absent, returns an all-False mask aligned to df's index —
    never raises, never renders a phantom count on a pre-bake bundle."""
    if df is None:
        return pd.Series(dtype=bool)
    if not {"candidate_status", "successor_visible_basis"}.issubset(df.columns):
        return pd.Series(False, index=df.index)
    return (df["candidate_status"] == "expired_grace") & (df["successor_visible_basis"] == "none_visible")


@st.cache_data(show_spinner="Scoring the pipeline for your company…", max_entries=32, ttl=3600)
def _prepared(data_dir_str: str, today_str: str, profile_json: str, scorer_version: str) -> pd.DataFrame:
    """Load candidates, recompute runway vs today, and (re)score against the active
    profile. Cache key includes today + profile + scorer version so a new day, a new
    profile, or a scorer bump all bust the cache (the classic Streamlit stale-cache trap)."""
    df = load_table(data_dir_str, "fact_recompete_candidates").copy()
    if df.empty:
        return df
    df["pursuit_score_demo"] = df.get("pursuit_score")
    df["priority_tier_demo"] = df.get("priority_tier")
    df = _recompute_runtime(df, pd.Timestamp(today_str))
    return rescore.score_candidates(df, json.loads(profile_json))


def get_context() -> dict:
    """Loads everything a page needs once, cached. Candidates are recomputed to today
    and scored against the active profile (demo baseline kept in *_demo cols)."""
    data_dir, mode = resolve_data_dir()
    ds = str(data_dir)
    candidates = _prepared(ds, date.today().isoformat(),
                           json.dumps(get_profile(), sort_keys=True), rescore.SCORER_VERSION)
    return {
        "mode": mode,
        "as_of": pull_timestamp(ds),
        "profile": get_profile(),
        "profile_custom": profile_is_custom(),
        "candidates": candidates,
        "awards": load_table(ds, "fact_contract_awards"),
        "comparables": load_table(ds, "fact_ptw_comparables"),
        "bridge": load_table(ds, "bridge_award_opportunity_links"),
        "notices": load_table(ds, "fact_opportunity_notices"),
        "dim_agency": load_table(ds, "dim_agency"),
        "dim_vendor": load_table(ds, "dim_vendor"),
        "dim_naics": load_table(ds, "dim_naics"),
        "dim_psc": load_table(ds, "dim_psc"),
        "data_quality": load_table(ds, "data_quality_report"),
        # Presence-tolerant: older bundles (incl. the shipped release) have no
        # trust table — surfaces render an honest "not present" caption on None.
        "trust_metrics": load_table(ds, "trust_metrics_report"),
    }


DISCLAIMER = (
    "**Estimates vs. facts:** contract identifiers, agencies, values, and dates are "
    "**facts** from USAspending.gov. Pursuit score, priority tier, recompete windows, and "
    "incumbent vulnerability are **analytical estimates**, not official government predictions. "
    "Pursuit scores are recomputed live from **your** profile inputs against those contract facts — "
    "change your company profile and the scores change."
)


def page_header(title: str, ctx: dict, subtitle: str = None):
    """Canonical header, delegated to the Intelligence-Desk shell so every page
    shares the branded band, data badge, and estimates-vs-facts note."""
    from components import shell  # local import avoids any load-order coupling
    # Titles historically carried an emoji prefix; the shell provides the brand.
    clean_title = title.lstrip("🛰️📡🎯📊🗺️🏢📅✅ ").strip() if title else title
    shell.render_header(ctx, title=clean_title, subtitle=subtitle, disclaimer=DISCLAIMER)


def _options(series: pd.Series):
    return sorted([v for v in series.dropna().unique().tolist()])


# ─── Shareable filter state (persisted in the URL via st.query_params) ─────────
# Filters live in the query string so a view is bookmarkable, deep-linkable, and
# shareable — copy the address bar to hand a teammate the exact same board.
# NB: the whole pipeline is DoD (agency code 097), so `agency` is a constant
# ("DEPARTMENT OF DEFENSE") — the real discriminator is `subagency` (the DoD
# component: Army/Navy/Air Force/DISA/…). Filters/charts group on subagency.
_QP = {"expiration_bucket": "exp", "priority_tier": "tier", "subagency": "component", "state": "state"}
_SEP = "||"  # multiselect values joined with || (agency names contain commas/spaces)


def _qp_list(key: str) -> list:
    raw = st.query_params.get(key)
    return [s for s in raw.split(_SEP) if s] if raw else []


def _qp_set(key: str, values: list):
    if values:
        st.query_params[key] = _SEP.join(map(str, values))
    elif key in st.query_params:
        del st.query_params[key]  # keep the URL clean; preserves other params (e.g. cid)


def sidebar_filters(candidates: pd.DataFrame) -> dict:
    """Render the shared global filters, seeded from and written back to the URL."""
    st.sidebar.header("Control panel")
    if candidates.empty:
        st.sidebar.info("No candidate data loaded.")
        return {}
    sel = {}

    def _ms(label, col, qpkey):
        opts = _options(candidates[col])
        default = [v for v in _qp_list(qpkey) if v in opts]
        return st.sidebar.multiselect(label, opts, default=default, key=f"flt_{qpkey}")

    if "expiration_bucket" in candidates:
        sel["expiration_bucket"] = _ms("Expiration window", "expiration_bucket", _QP["expiration_bucket"])
    if "priority_tier" in candidates:
        sel["priority_tier"] = _ms("Priority tier", "priority_tier", _QP["priority_tier"])
    if "subagency" in candidates:
        sel["subagency"] = _ms("DoD Component", "subagency", _QP["subagency"])
    if "place_of_performance_state" in candidates:
        sel["state"] = _ms("State", "place_of_performance_state", _QP["state"])
    if "total_obligated_amount" in candidates and candidates["total_obligated_amount"].notna().any():
        vmin = float(candidates["total_obligated_amount"].min())
        vmax = float(candidates["total_obligated_amount"].max())
        if vmax > vmin:
            qv = _qp_list("val")
            dv = (float(qv[0]), float(qv[1])) if len(qv) == 2 else (vmin, vmax)
            dv = (max(vmin, dv[0]), min(vmax, dv[1]))
            vr_val = st.sidebar.slider(
                "Estimated value ($)", min_value=vmin, max_value=vmax, value=dv, format="$%d", key="flt_val"
            )
            narrowed = tuple(vr_val) != (vmin, vmax)
            if narrowed:  # only an "active filter" when the user actually narrows the range
                sel["value_range"] = vr_val
            _qp_set("val", list(vr_val) if narrowed else [])
        else:
            _qp_set("val", [])  # min==max: no slider shown — clear any lingering ?val=
    else:
        _qp_set("val", [])

    # Write selections back to the URL so it is the shareable link.
    for key, qpkey in _QP.items():
        _qp_set(qpkey, sel.get(key, []))

    if any(sel.get(k) for k in _QP) or sel.get("value_range"):
        if st.sidebar.button("Clear filters", width="stretch"):
            for qpkey in list(_QP.values()) + ["val"]:
                st.query_params.pop(qpkey, None)
                st.session_state.pop(f"flt_{qpkey}", None)
            st.rerun()
    st.sidebar.caption("🔗 This view lives in the URL — copy the address bar to share it.")
    return sel


def active_filter_chips(sel: dict) -> str:
    """HTML chips summarizing the active filters (for a page's header strip)."""
    labels = {"expiration_bucket": "Window", "priority_tier": "Tier", "subagency": "Component", "state": "State"}
    chips = []
    for key, lab in labels.items():
        for v in sel.get(key, []) or []:
            chips.append(f'<span class="filter-chip">{lab}: {html.escape(str(v))}</span>')  # escape data-sourced HTML
    vr = sel.get("value_range")
    if vr:
        chips.append(f'<span class="filter-chip">Value: {usd(vr[0])}–{usd(vr[1])}</span>')
    return "".join(chips)


def apply_filters(candidates: pd.DataFrame, sel: dict) -> pd.DataFrame:
    df = candidates.copy()
    if not sel:
        return df
    for col, key in [("expiration_bucket", "expiration_bucket"), ("priority_tier", "priority_tier"),
                     ("subagency", "subagency"), ("place_of_performance_state", "state")]:
        chosen = sel.get(key)
        if chosen and col in df:
            df = df[df[col].isin(chosen)]
    vr = sel.get("value_range")
    if vr and "total_obligated_amount" in df:
        df = df[df["total_obligated_amount"].between(vr[0], vr[1])]
    return df


def usd(value) -> str:
    try:
        return f"${value:,.0f}"
    except (TypeError, ValueError):
        return "—"
