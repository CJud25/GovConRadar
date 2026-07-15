"""
reason_codes (app adapter) — Streamlit-free glue between the live scorer and the strict engine.

This is the ONLY module that imports `rescore` — the parity firewall. The strict engine
(scoring.reason_codes) never imports the scorer tree; this adapter owns the `rescore` edge, turning
a candidate row + profile into machine-keyed component scores and handing them to the engine. It is
app glue (import-order lint applies, but it is NOT in the mypy `files` list / FMT_PATHS).
"""

from __future__ import annotations

from typing import Mapping

from components import rescore
from scoring import reason_codes as engine
from utils.config import REASON_CODES

_CFG = engine.load_reason_config(REASON_CODES)  # built once at import (the range/ordering/completeness validator)


def _component_scores(row: Mapping[str, object], profile: Mapping[str, object]) -> dict[str, float]:
    """Machine-keyed component dict for one row under `profile`. rescore.score_components is the ONE
    public path (verified present at rescore.py:97) — called UNCONDITIONALLY; there is no breakdown_rows
    fallback and no label->key map (that would reintroduce the label-drift surface the design rejected).
    Keys == engine.COMPONENT_KEYS (pinned by test_component_keys_match_rescore)."""
    return {str(k): float(v) for k, v in rescore.score_components(row, profile).items()}


def detail_chips(row: Mapping[str, object], profile: Mapping[str, object]) -> list[engine.ReasonChip]:
    """Full ordered chip list for one candidate under the ACTIVE profile (profile-driven + independent).
    Callers render engine.summary_chips(...) in the chip row and the full list in the expander."""
    return engine.reason_codes(row, _component_scores(row, profile), profile, _CFG)


def explorer_chips(row: Mapping[str, object]) -> list[engine.ReasonChip]:
    """Profile-INDEPENDENT, executive-only chips (component_scores=None, context dropped) — cheap, stable
    across ?p= (no per-row rescore call)."""
    chips = engine.summary_chips(engine.reason_codes(row, None, {}, _CFG))
    return engine.top_chips(chips, _CFG.max_chips_explorer)
