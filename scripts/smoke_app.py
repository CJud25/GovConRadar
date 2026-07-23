"""
smoke_app.py -- enforced pre-deploy boot smoke check (Corrections v2 Cross-cutting
deploy-boot safety / Reconciliation Collision 8).

Fails LOUDLY (non-zero exit + a precise, file-named message) if the app would not
boot, so a missing or malformed config yaml is caught in the deploy step BEFORE a
user hits a white screen. Two checks, fail-fast, no silent defaults:
  1. every config/*.yaml loads and passes its import-time asserts (via utils.config)
  2. the real Streamlit app entrypoint boots AND every view renders on the committed
     sample without raising (Streamlit's native AppTest harness -- no browser).

Run in the deploy-sync step (and any CI boot gate):  py scripts/smoke_app.py
Exit code is non-zero if the app would not boot.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "streamlit_app"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _check_config() -> int:
    """Eager-load every config/*.yaml through utils.config's import-time asserts.

    A missing file, a YAML ScannerError, a failed structural assert, or a missing key
    all surface here -- named -- instead of white-screening the deployed app.
    """
    try:
        import utils.config  # noqa: F401  -- loads + asserts every config/*.yaml at import
    except FileNotFoundError as exc:
        print(f"SMOKE FAIL [config: missing file]: {exc}", flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001 -- ScannerError/AssertionError/KeyError all name the cause
        print(f"SMOKE FAIL [config: {type(exc).__name__}]: {exc}", flush=True)
        return 1
    return 0


def _check_app_boot() -> int:
    """Boot the real app entrypoint, then render every view via AppTest; any exception fails the deploy."""
    from streamlit.testing.v1 import AppTest

    app = ROOT / "streamlit_app" / "app.py"
    views = [f"views/{p.name}" for p in sorted((ROOT / "streamlit_app" / "views").glob("*.py"))]

    harness = AppTest.from_file(str(app), default_timeout=90)
    harness.run()
    if harness.exception:  # AppTest.exception is an ElementList; empty == no exception raised
        print(f"SMOKE FAIL [app boot]: {harness.exception}", flush=True)
        return 1

    for view in views:
        page = AppTest.from_file(str(app), default_timeout=90)
        page.run()
        page.switch_page(view)
        page.run()
        if page.exception:
            print(f"SMOKE FAIL [view render: {view}]: {page.exception}", flush=True)
            return 1
    return 0


def _check_explorer_empty_search() -> int:
    """Drive the Explorer global Search to a guaranteed no-match term and assert the script
    does not raise. Regression guard for the duplicate keyless _empty_fig plotly-id crash: an
    empty result set is the single most ordinary workbench action and must degrade to a
    'no matches' state, never a fatal error."""
    from streamlit.testing.v1 import AppTest
    app = ROOT / "streamlit_app" / "app.py"
    page = AppTest.from_file(str(app), default_timeout=90)
    page.run()
    page.switch_page("views/explorer.py")
    page.run()
    target = next((t for t in page.text_input if (t.label or "").strip().lower() == "search"), None)
    if target is None:
        print("SMOKE FAIL [explorer empty search]: Search input not found", flush=True)
        return 1
    target.set_value("ZZZNOMATCH_NO_RESULTS")
    page.run()
    if page.exception:
        print(f"SMOKE FAIL [explorer empty search]: {page.exception}", flush=True)
        return 1
    return 0


def _check_company_form_submit() -> int:
    """Submit the Company-profile form and assert it does not raise.

    Regression guard for a form-key / session_state-key collision: the profile form's
    key MUST differ from the "company_profile" key that set_profile() writes into
    st.session_state (a form's key shares Streamlit's widget/session_state namespace).
    If they match, submitting the form raises StreamlitAPIException ("... cannot be
    modified after the widget with key company_profile is instantiated"). The initial-
    render checks above never click submit, so this interaction needs its own gate.
    """
    from streamlit.testing.v1 import AppTest

    app = ROOT / "streamlit_app" / "app.py"

    def _click(page, needle: str) -> bool:
        for b in page.button:  # form_submit_button is exposed via .button too
            if needle.lower() in (b.label or "").lower():
                b.click()
                return True
        return False

    page = AppTest.from_file(str(app), default_timeout=90)
    page.run()
    page.switch_page("views/company.py")
    page.run()
    # Seed the form with the demo profile so submit has a NAICS/capability and proceeds
    # into set_profile() (a blank form short-circuits before the session_state write).
    if not _click(page, "Load demo profile"):
        print("SMOKE FAIL [company form]: 'Load demo profile' button not found", flush=True)
        return 1
    page.run()
    if not _click(page, "Score my pipeline"):
        print("SMOKE FAIL [company form]: 'Score my pipeline' submit button not found", flush=True)
        return 1
    page.run()
    if page.exception:
        print(f"SMOKE FAIL [company form submit]: {page.exception}", flush=True)
        return 1
    return 0


def main() -> int:
    for step in (_check_config, _check_app_boot, _check_explorer_empty_search, _check_company_form_submit):
        rc = step()
        if rc != 0:
            return rc
    print("smoke: OK -- config loads, the app boots on the committed sample, and the "
          "company profile form submits", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
