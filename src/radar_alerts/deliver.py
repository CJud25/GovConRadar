"""
deliver.py — the delivery half of the alerting loop: push a rendered digest (built by
``bridge.render_digest`` / ``bridge.build_digest``) out over email or webhook.

Durable-automation discipline: this layer fails LOUD. A missing secret, a partially
configured transport, or a transport/HTTP error is a raise (non-zero exit at the edge),
NEVER a silent "0 changes" success. An honest empty digest (no items after filtering) is
still delivered — the loud-failure rule is about config/transport errors, not empty content.

Functional core + injectable I/O. Transports are stdlib ONLY (``smtplib`` +
``urllib.request``): no new dependency, no ``requests``. (The CRM lead export below also
uses ``pandas`` — already a core repo dependency, still nothing new.) Secrets come from
the environment and are never logged or embedded in output.

User-facing copy (subjects, body, error messages) says "digest"/"update" — the package name
says "alerts" but the word never leaks into text a user reads.
"""

from __future__ import annotations

import json
import math
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Mapping, Protocol

import pandas as pd

from scoring import reason_codes as rc

# Real priority-tier labels (must match bridge/scoring output exactly).
TIER_1 = "Tier 1: Pursue Now"
TIER_2 = "Tier 2: Capture Research"
_ACTION_TIERS = frozenset({TIER_1, TIER_2})

# The one honesty footer every delivered digest carries, on its own line.
FOOTER = "DoD FPDS reporting lags ~90 days; termination signals are ≥3 months old."

_MODES = ("all", "action_needed", "new_only")

# Environment variables read by load_transport_from_env (values NEVER hard-coded / logged).
SMTP_ENV_VARS = (
    "GOVCONRADAR_SMTP_HOST",
    "GOVCONRADAR_SMTP_PORT",
    "GOVCONRADAR_SMTP_USER",
    "GOVCONRADAR_SMTP_PASS",
    "GOVCONRADAR_SMTP_FROM",
    "GOVCONRADAR_SMTP_TO",
)
WEBHOOK_ENV_VAR = "GOVCONRADAR_WEBHOOK_URL"


def filter_items(items: list[dict], mode: str) -> list[dict]:
    """Filter digest items down to what a given delivery mode should carry (pure).

    mode:
      - ``"all"``           — every item, unchanged.
      - ``"action_needed"`` — items that demand a human decision: a *Tier change* whose NEW
        tier is Tier 1 or Tier 2 (parsed from the item's ``new`` field), OR a *New candidate*
        that is already Tier 1 (its ``tier`` field).
      - ``"new_only"``      — *New candidate* items only.

    NOTE: "New notice link" items (bridge.RULE_ORDER[2]) are deliberately dropped by BOTH
    ``action_needed`` and ``new_only`` — a new notice link is neither a tier change nor a new
    candidate. Only ``"all"`` carries them.

    Raises ``ValueError`` on any other mode.
    """
    if mode not in _MODES:
        raise ValueError(f"unknown filter mode {mode!r}; expected one of {_MODES}")
    if mode == "all":
        return list(items)
    if mode == "new_only":
        return [it for it in items if it.get("rule") == "New candidate"]
    # action_needed
    kept: list[dict] = []
    for it in items:
        rule = it.get("rule")
        if rule == "Tier change" and it.get("new") in _ACTION_TIERS:
            kept.append(it)
        elif rule == "New candidate" and it.get("tier") == TIER_1:
            kept.append(it)
    return kept


class Transport(Protocol):
    """A digest transport: something that can send one subject + body. Structural — a test
    fake that records calls satisfies this without inheriting."""

    def send(self, subject: str, body: str) -> None:  # pragma: no cover - protocol stub
        ...


class SmtpTransport:
    """Send a digest as a plain-text email over SMTP with STARTTLS (stdlib ``smtplib``).

    Any ``smtplib`` exception propagates unchanged (loud): a broken transport must not look
    like a successful "0 changes" delivery.
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_addr: str,
        to_addrs: list[str],
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs

    def send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port) as server:
            server.starttls()
            if self.user:
                server.login(self.user, self.password)
            server.send_message(msg)


class WebhookTransport:
    """POST the digest as JSON ``{"subject": ..., "text": ...}`` to a webhook URL (stdlib
    ``urllib.request``).

    A non-2xx response or a ``URLError`` RAISES (loud). Note ``urlopen`` already raises
    ``HTTPError`` (a ``URLError`` subclass) on non-2xx in normal operation; the explicit
    status check covers transports that hand back a non-raising 5xx response object.
    """

    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, subject: str, body: str) -> None:
        payload = json.dumps({"subject": subject, "text": body}).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # A URLError from urlopen propagates unchanged (loud).
        with urllib.request.urlopen(req) as resp:
            status = getattr(resp, "status", None)
            if status is None:
                status = resp.getcode()
            if status is None or not (200 <= status < 300):
                raise RuntimeError(f"digest webhook POST failed with HTTP status {status}")


def load_transport_from_env() -> Transport:
    """Build a Transport from the environment — fully SMTP-configured, or a webhook URL.

    Reads ``GOVCONRADAR_SMTP_{HOST,PORT,USER,PASS,FROM,TO}`` (TO may be comma-separated) OR
    ``GOVCONRADAR_WEBHOOK_URL``. An empty value counts as unset.

    - All six SMTP vars set   -> ``SmtpTransport``.
    - Webhook URL set          -> ``WebhookTransport``.
    - SMTP partially set        -> ``RuntimeError`` naming exactly which vars are missing.
    - Neither configured        -> ``RuntimeError`` with an actionable message naming both options.

    No default, no silent skip: a delivery attempt with no transport is a raise.
    """
    smtp_values = {name: os.environ.get(name, "").strip() for name in SMTP_ENV_VARS}
    missing = [name for name, val in smtp_values.items() if not val]
    webhook = os.environ.get(WEBHOOK_ENV_VAR, "").strip()

    if not missing:
        to_addrs = [addr.strip() for addr in smtp_values["GOVCONRADAR_SMTP_TO"].split(",") if addr.strip()]
        return SmtpTransport(
            host=smtp_values["GOVCONRADAR_SMTP_HOST"],
            port=int(smtp_values["GOVCONRADAR_SMTP_PORT"]),
            user=smtp_values["GOVCONRADAR_SMTP_USER"],
            password=smtp_values["GOVCONRADAR_SMTP_PASS"],
            from_addr=smtp_values["GOVCONRADAR_SMTP_FROM"],
            to_addrs=to_addrs,
        )
    if webhook:
        return WebhookTransport(webhook)
    if len(missing) < len(SMTP_ENV_VARS):
        # Some SMTP vars set but not all — a half-configured email transport is a hard error.
        raise RuntimeError(
            "Incomplete SMTP configuration for digest delivery: set the missing environment "
            f"variable(s): {', '.join(missing)}. All of {', '.join(SMTP_ENV_VARS)} are required "
            "for email delivery."
        )
    raise RuntimeError(
        "No digest delivery transport configured. Set the full SMTP set "
        f"({', '.join(SMTP_ENV_VARS)}) for email, or {WEBHOOK_ENV_VAR} for webhook delivery."
    )


def push_digest(digest_text: str, items: list[dict], mode: str, transport: Transport) -> None:
    """Filter ``items`` by ``mode``, build the subject + body, and hand them to ``transport``.

    Subject carries the filtered change count; body is the passed ``digest_text`` followed by
    the honesty footer on its own line. Sends even when the filtered set is empty — an honest
    "0 change(s)" digest is still a delivery. Config/transport errors raise (loud); empty
    content does not.
    """
    filtered = filter_items(items, mode)
    subject = f"GovConRadar digest — {len(filtered)} change(s)"
    body = f"{digest_text}\n\n{FOOTER}"
    transport.send(subject, body)


# ── CRM lead export ──────────────────────────────────────────────────────────

# Column contract for the CRM lead frame — order pinned, keyed by the stable candidate_id.
CRM_COLUMNS = [
    "candidate_id",
    "agency",
    "subagency",
    "incumbent_vendor",
    "incumbent_uei",
    "total_obligated_amount",
    "ptw_incumbent_runrate",
    "selected_expiration_date",
    "priority_tier",
    "top_reason_code",
    "crm_note",
]

# Short-form per-row lag disclosure. Deliberately the row-sized form of the digest FOOTER
# (header form vs row form — both sanctioned); keep the two consistent if either changes.
CRM_LAG_NOTE = "(DoD FPDS lags ~90d)"

# Mirror of streamlit_app/components/export.py::_defuse_formulas' cell rule (CWE-1236):
# Excel/Sheets execute a cell beginning with = + - @ as a formula, and FPDS-sourced text
# really does start with these (vendor "@MIRE, INC.", titles like "- MPS FOR ...").
# MIRRORED, not imported: a src/ module must not couple to the app package (the import is
# technically feasible via sys.path, but decoupling is the chosen reason). Keep in sync.
_FORMULA_CHARS = ("=", "+", "-", "@")


def _csv_safe(value: object) -> object:
    """Apostrophe-prefix a string cell that begins with a formula char (see _FORMULA_CHARS
    note above) so a downloaded CSV can never execute on open. Non-strings pass through —
    a negative number is data, not a formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_CHARS):
        return "'" + value
    return value


def _fnum(v: object) -> float | None:
    """Finite float or None (None/NaN/inf/unparseable — CSV round-trips hand back strings).
    The None path is what keeps '$None'/'$nan' out of every note."""
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _snorm(v: object) -> str:
    """str -> strip; None / NaN / pandas-missing sentinels -> '' (never the literal 'nan')."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    s = str(v).strip()
    return "" if s in ("nan", "NaN", "NaT", "<NA>") else s


def _usd_compact(x: float) -> str:
    """Compact USD ($1.2M). A tiny LOCAL formatter by design: the app helper
    (streamlit_app/components/theme.usd_short) would couple src/ to the app package, and
    scoring.reason_codes._usd is private — six lines beat either dependency."""
    ax = abs(x)
    if ax >= 1e9:
        return f"${x / 1e9:.1f}B"
    if ax >= 1e6:
        return f"${x / 1e6:.1f}M"
    if ax >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:.0f}"


def _mm_dd(v: object) -> str:
    """'MM/DD' from a date-like value, or '' when absent/unparseable — never 'ends nan'."""
    s = _snorm(v)
    if not s:
        return ""
    ts = pd.to_datetime(s, errors="coerce")
    return "" if pd.isna(ts) else f"{ts.month:02d}/{ts.day:02d}"


def build_crm_rows(
    candidates: pd.DataFrame, profile: Mapping[str, object], reason_cfg: rc.ReasonConfig
) -> pd.DataFrame:
    """Flatten candidates into CRM lead rows — one row per input candidate, keyed by the
    stable ``candidate_id``, input order preserved, columns exactly ``CRM_COLUMNS``.

    ``top_reason_code`` and the note's reason label come from the EXISTING explainability
    layer (``scoring.reason_codes`` with ``component_scores=None`` — the profile-independent
    path, so the profile-driven handlers are skipped; no new scoring happens here). The
    ``crm_note`` one-liner carries: subagency, incumbent, compact $run-rate/yr (the clause is
    DROPPED entirely when ``ptw_incumbent_runrate`` is absent/NaN — never '$None'), 'ends
    MM/DD', the top reason label, and the short-form per-row lag disclosure. Every string
    cell of the returned frame passes through ``_csv_safe``.
    """
    rows: list[dict] = []
    for row in candidates.to_dict("records"):
        chips = rc.reason_codes(row, None, profile, reason_cfg)  # always non-empty by contract
        top = rc.top_chips(chips, 1)[0]

        parts: list[str] = []
        subagency = _snorm(row.get("subagency"))
        if subagency:
            parts.append(subagency)
        vendor = _snorm(row.get("incumbent_vendor"))
        if vendor:
            parts.append(f"incumbent {vendor}")
        runrate = _fnum(row.get("ptw_incumbent_runrate"))
        if runrate is not None:
            parts.append(f"{_usd_compact(runrate)}/yr")
        ends = _mm_dd(row.get("selected_expiration_date"))
        if ends:
            parts.append(f"ends {ends}")
        parts.append(top.text)  # the chip's human label (its rendered template text)
        parts.append(CRM_LAG_NOTE)

        rows.append(
            {
                "candidate_id": row.get("candidate_id"),
                "agency": row.get("agency"),
                "subagency": row.get("subagency"),
                "incumbent_vendor": row.get("incumbent_vendor"),
                "incumbent_uei": row.get("incumbent_uei"),
                "total_obligated_amount": row.get("total_obligated_amount"),
                "ptw_incumbent_runrate": row.get("ptw_incumbent_runrate"),
                "selected_expiration_date": row.get("selected_expiration_date"),
                "priority_tier": row.get("priority_tier"),
                "top_reason_code": top.code,
                "crm_note": " · ".join(parts),
            }
        )

    out = pd.DataFrame(rows, columns=CRM_COLUMNS)
    for col in out.columns:  # defuse every string cell (see _FORMULA_CHARS note)
        s = out[col]
        if s.dtype == object or str(s.dtype).startswith("str"):  # same dtype rule as the app copy
            out[col] = s.astype(object).map(_csv_safe)
    return out
