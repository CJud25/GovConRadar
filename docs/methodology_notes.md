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

## How we compare (design principles, not a scorecard)

Recompete-intelligence tools in this market tend to share three habits. GovConRadar
was designed around their opposites — stated here as our own principles, about
patterns rather than products:

1. **Decomposed, evidence-linked components — not a black-box number.** A score you
   cannot interrogate is a score you cannot defend in a bid/no-bid meeting. Every
   pursuit score here decomposes into its eight components in
   `fact_scoring_breakdown` (long format, one row per component), every chip in the
   app names its basis (observed / inferred / missing), and every row links to its
   public source record. If a number cannot show its work, it does not ship.

2. **A comparables floor that refuses — not a guess.** The Competitive Price Range
   engine declines to estimate (`ptw_basis = "insufficient"`) below a minimum
   comparables count rather than extrapolating from too little data, and the mod
   signals gate the same way (`ceiling_basis`/`bridge_basis`/`mods_basis` =
   `"insufficient"`). A refusal is information; a fabricated midpoint is a liability
   wearing a decimal place.

3. **Unknown as a first-class answer — not a default filled in.** Missing components
   are never imputed to a neutral middle: weights renormalize over what is actually
   present, stale or garbled records quarantine to a visible **Data Gap** tier
   instead of blending into headlines, and the validator enforces the equivalences
   (an "Unknown" can never secretly carry a number, and a number can never secretly
   be an Unknown). A reader who learns they can believe our "Unknown" can believe
   our numbers.

These are testable claims, not marketing: `scripts/validate_data.py` enforces all
three families of invariants on every published bundle, in CI.

## Known limitations

See `data_quality_report.csv`'s `source_coverage_notes` rows for the full,
current list, including the USAspending last-modified-date extraction gap.

SAM.gov opportunity data is present in this build (~1,816 notices in
`fact_opportunity_notices.csv`, loaded via the no-key SAM bulk CSV path), fuzzy-
matched to awards in `bridge_award_opportunity_links.csv`. **Link coverage is low
by design of the data, not a defect:** only ~4% of candidates currently match a
live notice on the 2026-07-15 snapshot (measured: 1,311 of 35,964; the app's
Methodology page recomputes the live figure, and the count reflects the
linker's recency + origin gates), because a recompete's
solicitation is typically posted only months before award — so contracts expiring
6–24 months out usually have no notice yet, and PIID/title drift between an award
and its future solicitation further limits matches. A linked notice is a strong
early signal; its absence is **no signal**, never evidence a recompete isn't coming.

## Lead time — operational definition (measured-trust rails)

`lead_time_days = earliest posted_date across the candidate's High/Medium linked notices −
first_flag_date`, where `first_flag_date` = earliest comparable snapshot (≥ the shared
candidate-id migration date `comparable_since` in `config/measurement.yaml`) containing the
candidate_id. Degenerate cases, all pinned in `scoring.trust_metrics.lead_time_rows`:
(1) flagged-after-notice (negative) kept in the median and reported as
`lead_time_flagged_after_rate`, never clipped — unless the notice precedes the data window,
which is case (2); (2) notice precedes the data window → excluded from the median, counted as
`lead_time_window_precedes_n`; (3) multiple notices → anchor on the earliest parseable
`posted_date` (the most conservative credit); (4) left-censoring (`first_flag_date` == the
earliest snapshot) → included, with the median labeled a **conservative lower bound** and
`lead_time_censored_share` disclosed; (5) missing/unparseable `posted_date` → excluded, counted.
Every rendered surface carries the selection-bias sentence: only the ~4% linked subset can
carry a lead time, it skews late-stage, and DoD FPDS reporting lags ~90 days.

## Naive-vs-radar baseline (internal note)

`baseline_*` rows compare two deterministic top-50 lists: **naive_50** = active candidates
sorted by `days_until_expiration` ascending, ties broken by `potential_value` descending then
`candidate_id` (the naive analyst does NOT quality-filter — Data-Gap actives stay in); and
**radar_50** = active non-Data-Gap candidates sorted by `pursuit_score` descending, ties broken
by `candidate_id`. Reported: top-50 overlap share, the share of each list carrying any
data-quality flag, and the share of each list holding a High/Medium notice link.
This comparison uses no outcome labels; it states what the tiers *add* — quality filtering and
evidence — and makes no calibration or accuracy claim. Values live in `trust_metrics_report`
(`surface = "internal"`), are regenerated on every rebake, and are deliberately not copied into
this file.

## Outcome-label taxonomy (`labels.taxonomy.OUTCOME_LABELS` / `OUTCOME_DISPLAY`)

One vocabulary, shared by prose and code — the keys below are `OUTCOME_LABELS` and the
phrases are `OUTCOME_DISPLAY`, sourced by name from `src/labels/taxonomy.py` (change them
there; this table describes, it never redefines).

| Key | Phrase | Evidence rule |
|---|---|---|
| `recompete_unchanged` | Recompeted — substantially the same work | Anchoring notice preferred (`notice_anchored=N` caps confidence at medium) |
| `extension_bridge` | Extended / bridged — follow-on still pending | Extension/bridge action visible; follow-on not yet awarded |
| `vehicle_migration` | Moved onto a contract vehicle | Successor work visible on an IDV/GWAC/BPA |
| `consolidation_or_split` | Consolidated or split (positive evidence) | **Positive evidence required** — never inferred from absence |
| `ended` | Ended — positive evidence of no follow-on | **Positive evidence required** — an invisible successor is `undeterminable(no_notice_found)`, never `ended` |
| `sole_source_follow_on` | Sole-source follow-on | J&A / sole-source award visible |
| `undeterminable` | Undeterminable from public data | Requires an `undeterminable_reason`; excluded from precision's numerator AND denominator, disclosed as a rate |
| `successor_out_of_scope` | Successor outside this dataset's scope (e.g. assisted acquisition) | Continuation visible but outside the awarding-097 slice; counts as continuation |

`CONTINUATION_OUTCOMES` (the precision@50 positive class) is every key except `ended` and
`undeterminable` — defined in the taxonomy, consumed by `trust_metrics.precision_at_50_rows`.
