# GovConRadar — Roadmap & "Deliberately Not Built"

> A product roadmap in two halves: what ships next, and — just as deliberately — what does
> not ship and why. The second half is a feature, not an apology: showing the full design
> space *and* the disciplined subset chosen is what separates a trustworthy analytical tool
> from a feature checklist. Read alongside `docs/methodology_notes.md` ("How we compare").

## The reconciliation clause (the gate)

This clause governs every "future ML/prediction" idea in this document and in the public
`FUTURE IMPROVEMENTS.md`, so the two documents can never be quoted against each other:

> **A backtested recompete-outcome model ships only behind a pinned gate — no published
> probability before (a) a labeled-record threshold and (b) a published out-of-sample
> backtest with its n and interval. Until then the honest output is deterministic signals +
> coverage-gated Unknown.**

The working model for this discipline already ships: `src/scoring/ptw_backtest.py` is the
Competitive Price Range's outcome-coverage harness, built and tested but **dormant** — not
wired into the pipeline, the app, or Power BI — because on the current pool its
predecessor→successor proxy pairs are dominated by same-IDV task-order sequences rather
than true cross-vehicle recompetes. It activates when the data can support it, not when a
roadmap wants it to.

**Status (2026-07): the labeled-record path now has rails.** `config/measurement.yaml`
pins the thresholds; `data/labels/` + `docs/labeling_protocol.md` define the awardee-blind
samples; `trust_metrics_report` publishes each metric only past its gate, with n and a
Wilson interval. Still true, unchanged: no probabilities, no weight fitting, no
"calibrated" claims — the gate opens on labels, not on wanting it to.

## Shipped (the keepers)

| Signal | Status | Honesty handling |
|---|---|---|
| Reason-codes / explainability layer | ✅ 2.2.0 | Evidence + named basis per chip |
| Burn-pressure (obligation pace vs PoP clock) | ✅ 2.2.0 | Order value never the parent-IDV ceiling; coverage-gated |
| Office / buyer-behavior fingerprints (descriptive) | ✅ 2.2.0 | 100% descriptive aggregates, no prediction |
| Termination / ghost-fix + mod-velocity + ceiling-balloon + bridge detection | ✅ this release | FPDS codes only (E/F/X/N; K guarded); complete-vs-partial *inferred, conservative*; 90-day FPDS lag disclosed on every surface |
| Recently-lapsed / bridge-watch lens (successor-visible proxy) | ✅ this release | A label, never a filter; same-parent-IDV excluded; "no successor visible" never rendered as "missed recompete" |
| Incumbent size-determination shift (structural eligibility) | ✅ this release | Per-procurement S→O code shift; directional flag with named basis, never a verdict |
| Digest delivery (email/webhook) + CRM lead export | ✅ this release | Loud failures; lag disclosure in every body/row |
| Sources Sought / RFI early-warning lane | ✅ this release (staged) | Currency depends on the SAM bulk CSV refresh; never fabricates a notice |

## Next (standing priorities)

1. **Agency acquisition-forecast integration** — the standing top roadmap item; a larger
   standalone build (new ingest + entity resolution + honest-linkage design), sequenced
   after the termination/mods correctness work that this release delivered.
2. **Acquisition-via-parent detection** — the parent-UEI columns exist in the intake
   (`recipient_parent_uei`/`recipient_parent_name`), but an honest acquisition signal needs
   temporal parent-change joins, a false-positive study, and coverage gates of its own.
   Scope decision, not a data gap.
3. **Backtest activation** — per the gate clause above, when multi-fiscal-year true
   cross-IDV recompete pairs dominate the proxy.
4. **Award-level delete-marker policy** (adversarial review, 2026-07-13): the loader's
   award dedupe still lets a retracted (`correction_delete_ind = "D"`) transaction win the
   award record while the digest fold skips it — a fully-retracted award would become a
   digest-less candidate and fail the validator loudly. Zero occurrences on current data
   (the delta column is 100% blank); needs its own reviewed change to the frozen dedupe.
5. **RFI coverage beyond the notice_type enum** (adversarial review, 2026-07-13): SAM's
   current exports carry no explicit RFI notice_type — RFIs ride under Sources Sought or
   Special Notice. Honest detection inside Special Notice needs title/keyword work with a
   false-positive study; the lane's caption discloses the gap meanwhile.

## Deliberately Not Built — and why

| Not built | The honest reason |
|---|---|
| **ML timing stack** (gradient-boosted P(recompete in 6/12/18/24 mo), survival models) | No ground-truth labels exist. Recompete lineage is **inferred**, so training on it is **circular** (using vendor identity to link, then measuring retention = label leakage). |
| **Calibrated probabilities / specific RFI→RFP→award dates** | Honest only with a real outcome backtest (see the gate clause). A confident-but-wrong "82%" directly contradicts the one thing that makes this product credible; refusing to emit it *is* the differentiator. |
| **LinkedIn / job-posting signals** | ToS-hostile scraping, noisy, and drags PII into a clean-public-data product. |
| **Vehicle-migration *predictor*** | Speculative on thin data. The *observed* half (which vehicles an office historically uses) ships in the fingerprints. |
| **Full contract-family graph DB** | The load-bearing 5% is a pairwise predecessor→successor candidate table — a join, not a graph database. |
| **Requirement-drift embeddings** | The PWS/SOW does not exist for a pre-solicitation recompete; there is nothing honest to embed. |
| **J&A full-text parsing, protest NLP, CPARS-style performance proxies** | Above the public-data ceiling: J&A text is inconsistently posted, CPARS is never public. A lead, never a verdict. |

## The one rule that governs all of it

Every "prediction" becomes an **observed signal + named basis + coverage gate**:
"shows 3 of 5 recompete signals — here's the evidence for each" beats "82% probability"
in front of anyone who does this work for a living, because they can check the first
claim and they know nobody can check the second.
