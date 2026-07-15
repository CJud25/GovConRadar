# GovCon Recompete Radar — Methodology Notes

## What is fact vs. estimate

**Fact** (traced to a real USAspending.gov API response, source metadata preserved):
PIID, awardee name/UEI, awarding/funding agency and DoD component (sub-agency),
obligated amount, base-and-all-options ceiling, period of performance dates,
NAICS, PSC, contract pricing type, set-aside type, extent competed, number of
offers received.

**Estimate** (analytical, never an official government prediction):
cyber/IT classification confidence, recompete candidacy, estimated recompete
timing windows, pursuit score and priority tier, the **Competitive Price Range**
(a range of comparable historical *winning* award run-rates — not a bid
prediction), incumbent analysis, opportunity/notice link confidence.

## Cyber/IT classification

Rules-based, using three independent signals: NAICS relevance (from
`dim_naics.csv`), PSC relevance (from `dim_psc.csv`), and keyword match
against the award description (keyword taxonomy in `config/keywords.yaml`,
exposed via `src/utils/config.py`). High confidence requires all three signals
together — a single NAICS code or keyword alone never produces more than Low
confidence. See `src/transform/classification.py`.

## Recompete candidacy

A classified award becomes a recompete candidate if it clears the configured
minimum value ($250,000 default) — this is the only hard filter. An award
with no known end date at all still becomes a candidate: `expiration_date_basis`
is set to `"unknown"`, `days_until_expiration` is left null, and — in the
published (rebaked) data — `expiration_bucket` reads `"Expired — verify"` (the
quarantine bucket) with the missing-end-date flag forcing the Data Gap tier,
rather than excluding the row. Where a
usable date does exist, the "selected expiration date" prefers
`potential_end_date` over `current_end_date` when both are available, since
potential end date reflects the full base-plus-options horizon.

## Estimated recompete windows

High-value contracts (≥ $10M) widen the estimated window to 18–6 months
before expiration; smaller contracts narrow it to 9–2 months before
expiration. This mirrors typical federal capture timelines but is always
labeled as an estimate — see `scoring_methodology.md`.

## Known limitations

See `data_quality_report.csv`'s `source_coverage_notes` rows for the full,
current list, including the USAspending last-modified-date extraction gap.

SAM.gov opportunity data is present in this build (~1,816 notices in
`fact_opportunity_notices.csv`, loaded via the no-key SAM bulk CSV path), fuzzy-
matched to awards in `bridge_award_opportunity_links.csv`. **Link coverage is low
by design of the data, not a defect:** only ~12% of candidates currently match a
live notice on the 2026-07-15 snapshot (measured: 4,163 of 35,964; the app's
Methodology page recomputes the live figure), because a recompete's
solicitation is typically posted only months before award — so contracts expiring
6–24 months out usually have no notice yet, and PIID/title drift between an award
and its future solicitation further limits matches. A linked notice is a strong
early signal; its absence is **no signal**, never evidence a recompete isn't coming.
