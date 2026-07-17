"""
reason_codes — explainability layer over the (already-computed) 8-component pursuit score (pure).

Turns a scored candidate row into a short, ordered list of ``ReasonChip``s, each stamped
``observed ●`` (a raw contract fact), ``inferred ◐`` (a threshold band or a neutral baseline the
*locked* scorer substituted for absence of a match), or ``missing ○`` (a fact/profile field required
to assess is absent — no fabricated number). The layer is a **read-only projection**: it consumes the
machine-keyed ``component_scores`` (never recomputes them) and raw baked columns; it **never imports
the scorer tree** (``rescore``/``pursuit_score``/``transform``) — the app adapter owns that edge. No
clock, no RNG, no module-level mutable state. Priors live in ``config/reason_codes.yaml``. Mirrors the
house idiom of ``src/scoring/eligibility.py`` and ``src/scoring/burn_pressure.py``.

Honesty contract (unit- and validator-pinned): for every emitted chip
``glyph == BASIS_GLYPHS[basis]``, ``is_estimate == (basis == "inferred")``, and — the crisp machine
check — ``basis == "missing"`` implies ``evidence`` contains no digit (the ``NOT_REPORTED`` sentinel).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

BASES: tuple[str, ...] = ("observed", "inferred", "missing")
BASIS_GLYPHS: dict[str, str] = {"observed": "●", "inferred": "◐", "missing": "○"}  # ● ◐ ○
BASIS_LABELS: dict[str, str] = {"observed": "Fact", "inferred": "Estimate", "missing": "Not reported"}
NOT_REPORTED: str = "not reported"  # evidence sentinel; NEVER "" ; digit-free by contract
BASIS_RANK: dict[str, int] = {"observed": 0, "inferred": 1, "missing": 2}
GRID_LABEL_WIDTH: int = 18  # Explorer "Why" cell width (replaces the cut `short` field)

SOLE_SOURCE_OFFERS: int = 1  # domain fact: 1 offer on a competed award = sole bid (hardcoded, not a prior)

COMPONENT_KEYS: tuple[str, ...] = (  # mirrors rescore.score_components keys (pinned by a drift test)
    "capability_match",
    "expiration_urgency",
    "estimated_value",
    "agency_fit",
    "set_aside_fit",
    "recompete_confidence",
    "location_fit",
    "data_quality",
)
PROFILE_DRIVEN_KEYS: frozenset[str] = frozenset(  # == rescore.PROFILE_DRIVEN (asserted in tests, NOT imported)
    {"capability_match", "estimated_value", "agency_fit", "location_fit"}
)
COMPETED_CODES: frozenset[str] = frozenset({"A", "D", "F"})  # FPDS extent_competed: actually competed
# tier-6 context chips: rendered in the evidence expander only, never in the executive chip row (§7).
# The `context` flag on each chip is derived from membership here (single source of truth).
CONTEXT_CODES: frozenset[str] = frozenset({"idv_task_order", "data_gap_code_prefix", "data_gap_short_title"})

# Every SIGNAL code — the priority projection must name each exactly once (completeness/uniqueness).
# A signal can emit several template_ids (one per basis case); priority is keyed by signal code.
SIGNAL_CODES: frozenset[str] = frozenset(
    {
        "data_gap_title",
        "data_gap_end_date",
        "data_gap_stale",
        "incumbent_lock",
        "set_aside",
        "urgency",
        "expired_grace",
        "capability",
        "value",
        "agency",
        "location",
        "recompete",
        "ptw",
        "idv_task_order",
        "data_quality",
        "data_gap_code_prefix",
        "data_gap_short_title",
        "displacement",
        "empty_state",
    }
)  # 19 signals (burn chip CUT per Corrections v2 C2.1; competition CUT per Spec 2 §12; displacement lane F1)

# Every template_id the handlers can emit — the completeness contract (load_reason_config validates
# TEMPLATE_IDS == cfg.templates exactly: no missing, no orphan). `agency_baseline_blank` is the
# blank-subagency-safe baseline (Corrections v2 C2.3); there are NO burn / data_quality_neutral /
# agency_subagency_missing templates (all cut). 38 templates.
TEMPLATE_IDS: frozenset[str] = frozenset(
    {
        "incumbent_lock",
        "set_aside_restricted",
        "set_aside_none",
        "set_aside_missing",
        "urgency_near",
        "urgency_soon",
        "urgency_missing",
        "expired_grace",
        "data_gap_stale",
        "data_gap_end_date",
        "data_gap_title",
        "data_gap_code_prefix",
        "data_gap_short_title",
        "capability_strong",
        "capability_partial",
        "capability_missing",
        "value_in_range",
        "value_over",
        "value_missing",
        "agency_pastperf",
        "agency_baseline",
        "agency_baseline_blank",
        "agency_missing",
        "location_in_area",
        "location_out_area",
        "location_nationwide",
        "location_state_missing",
        "location_missing",
        "recompete_conf",
        "recompete_low",
        "recompete_missing",
        "ptw_available",
        "ptw_missing",
        "displacement_signals",
        "displacement_missing",
        "idv_task_order",
        "data_quality_issue",
        "empty_state",
    }
)


@dataclass(frozen=True)
class ReasonChip:
    code: str  # stable signal slug (drift-tested independent of prose): "set_aside", "incumbent_lock", ...
    text: str  # full sentence for Detail AND the source of the Explorer grid label (RAW — render layer escapes)
    basis: str  # one of BASES
    evidence: str  # verbatim/formatted raw fact, or NOT_REPORTED; DIGIT-FREE when basis == "missing"
    priority: int  # ordering rank from cfg.priority[code]
    profile_driven: bool  # True only for the 4 PROFILE_DRIVEN_KEYS chips
    critical: bool = False  # data-gap caveat -> chip-red on render
    context: bool = False  # tier-6 context -> evidence expander only, kept out of the summary chip row

    @property
    def glyph(self) -> str:
        return BASIS_GLYPHS[self.basis]

    @property
    def is_estimate(self) -> bool:
        return self.basis == "inferred"


@dataclass(frozen=True)
class ReasonConfig:
    priority: dict[str, int]  # signal code -> unique rank (complete over the taxonomy)
    capability_strong_min: float  # 70.0
    capability_partial_min: float  # 40.0
    urgency_near_days: int  # 182
    urgency_soon_days: int  # 365
    offers_sentinels: frozenset[int]  # {117, 253}
    offers_max_plausible: int  # 100
    data_quality_neutral: float  # 70.0 (reference; detects the neutral prior)
    max_chips_detail: int  # 12
    max_chips_explorer: int  # 2
    templates: dict[str, str]  # template_id -> string with {placeholders}


# ── grid label (replaces the deleted `short` field) ──
def grid_label(chip: ReasonChip, width: int = GRID_LABEL_WIDTH) -> str:
    """Explorer-grid label: the chip's already-pinned ``text`` truncated to ``width`` chars (… when cut).
    ONE rule, zero new strings — no `shorts:` table to keep honest. The Explorer cell prepends the glyph
    (●/◐/○), so the rendered token never starts with an ASCII formula char regardless of truncation."""
    t = chip.text
    return t if len(t) <= width else t[: width - 1].rstrip() + "…"


# ── config coercion helpers (isinstance-narrowing → mypy-strict clean; typed errors for the stranger) ──
def _cfg_num(raw: Mapping[str, object], key: str) -> float:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"reason_codes config: {key!r} must be a real number, got {v!r}")
    return float(v)


def _cfg_int(raw: Mapping[str, object], key: str) -> int:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"reason_codes config: {key!r} must be an int, got {v!r}")
    return int(v)


def _cfg_int_set(raw: Mapping[str, object], key: str) -> frozenset[int]:
    v = raw[key]
    if not isinstance(v, (list, tuple)):
        raise ValueError(f"reason_codes config: {key!r} must be a list/tuple, got {v!r}")
    out: set[int] = set()
    for x in v:
        if isinstance(x, bool) or not isinstance(x, int):
            raise ValueError(f"reason_codes config: {key!r} entries must be ints, got {x!r}")
        out.add(int(x))
    return frozenset(out)


def _cfg_str_map(raw: Mapping[str, object], key: str) -> dict[str, str]:
    v = raw[key]
    if not isinstance(v, dict):
        raise ValueError(f"reason_codes config: {key!r} must be a mapping, got {v!r}")
    return {str(k): str(val) for k, val in v.items()}


def _cfg_int_map(raw: Mapping[str, object], key: str) -> dict[str, int]:
    v = raw[key]
    if not isinstance(v, dict):
        raise ValueError(f"reason_codes config: {key!r} must be a mapping, got {v!r}")
    out: dict[str, int] = {}
    for k, val in v.items():
        if isinstance(val, bool) or not isinstance(val, int):
            raise ValueError(f"reason_codes config: priority rank for {k!r} must be an int, got {val!r}")
        out[str(k)] = int(val)
    return out


def load_reason_config(raw: Mapping[str, object]) -> ReasonConfig:
    """Coerce + VALIDATE the mapping into a frozen ``ReasonConfig``. This is the SINGLE comprehensive
    validator (the one source of truth for value checks): it raises ``ValueError`` on a wrong-typed
    value; incomplete/duplicate/non-integer priority; ``capability_partial_min`` not in ``[0, strong)``;
    ``urgency_near_days > urgency_soon_days``; ``max_chips_explorer < 1``; ``max_chips_detail <
    max_chips_explorer``; or a ``templates`` map that does not equal ``TEMPLATE_IDS`` exactly (a missing
    or orphan template). ``config.py`` keeps only a fast import-time structural assert (priority
    completeness/uniqueness) for the shipped yaml; every other check lives here alone. Callers pass
    ``utils.config.REASON_CODES``."""
    priority = _cfg_int_map(raw, "priority")
    templates = _cfg_str_map(raw, "templates")
    cfg = ReasonConfig(
        priority=priority,
        capability_strong_min=_cfg_num(raw, "capability_strong_min"),
        capability_partial_min=_cfg_num(raw, "capability_partial_min"),
        urgency_near_days=_cfg_int(raw, "urgency_near_days"),
        urgency_soon_days=_cfg_int(raw, "urgency_soon_days"),
        offers_sentinels=_cfg_int_set(raw, "offers_sentinels"),
        offers_max_plausible=_cfg_int(raw, "offers_max_plausible"),
        data_quality_neutral=_cfg_num(raw, "data_quality_neutral"),
        max_chips_detail=_cfg_int(raw, "max_chips_detail"),
        max_chips_explorer=_cfg_int(raw, "max_chips_explorer"),
        templates=templates,
    )
    # priority completeness + uniqueness (so cfg.priority[code] can never KeyError at render time)
    if set(cfg.priority) != SIGNAL_CODES:
        missing = sorted(SIGNAL_CODES - set(cfg.priority))
        orphan = sorted(set(cfg.priority) - SIGNAL_CODES)
        raise ValueError(
            f"reason_codes: priority must name every signal exactly once (missing={missing}, orphan={orphan})"
        )
    if len(set(cfg.priority.values())) != len(cfg.priority):
        raise ValueError("reason_codes: priority ranks must be unique")
    # threshold ordering / ranges
    if not (0.0 <= cfg.capability_partial_min < cfg.capability_strong_min):
        raise ValueError("reason_codes: 0 <= capability_partial_min < capability_strong_min required")
    if not (0 <= cfg.urgency_near_days <= cfg.urgency_soon_days):
        raise ValueError("reason_codes: 0 <= urgency_near_days <= urgency_soon_days required")
    if cfg.max_chips_explorer < 1:
        raise ValueError("reason_codes: max_chips_explorer must be >= 1")
    if cfg.max_chips_detail < cfg.max_chips_explorer:
        raise ValueError("reason_codes: max_chips_detail must be >= max_chips_explorer")
    # template completeness + no orphan (every emittable template_id present, nothing extra)
    if set(cfg.templates) != TEMPLATE_IDS:
        missing_t = sorted(TEMPLATE_IDS - set(cfg.templates))
        orphan_t = sorted(set(cfg.templates) - TEMPLATE_IDS)
        raise ValueError(
            f"reason_codes: templates must match TEMPLATE_IDS exactly (missing={missing_t}, orphan={orphan_t})"
        )
    return cfg


# ── typed value helpers (mirror the eligibility / burn_pressure precedent) ──
def _to_float(v: object) -> float | None:
    """None on None/NaN/inf/unparseable; a finite float otherwise. May be negative."""
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _to_int(v: object) -> int | None:
    f = _to_float(v)
    return None if f is None else int(round(f))


def _norm_str(v: object) -> str:
    """str -> strip; None / NaN / pandas-missing sentinels -> "" (never the literal 'nan'/'<NA>')."""
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v).strip()
    return "" if s in ("nan", "NaN", "NaT", "<NA>") else s


def _norm_code(v: object) -> str:
    """str -> strip -> drop a single trailing '.0' (a CSV round-trip can hand back a float-looking code)."""
    s = _norm_str(v)
    return s[:-2] if s.endswith(".0") else s


def _as_list(v: object) -> list[object]:
    return list(v) if isinstance(v, (list, tuple)) else []


def _flag(row: Mapping[str, object], key: str) -> bool:
    """Truthiness of a boolean quality flag, NaN-safe."""
    v = row.get(key)
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return bool(v)


def _usd(x: float | None) -> str:
    if x is None:
        return NOT_REPORTED
    ax = abs(x)
    if ax >= 1e9:
        return f"${x / 1e9:.1f}B"
    if ax >= 1e6:
        return f"${x / 1e6:.1f}M"
    if ax >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:.0f}"


def _approx(a: float, b: float, tol: float = 0.5) -> bool:
    return abs(a - b) <= tol


# ── chip factory ──
def _mk(
    signal: str,  # the signal code (keys cfg.priority + CONTEXT_CODES); named `signal`, not `code`, so a
    template_id: str,  # `{code}` template placeholder (e.g. set_aside_restricted) never collides with it
    basis: str,
    cfg: ReasonConfig,
    *,
    evidence: str,
    profile_driven: bool = False,
    critical: bool = False,
    **fmt: object,
) -> ReasonChip:
    template = cfg.templates[template_id]
    text = template.format(**fmt) if fmt else template
    return ReasonChip(
        code=signal,
        text=text,
        basis=basis,
        evidence=evidence,
        priority=cfg.priority[signal],
        profile_driven=profile_driven,
        critical=critical,
        context=signal in CONTEXT_CODES,
    )


# ── profile-INDEPENDENT handlers (each returns one chip or None) ──
def _h_data_gap_title(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    if not _flag(row, "flag_garbled_title"):
        return None
    return _mk(
        "data_gap_title",
        "data_gap_title",
        "observed",
        cfg,
        critical=True,
        evidence="Source title flagged as garbled/unusable; the raw record is hidden from the pipeline.",
    )


def _h_data_gap_end_date(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    if not _flag(row, "flag_missing_end_date"):
        return None
    return _mk(
        "data_gap_end_date",
        "data_gap_end_date",
        "observed",
        cfg,
        critical=True,
        evidence="No end date could be derived from the source record; quarantined as a Data Gap.",
    )


def _h_data_gap_stale(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    stale = _norm_str(row.get("candidate_status")) == "expired_stale" or _flag(row, "flag_stale_expiration")
    if not stale:
        return None
    return _mk(
        "data_gap_stale",
        "data_gap_stale",
        "observed",
        cfg,
        critical=True,
        evidence="Expired well beyond the 90-day grace window; quarantined as a Data Gap.",
    )


def _h_incumbent_lock(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    offers = _to_int(row.get("number_of_offers_received"))
    if offers is None or offers != SOLE_SOURCE_OFFERS:
        return None
    if offers in cfg.offers_sentinels or offers > cfg.offers_max_plausible:
        return None
    extent = _norm_code(row.get("extent_competed_code")).upper()
    if extent not in COMPETED_CODES:
        return None
    # C2.2: the `●` fact is only "sole bid on a competed award"; the incumbent-lock read is in evidence.
    return _mk(
        "incumbent_lock",
        "incumbent_lock",
        "observed",
        cfg,
        evidence=f"{offers} offer on a competed award (extent {extent}) - a de-facto incumbent lock, "
        "the strongest public entrenchment signal.",
    )


def _h_set_aside(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    code = _norm_code(row.get("type_of_set_aside_code"))
    if code == "":  # blank/null — NOT the same as full-and-open
        return _mk("set_aside", "set_aside_missing", "missing", cfg, evidence=NOT_REPORTED)
    if code.upper() == "NONE":
        return _mk(
            "set_aside",
            "set_aside_none",
            "observed",
            cfg,
            evidence="Set-aside coded NONE - no set-aside restriction (competition extent is a separate FPDS field).",
        )
    return _mk("set_aside", "set_aside_restricted", "observed", cfg, evidence=f"FPDS set-aside code {code}.", code=code)


def _h_urgency(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    days = _to_int(row.get("days_until_expiration"))
    date = _norm_str(row.get("selected_expiration_date")) or "date unknown"
    if days is None:  # NaN days — runway unknown, never "not urgent"
        return _mk("urgency", "urgency_missing", "missing", cfg, evidence=NOT_REPORTED)
    if days < 0 or days > cfg.urgency_soon_days:
        return None  # expired (grace/stale handled elsewhere) or comfortably far out — no chip
    template_id = "urgency_near" if days <= cfg.urgency_near_days else "urgency_soon"
    return _mk(
        "urgency",
        template_id,
        "observed",
        cfg,
        evidence=f"Ends {date} - {days} days of runway.",
        date=date,
        days=days,
    )


def _h_expired_grace(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    if _norm_str(row.get("candidate_status")) != "expired_grace":
        return None
    date = _norm_str(row.get("selected_expiration_date")) or "recently"
    return _mk(
        "expired_grace",
        "expired_grace",
        "observed",
        cfg,
        evidence=f"Expired {date}, within the 90-day grace window - verify current status on SAM.gov.",
    )


def _h_recompete(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    conf = _norm_str(row.get("classification_confidence"))
    if conf == "Low":
        return _mk(
            "recompete",
            "recompete_low",
            "inferred",
            cfg,
            evidence="Recompete classifier confidence is Low - verify this is a genuine cyber/IT recompete.",
        )
    if conf in ("High", "Medium"):
        return _mk(
            "recompete",
            "recompete_conf",
            "inferred",
            cfg,
            evidence=f"Recompete classifier confidence is {conf}.",
            level=conf,
        )
    # null/blank OR any unrecognized non-null value (e.g. the awards-only "Not Classified") -> missing.
    return _mk("recompete", "recompete_missing", "missing", cfg, evidence=NOT_REPORTED)


def _h_ptw(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    if "ptw_basis" not in row:  # presence-gated — a pre-PTW bundle yields no chip, no KeyError
        return None
    basis = _norm_str(row.get("ptw_basis"))
    if basis == "comparables":
        strength = _norm_str(row.get("ptw_data_strength")) or "unrated"
        n = _to_int(row.get("ptw_n_comparables"))
        n_disp = n if n is not None else 0
        return _mk(
            "ptw",
            "ptw_available",
            "observed",
            cfg,
            evidence=f"A price range is estimable from {n_disp} comparable public awards (strength: {strength}).",
            strength=strength,
            n=n_disp,
        )
    return _mk("ptw", "ptw_missing", "missing", cfg, evidence=NOT_REPORTED)


def _h_displacement(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    """The incumbent-displacement lane chip (F1). Presence-gated like ``_h_ptw`` — a pre-lane
    bundle yields no chip, no KeyError. Reads only the baked lane columns
    (scoring.incumbent_displacement) — never recomputes them. Fired signals -> an inferred
    (ESTIMATE) chip naming k of n; an insufficient lane -> a missing chip (digit-free);
    an observed-but-quiet lane -> NO chip (absence speaks — mirrors ``_h_urgency``)."""
    if "displacement_basis" not in row:
        return None
    basis = _norm_str(row.get("displacement_basis"))
    if basis == "insufficient":
        return _mk("displacement", "displacement_missing", "missing", cfg, evidence=NOT_REPORTED)
    if basis != "observed":
        return None  # blank/NaN basis (partially-baked bundle) — never guessed
    k = _to_int(row.get("displacement_signal_count"))
    n = _to_int(row.get("displacement_signals_read"))
    if k is None or n is None or k < 1:
        return None  # zero signals observed — no chip
    fired = _norm_str(row.get("displacement_signals")).replace("+", ", ").replace("_", " ")
    return _mk(
        "displacement",
        "displacement_signals",
        "inferred",
        cfg,
        evidence=f"{k} of {n} readable displacement signals fired ({fired or 'unnamed'}) - "
        "a categorical lane over baked facts, never blended into the pursuit score.",
        k=k,
        n=n,
    )


def _h_idv_task_order(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    idv = _norm_str(row.get("referenced_idv_piid"))
    if idv == "":
        return None
    return _mk(
        "idv_task_order",
        "idv_task_order",
        "observed",
        cfg,
        evidence=f"References parent IDV {idv}; this is a task-order sequence, not a standalone recompete.",
        idv=idv,
    )


def _h_data_quality(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    note = _norm_str(row.get("data_quality_notes"))
    if note == "":  # neutral branch emits nothing (Corrections v2 C2.3 — the absence of gap chips says "no issues")
        return None
    return _mk(
        "data_quality",
        "data_quality_issue",
        "observed",
        cfg,
        evidence=f"Recorded data-quality note: {note}.",
        note=note,
    )


def _h_data_gap_code_prefix(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    if not _flag(row, "flag_code_prefix"):
        return None
    return _mk(
        "data_gap_code_prefix",
        "data_gap_code_prefix",
        "observed",
        cfg,
        evidence="Source title carried an IGF-code prefix; stripped during normalization.",
    )


def _h_data_gap_short_title(row: Mapping[str, object], cfg: ReasonConfig) -> ReasonChip | None:
    if not _flag(row, "flag_short_title"):
        return None
    return _mk(
        "data_gap_short_title",
        "data_gap_short_title",
        "observed",
        cfg,
        evidence="Source title was very short; lower confidence in the parsed fields.",
    )


# ── profile-DRIVEN handlers (skipped entirely when component_scores is None — the Explorer path) ──
def _capability_matched(row: Mapping[str, object], profile: Mapping[str, object]) -> str:
    """Describe WHICH profile dimensions overlap the row (evidence only — reads raw facts, never scores)."""
    parts: list[str] = []
    naics = _norm_str(row.get("naics")).split(".")[0]
    if naics and naics in {str(x) for x in _as_list(profile.get("preferred_naics"))}:
        parts.append(f"NAICS {naics}")
    psc = _norm_str(row.get("psc"))
    if psc and psc in {str(x) for x in _as_list(profile.get("preferred_psc"))}:
        parts.append(f"PSC {psc}")
    title = _norm_str(row.get("contract_title")).lower()
    hits = sum(1 for cap in _as_list(profile.get("capabilities")) if str(cap).lower() in title)
    if hits:
        parts.append(f"{hits} capability keyword" + ("" if hits == 1 else "s"))
    return ", ".join(parts) if parts else "profile overlap"


def _h_capability(
    row: Mapping[str, object], comps: Mapping[str, float], profile: Mapping[str, object], cfg: ReasonConfig
) -> ReasonChip | None:
    raw = comps.get("capability_match")
    if raw is None:
        return None
    if not (profile.get("preferred_naics") or profile.get("preferred_psc") or profile.get("capabilities")):
        return _mk("capability", "capability_missing", "missing", cfg, evidence=NOT_REPORTED, profile_driven=True)
    matched = _capability_matched(row, profile)
    if raw >= cfg.capability_strong_min:
        return _mk(
            "capability",
            "capability_strong",
            "inferred",
            cfg,
            evidence=f"Capability component scored {raw:.0f}/100 - {matched}.",
            profile_driven=True,
            matched=matched,
        )
    if raw >= cfg.capability_partial_min:
        return _mk(
            "capability",
            "capability_partial",
            "inferred",
            cfg,
            evidence=f"Capability component scored {raw:.0f}/100 - {matched}.",
            profile_driven=True,
            matched=matched,
        )
    return None  # below partial threshold — no positive capability chip


def _h_value(
    row: Mapping[str, object], comps: Mapping[str, float], profile: Mapping[str, object], cfg: ReasonConfig
) -> ReasonChip | None:
    raw = comps.get("estimated_value")
    if raw is None:
        return None
    ceiling = profile.get("max_comfortable_contract_value")
    if not ceiling:  # falsy ceiling -> the scorer returns the neutral 50 -> refuse, do not fabricate a range
        return _mk("value", "value_missing", "missing", cfg, evidence=NOT_REPORTED, profile_driven=True)
    val = _usd(_to_float(row.get("total_obligated_amount")))  # value evidence cites OBLIGATED, never base/ceiling
    ceil = _usd(_to_float(ceiling))
    if _approx(raw, 100.0):
        return _mk(
            "value",
            "value_in_range",
            "inferred",
            cfg,
            evidence=f"Obligated {val} is within your {ceil} comfortable ceiling.",
            profile_driven=True,
            value=val,
            ceiling=ceil,
        )
    if _approx(raw, 60.0) or _approx(raw, 25.0):
        return _mk(
            "value",
            "value_over",
            "inferred",
            cfg,
            evidence=f"Obligated {val} exceeds your {ceil} comfortable ceiling.",
            profile_driven=True,
            value=val,
            ceiling=ceil,
        )
    return None  # score ~= 30 (well under range) or ~= 0 (no value) -> no chip


def _h_agency(
    row: Mapping[str, object], comps: Mapping[str, float], profile: Mapping[str, object], cfg: ReasonConfig
) -> ReasonChip | None:
    raw = comps.get("agency_fit")
    if raw is None:
        return None
    if not profile.get("agencies_with_past_performance"):
        return _mk("agency", "agency_missing", "missing", cfg, evidence=NOT_REPORTED, profile_driven=True)
    subagency = _norm_str(row.get("subagency"))
    if _approx(raw, 100.0):  # a match -> subagency is non-blank by construction
        return _mk(
            "agency",
            "agency_pastperf",
            "observed",
            cfg,
            evidence=f"{subagency} is in your declared past-performance list.",
            profile_driven=True,
            subagency=subagency,
        )
    # baseline (score ~= 50, the DoD-wide neutral) — kept INFERRED so the imputation stays visible.
    if subagency == "":  # blank-safe: no empty {subagency} placeholder (Corrections v2 C2.3)
        return _mk(
            "agency",
            "agency_baseline_blank",
            "inferred",
            cfg,
            evidence="DoD component not reported on this record; scored the DoD-wide baseline.",
            profile_driven=True,
        )
    return _mk(
        "agency",
        "agency_baseline",
        "inferred",
        cfg,
        evidence=f"No past performance at {subagency}; scored the DoD-wide baseline.",
        profile_driven=True,
        subagency=subagency,
    )


def _h_location(
    row: Mapping[str, object], comps: Mapping[str, float], profile: Mapping[str, object], cfg: ReasonConfig
) -> ReasonChip | None:
    raw = comps.get("location_fit")
    if raw is None:
        return None
    if profile.get("nationwide"):
        return _mk(
            "location",
            "location_nationwide",
            "inferred",
            cfg,
            evidence="Your profile serves nationwide, so every place of performance fits.",
            profile_driven=True,
        )
    if not profile.get("states_served"):
        return _mk("location", "location_missing", "missing", cfg, evidence=NOT_REPORTED, profile_driven=True)
    state = _norm_str(row.get("place_of_performance_state"))
    if state == "":
        return _mk("location", "location_state_missing", "missing", cfg, evidence=NOT_REPORTED, profile_driven=True)
    if _approx(raw, 100.0):
        return _mk(
            "location",
            "location_in_area",
            "observed",
            cfg,
            evidence=f"Performed in {state}, one of your served states.",
            profile_driven=True,
            state=state,
        )
    return _mk(
        "location",
        "location_out_area",
        "observed",
        cfg,
        evidence=f"Performed in {state}, outside your served states.",
        profile_driven=True,
        state=state,
    )


def _empty_state(cfg: ReasonConfig) -> ReasonChip:
    return _mk("empty_state", "empty_state", "missing", cfg, evidence=NOT_REPORTED)


def reason_codes(
    candidate_row: Mapping[str, object],
    component_scores: Mapping[str, float] | None,  # from rescore.score_components(row, profile); None => Explorer path
    profile: Mapping[str, object],
    cfg: ReasonConfig,
) -> list[ReasonChip]:
    """Ordered, de-duped chip list for one candidate. ALWAYS non-empty (a single empty-state chip when
    nothing else fires). Never mutates any input. Deterministic: sort key ``(priority, BASIS_RANK, code)``
    — no float tie, no clock, no RNG, no input-dict-order dependence. When ``component_scores is None``
    (Explorer), the four profile-driven handlers are skipped, so the result is profile-independent."""
    row = candidate_row
    chips: list[ReasonChip] = []

    def add(chip: ReasonChip | None) -> None:
        if chip is not None:
            chips.append(chip)

    add(_h_data_gap_title(row, cfg))
    add(_h_data_gap_end_date(row, cfg))
    add(_h_data_gap_stale(row, cfg))
    add(_h_incumbent_lock(row, cfg))
    add(_h_set_aside(row, cfg))
    add(_h_urgency(row, cfg))
    add(_h_expired_grace(row, cfg))
    add(_h_recompete(row, cfg))
    add(_h_ptw(row, cfg))
    add(_h_displacement(row, cfg))
    add(_h_idv_task_order(row, cfg))
    add(_h_data_quality(row, cfg))
    add(_h_data_gap_code_prefix(row, cfg))
    add(_h_data_gap_short_title(row, cfg))

    if component_scores is not None:
        add(_h_capability(row, component_scores, profile, cfg))
        add(_h_value(row, component_scores, profile, cfg))
        add(_h_agency(row, component_scores, profile, cfg))
        add(_h_location(row, component_scores, profile, cfg))

    # dedup: an explicit Data-Gap end-date / stale chip supersedes the urgency chip (same fact, sharper).
    codes = {c.code for c in chips}
    if "data_gap_end_date" in codes or "data_gap_stale" in codes:
        chips = [c for c in chips if c.code != "urgency"]

    chips.sort(key=lambda c: (c.priority, BASIS_RANK[c.basis], c.code))
    chips = chips[: cfg.max_chips_detail]
    if not chips:
        chips = [_empty_state(cfg)]
    return chips


def summary_chips(chips: Sequence[ReasonChip]) -> list[ReasonChip]:
    """The executive chip row: context (tier-6) chips are dropped; they live in the evidence expander only."""
    return [c for c in chips if not c.context]


def top_chips(chips: Sequence[ReasonChip], n: int) -> list[ReasonChip]:
    return list(chips[:n])
