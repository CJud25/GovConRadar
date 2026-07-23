# Changelog

> Note: this changelog records the private build repo. The public deploy ships the app, its
> `src/`+`config/` library, a committed `data/sample/`, and the script-based gate
> (`validate_data.py`, `check_doc_counts.py`, `smoke_app.py`). The pytest suites and
> `pyproject.toml`/ruff config referenced in older entries live in the build repo, not this
> public deploy.

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project versions the **scorer**
(`SCORER_VERSION`) alongside the app.

## [2.8.0] — 2026-07-16 — Forward surfacing: rank on the signals, eligibility beside the score, office concentration in the brief

`pursuit_score`, `config/scoring_weights.yaml`, and `priority_tier` are **byte-identical** across
this release — every feature below is a separate surface/lens beside the score, never a blend
into it (honesty rule #9; the validator's scorer-parity firewall stays green).

### Added
- **Displacement sort lens (F2)** — the Explorer table and the Contract Detail "top 5 fits"
  launcher can now be ordered by the baked F1 incumbent-displacement lane ("Displacement
  signals, then score": observed signal count desc, pursuit score as tie-break; lane-unreadable
  rows sort LAST, never imputed to zero). A bridge-flagged, sole-offer, expiring incumbent can
  finally outrank a mock-profile capability match — as an ORDERING choice, not a score change.
  Single-sourced in `components.data` (`displacement_sort` / `displacement_sort_ready` +
  the two option labels, the bridge-watch pattern) so the two surfaces can never drift;
  column-guarded so a pre-lane bundle never offers the lens. The Explorer grid also gains a
  compact "Displacement" (k of n) indicator column beside the score.
- **Eligibility beside the score (F3)** — the pursuit score's `set_aside_fit` component rewards
  restricted work regardless of whether the firm can actually prime it; the real gate/warn/
  unknown/clear verdict (`scoring.eligibility_lane`) now travels WITH the score everywhere it is
  ranked: a fixed-vocabulary **"Prime path"** column on the Explorer grid immediately beside
  Score (historical-path read, the `lane_counts` rule un-aggregated via the new adapter
  `lane_states`; demo profile honestly reads Unknown/Clear only — no attestation exists), and a
  Data-Gap-style ⛔ warning callout on Contract Detail whenever the lane hard-gates (live-notice
  path), so a strong score can never be read without the "can't prime it" verdict. A gate, not a
  score — the number never moves.
- **Per-candidate market-context join (F4)** — `scoring.market_concentration` computed
  `top_share` / `n_ueis` per DoD component but the read was stranded at market grain (one chart
  on the Incumbent Landscape). New `annotate_agency_concentration` bakes the SAME read onto
  `dim_agency` as four `concentration_*` columns (over the reportable, Data-Gap-excluded pool;
  both honesty gates ride through; a component with no reportable rows bakes `insufficient` with
  a named reason; unforgeable Unknown: observed ⇔ top_share present ⇔ reason empty, with
  `concentration_n_ueis` always published as a coverage fact). Wired into the pipeline (step 8),
  the rebake (`rebuild_dim_agency`), `EVIDENCE_CONTRACT["dim_agency"]`, and the capture brief's
  Office section — which now shows the buying office's concentration ("top incumbent holds X% …
  across N incumbents — a dollar-share, not market power") or the named refusal. Validator
  invariant 12b pins vocabulary, equivalences, and baked == fresh-recompute parity; both data
  dictionaries updated; sample bundles rebaked.

### Fixed
- **The baked bridge now regenerates through the current linker gates** — 2.7.1 fixed the
  linker CODE but the shipped `bridge_award_opportunity_links` artifacts were never re-baked:
  the full snapshot still carried 2,951 of 4,163 established links (71%) posted outside the
  recency window, including High links pairing a 2018 notice with 2026 expiries — the exact
  degenerate shape the gate was built to kill, still rendering in the app and the briefs.
  `scripts/rebake_data.py::rebake_dir` now rebuilds the bridge from the bundle's own
  candidates + `fact_opportunity_notices` through `transform.opportunity_linking
  .build_bridge_table` (build_sample ships that regenerated bridge too), and NEW validator
  **invariant 14** pins the artifact: no established link whose notice `posted_date` and
  candidate end-anchor both parse may sit outside the recency window around every known
  anchor. Established links after regeneration: data/powerbi 1,311 (was 4,163),
  data/sample 10 (was 23); `docs/methodology_notes.md` coverage figures updated (~4% linked).
- **Displacement lane: stale rows are quarantined out** (`scoring.incumbent_displacement`) —
  a Data-Gap stale row (`candidate_status == "expired_stale"`) read its months-to-years-old
  facts as observed "quiet" signals (`lapsed_no_successor` counted a 3-years-stale record as
  an observed non-lapse) and could surface an observed "k of n" band, sort ahead in the
  displacement lens, and earn the inferred chip beside its Data Gap tier. A stale row now
  reads EVERY signal unknown, coverage-gating the whole lane to `insufficient` — Unknown
  stays unforgeable, and the lane can never contradict the quarantine. All bundles rebaked;
  `pursuit_score` / `priority_tier` remain byte-identical (validator invariant 1).
- **Linker origin gate + either-anchor recency window** (`transform.opportunity_linking`) — two
  adversarial-review catches on the 2.7.1 recency gate: (1) an award's own ORIGIN solicitation
  (an exact-id match posted before `pop_start_date`) could still link High when a short period
  of performance put it inside the expiry window — an establishing match posted before the
  award's own start is now rejected (an origin notice is never a successor); (2) the gate
  anchored ONLY on the policy-selected expiration, so a legitimate early recompete posted near
  `current_end_date` after options went unexercised — exactly a displacement event — was wrongly
  gated out. The window now accepts a notice inside the recency window of EITHER the selected
  expiry OR the current period end. Missing/unparseable dates still never gate. Redacted
  public-artifact titles (`CONTACT_TITLE_PLACEHOLDER`) now score as EMPTY in the fuzzy matcher,
  so two redacted titles can never fabricate a 100-point "strong title" link.
- **Brief Office-section concentration wording** — the observed line claimed the top incumbent's
  share "of the component's reportable expiring obligated dollars", but
  `market_concentration.top_share` divides by the ATTRIBUTED (UEI-known) slice of the reportable
  pool (up to `max_unknown_uei_share` of dollars may carry no incumbent UEI). It now reads
  "attributed (UEI-known) expiring obligated dollars"; the same correction lands in both data
  dictionaries. No numeric change.

## [2.7.1] — 2026-07-16 — Link-track integrity: recency gate, origin-notice rule, bulk-fill guard

### Fixed
- **Linker recency gate** (`transform.opportunity_linking`) — the 2026-07 audit found a single
  2018 "IT SUPPORT SERVICES" notice matched as the "recompete" of 70 of 120 sampled 2025-ending
  awards, purely on title similarity. An establishing match (id / strong title / corroborated
  loose title) is now REJECTED when both dates are known and the notice's `posted_date` falls
  outside `[expiration − 24 months, expiration + 12 months]` around the candidate's own end —
  a recompete solicitation appears near/after the incumbent's expiry, never years before it.
  Window bounds are asserted priors in **`config/opportunity_linking.yaml`** (validated in
  `utils.config`); a missing/unparseable date on either side never gates (it cannot prove a
  violation). Tiers/reasons otherwise unchanged; a candidate whose only matches were
  recency-rejected gets an honest distinct No-Match reason. `posted_date` now rides
  `OPPORTUNITY_CLEAN_COLUMNS` (bulk `posted_date` / live-API `postedDate` both mapped).

### Added
- **Labeling protocol §1: origin paperwork is never a successor** — an award's *own*
  pre-solicitation, solicitation, award synopsis, or notice-of-intent-to-sole-source is not
  successor activity and must be labeled `incorrect` (`docs/labeling_protocol.md`).
- **Bulk-fill publication guard (G4)** — `trust_metrics.link_precision_rows` now REFUSES to
  publish a link-precision tier whose filled verdicts are 100% a single `labeled_date` with
  zero `labeler_notes` and zero `unsure` (the signature of a mechanical tier→label fill, not
  per-case adjudication): new `gate_state="suspect_bulk_fill"`, value withheld, honest note.
  Explicit `link_labels.allow_bulk_fill: false` prior in `config/measurement.yaml` — flipping
  it (a real bool, never coerced) is the only override, for a deliberate documented
  single-sitting session.

## [2.7.0] — 2026-07-16 — Incumbent Displacement lane: the forward signals finally drive a surface

### Added
- **`src/scoring/incumbent_displacement.py`** — a categorical decision-table lane
  ("Displacement signals: k of n observed") over six already-baked per-candidate signals:
  bridge extension, termination on record, large deobligation, lapsed-with-no-visible-successor
  (`expired_grace` + `successor_visible_basis == none_visible`), sole offer at the last
  competition (competed extent only, mirroring the incumbent-lock chip's exact semantics), and
  the incumbent's size-standard shift (joined from `dim_vendor` by UEI — gated Unknown when the
  vendor read is not cleanly available). Every signal resolves fired / quiet / unknown; fewer
  than `min_signals_read` readable inputs coverage-gates the whole lane to `insufficient`
  (Unknown unforgeable: `observed <=> count present <=> signals present <=> real band`).
  Six new baked columns (`displacement_*`) on `fact_recompete_candidates`; priors in
  `config/incumbent_displacement.yaml` (offers junk-count guards threaded verbatim from
  `config/reason_codes.yaml` — one home, no drift).
- **`displacement` reason-codes signal** (19 signals, 38 templates): an inferred (ESTIMATE)
  chip when >=1 signal fired, a digit-free missing chip when the lane is insufficient, no chip
  when observed-quiet or on a pre-lane bundle (presence-gated).
- **Contract Detail panel** "Incumbent displacement signals" (column-guarded, chips + unread
  note); Incumbents view carries a deliberate TODO for the vendor-grain rollup.
- **Validator invariant 10c** — displacement vocabulary, unforgeable-Unknown equivalences,
  `count <= read <= 6`, and baked == fresh recompute over the bundle's own `dim_vendor`.
- Lane columns wired into the rebake path, `EVIDENCE_CONTRACT`, both data dictionaries, and
  the capture briefs (via the chip row).

### Guaranteed unchanged
- **`pursuit_score`, `config/scoring_weights.yaml`, and `priority_tier` are byte-identical** —
  the lane is a separate surfaced label, never blended into any score; the scorer-parity
  invariant (validator #1) and a lane-level firewall test pin it.

## [2.6.0] — 2026-07-16 — Outcome-label vehicle grain: the honest recompete unit

### Changed
- **The outcome-label cohort is redrawn from order grain to vehicle grain**
  (`outcome_labels.grain: vehicle`, the new default; `order` keeps the legacy per-order draw).
  SINGLE-award task-order sequences roll up to ONE candidate per parent vehicle — under a
  single-award IDV the next order to the same holder is a continuation, not a recompete — with
  the recompete clock = the vehicle's ordering-period end; vehicles still open past the FY
  window are gated out. MULTIPLE-award vehicle orders (GWAC / GSA schedule / BPA
  fair-opportunity buys) stay at order grain: their follow-ons are genuine competed recompetes.
  Standalone contracts are unchanged (clock = their own potential end). The disclosed top-50 is
  now the top vehicles/contracts by their best cohort order's shipped `pursuit_score`.
- **`data/labels/outcome_labels.csv` migrated to the vehicle-grain draw**; the order-grain
  worksheet is archived under `data/labels/archive/`.

### Added
- **`data/reference/idv_attributes.csv`** — committed vehicle-attribute lookup (single-vs-
  multiple award, ordering-period end, IDV type, competition, set-aside, total obligation) the
  rollup joins against, refreshed offline by the new **`scripts/pull_idv_attributes.py`**
  (USAspending award-detail API; the pipeline reads only the committed CSV, so a cold clone
  stays reproducible with no network).
- **`outcome_labels.grain`** in `config/measurement.yaml`: `vehicle` (default) | `order`.

## [2.5.0] — 2026-07-15 — Outcome-label rails: gated precision@50 + rank stability

### Added
- **Gated precision@50** — the disclosed top-50 outcome sample (shipped demo-profile
  ranking, and the Methodology page says so) publishes a precision number only past
  ≥40 determinable hand labels, with its n and a 95% Wilson interval. Until then the row
  reads "not yet measured" with the current count. Undeterminable and out-of-scope rates
  plus a labeled-progress counter publish immediately — refusals are always publishable.
  Positive evidence rules: `ended` is never inferred from an invisible successor.
- **Rank-stability accumulator** — median top-50 overlap and tier-migration share across
  adjacent comparable snapshots, gated on 3+ comparable snapshots. Publishing now
  (3 comparable snapshots exist): top-50 overlap median 0.99, tier migration 0.24% —
  describes ranking churn between snapshots, never accuracy.
- Methodology's Measured-trust section explains the outcome-labeling program (stratified
  FY2023–FY2024 sample + disclosed top-50, awardee-blind) while the gate is closed; every
  count in the copy is computed from the metric rows, never hardcoded.

## [2.4.0] — 2026-07-15 — Eligibility lane, deterministic capture brief, freshness surfaces

### Added
- **Attested certifications on the company profile** — a self-attested cert multiselect
  (8(a), HUBZone, SDVOSB, VOSB, WOSB, EDWOSB), the 8(a) program exit date, and a
  small-business self-certification, all round-tripping through the shareable `?p=` link.
  Self-attested and used only to check set-aside eligibility — never verified.
- **The eligibility lane** — a categorical gate / warn / unknown / clear verdict rendered
  ABOVE the pursuit score on Contract Detail, plus a prime-path tally strip on Your Company.
  The hard gate fires only on a live, High-confidence linked notice carrying a set-aside
  code; a historical FPDS code only ever cautions (the contracting officer decides the
  recompete's strategy fresh). Blank is "not reported", never "unrestricted"; every failing
  verdict carries the teaming reframe. A label — it never moves the score.
- **Evidence-contract capture brief** — the Detail download is now an 8-section,
  deterministic, source-linked brief (WHY NOW / THE SIGNALS / WHO HOLDS IT / THE OFFICE /
  PRICE RANGE / ELIGIBILITY / WHAT WE CAN'T KNOW / SOURCES) rendered from an enumerated
  evidence contract: columns outside the contract are unreachable by construction, refusing
  bases render refusals (never numbers), and every value is escaped. System font stack,
  self-contained CSS, print-ready.
- **Freshness surfaces** — the header band now reads `as of {date} · {N}d old` in both live
  and sample modes.

### Removed
- The 2026-07-03 in-app capture brief (three inlined template functions, including a
  Google-Fonts `@import` inside a supposedly deterministic artifact). One renderer remains.

### Changed
- Lapsed/bridge-watch copy is single-sourced from the outcome-label taxonomy (strings
  byte-identical; drift is now structurally impossible).

## [2.3.1] — 2026-07-15 — Clean-history republish, provenance manifest, PSC catalog

### Security
- **Public repository history recreated as one clean commit.** Pre-redaction data
  (contracting-officer personnel titles) was still reachable by raw SHA on dangling commits
  even after the releases and tags referencing them were deleted — repository delete+recreate
  is GitHub's only true purge. The full development history lives in this private repo.

### Added
- **Release provenance manifest** — the data-snapshot zip carries `manifest.json` (SHA-256 +
  row count per table + source-freshness block); `scripts/download_data.py` verifies every
  table against it on extraction and refuses an asset without one.
- **PSC descriptions backfilled from the GSA PSC manual** (two committed vintages under
  `data/reference/psc_manual/`): `dim_psc` unknown descriptions 1,306 → 0; classification and
  tiers byte-identical (tripwire-verified).
- Public CI: import-walk over every shipped `src/**/*.py`, advisory pip-audit, blocking
  gitleaks, and `scripts/check_doc_counts.py` (pinned prose numbers vs generated values).

### Changed
- **Expiring aggregates and the pipeline-value KPI are forward-only** (first live in the
  2026-07-14 data refresh). `dim_vendor.expiring_value_*` and the `dim_agency` expiring
  counts/`expiring_pipeline_value` no longer count already-expired contracts as "expiring"
  (a 3.0x overstatement on the full snapshot, where 40.6% of known-dated candidate value is
  already expired). `dashboard_kpi_summary.total_estimated_pipeline_value` now sums the forward
  frame only (`0 <= days_until_expiration`) under ONE definition in both of its writers.
- **`incumbent_vulnerability_score` reworked to an honest percentage** (first live in the
  2026-07-14 data refresh). Now a value-weighted share of the vendor's FORWARD book expiring
  within 180 days (ESTIMATE, 0-100); the old formula measured portfolio size, not vulnerability.
  Unknown is unforgeable: score empty (never an imputed 0.0) with `vulnerability_basis` naming
  the reason; new disclosure columns `pct_value_expired` / `pct_value_unknown_expiration`.

### Fixed
- **Notice set-asides populate.** The SAM bulk export's real headers are `SetASideCode`/`SetASide`;
  a phantom `SetAside` mapping left `fact_opportunity_notices.set_aside` empty on every notice.
  Both fields now land (`set_aside` on 831 of 1,816 notices; new `set_aside_code` column).
- Deploy `requirements.txt` gains `rapidfuzz` + `requests`; stale pinned counts corrected across
  README and docs; `docs/case-study.md` deleted (stale in every pinned number, duplicative).

## [2.3.0] — 2026-07-13 — "Keep the mods": termination ghost-fix + modification-history signals

### Fixed
- **Terminated contracts stop ghost-riding the forward candidate list.** The bulk loader
  collapsed every award to its latest transaction and never read `action_type_code`, so a
  convenience-terminated contract kept a future `potential_end_date` and a live forward row.
  A parallel per-award transaction digest (cross-file deduped — the FY24/25 Delta member is a
  measured 100% re-list of the archives) now feeds a conservative `complete_likely` inference
  (PoP collapsed to ≤31 days of the termination date; missing dates can never qualify) that
  retargets `selected_expiration_date` to the termination date with the new
  `expiration_date_basis="terminated"`. Rows are kept + flagged, never dropped. On the
  2026-07-13 snapshot: 197 candidates carry termination evidence, 80 `complete_likely`
  retargeted, 4 ghost rows removed from counted forward/grace cohorts.

### Added
- **Mod signals (15 columns on `fact_recompete_candidates`)** — termination (code/date/kind/basis),
  mod_count/velocity/band, ceiling growth (read from the CUMULATIVE
  `potential_total_value_of_award` — measured: `base_and_all_options_value` is a per-transaction
  delta on these exports), deobligation and bridge flags — every signal coverage-gated with a
  named basis; priors fitted to measured distributions in `config/mods_signal.yaml`.
  `fact_transactions` is now populated with signal-bearing evidence rows (59,555; unique ids;
  `description` local-only, excluded from every public artifact).
- **Recently-lapsed / bridge-watch lens** — `successor_visible(/[_basis])` via the conservative
  same-cell proxy (same-parent-IDV task orders and the award itself excluded), an explorer lens
  + home KPI; "no successor visible in public data yet (DoD reporting lags ~90 days)", never
  "missed recompete".
- **Incumbent size-determination shift** — `dim_vendor.size_standard_shift(/_basis)`: per-NAICS
  S→O CODE transitions (never the text field), directional with a named basis; 353 of 2,394
  readable vendors flagged on the current snapshot.
- **Delivery half** — `radar_alerts.deliver` (stdlib email/webhook transports, loud failures),
  `scripts/send_digest.py` (filtered re-render; dry-run), `scripts/export_crm_leads.py`
  (`crm_note` one-liners from the shipped reason codes; formula-injection-defused).
- **Notice-clock** (live response-window countdown for the linked-notice subset) and a
  **Sources Sought / RFI early-warning lane** (staged on the SAM bulk refresh; never fabricates).
- App surfaces: Terminated (verify) / Bridge / Ceiling +N% chips with the fixed disclosure
  "DoD FPDS reporting lags ~90 days; termination signals are ≥3 months old."; size-standard-risk
  badge; "How we compare" methodology copy; `docs/ROADMAP.md` with the no-ML-probabilities gate.
- Validator: generic public-artifact policy (invariant 9 iterates every entry) + a presence-gated
  mods honesty block (triple-equivalences, ghost-fix contract both directions, evidence-id
  uniqueness). 712 → 797 tests; 145 validator PASS checks across both targets.

### Data
- Snapshot 2026-07-13 (FY2019–FY2026 intake unchanged). **Upgraders: delete `data/powerbi/`
  before running `scripts/download_data.py`** — stale CSV siblings from a prior snapshot would
  otherwise sit beside the fresh parquet extraction.

## [2.0.1] — 2026-07-05 — Portfolio-review fixes

### Fixed
- **Price-range strength on loose comparables.** Any tier-D competitive-price-range (which drops the
  incumbent size-band mask) is now labeled **Weak** rather than at most Moderate — a tier-D comparable
  set can sit far outside the incumbent's own run-rate and must not read as trustworthy.
- **Expired contracts no longer bucket forward.** `build_recompete_candidates` now derives the
  expiration bucket via the single `quality_flags.derive_bucket` (182-day boundaries; past-due →
  `"Expired — verify"`; missing date → Data Gap), so a past-due contract can never land in the forward
  `"0-6 Months"` bucket even before the rebake gate.
- **Backtest honesty.** `ptw_backtest` no longer claims "no predecessor→successor pairs on current
  data" (they exist, but are same-IDV task-order noise); added a `date_signed ≤ predecessor pop_end`
  guard so the out-of-sample test builds no range from awards that didn't yet exist.

### Changed
- **Faster ETL.** Opportunity linking vectorized with `rapidfuzz.process.cdist` (result-identical to
  the per-pair path, proven by parity test).
- **Data rebaked** on the 2026-07-05 snapshot; all `validate_data.py` invariants pass. Tier 1 26 → 25
  (a two-day date shift; scorer parity max-abs-diff 0.0).

## [2.0.0] — 2026-07-03 — Demo-grade honesty overhaul

The headline change: expired contracts no longer masquerade as live pursuit targets.

### Fixed
- **Expiration-urgency cliff (the credibility hole).** `_urgency()` / `urgency_score()` returned the
  *maximum* score for any contract with `days_until_expiration <= 0`, so a contract that ended in
  **2003** scored into Tier 1. 34% of candidates (1,581) were expired; 92 of 118 Tier-1 rows were
  unpursuable. Replaced with a graduated curve (active linear decay, a −90..−1 grace window, stale
  → 0). Result: **Tier 1 dropped 118 → 26**, all active or within the 90-day grace window.
- **Inverted data-quality logic.** `_data_quality()` scored empty notes as a perfect 100 — "unknown"
  read as "flawless," producing a fake `average_data_quality_score = 100.0`. Now neutral **70** for
  unknown, −20 per recorded note, −15 per quality flag, floor 20 (honest average **65.9**).
- **`clean_title` remnant leak.** Stripping an `IGF::OT::IGF` prefix left `"IGF"`; now collapses to
  a placeholder so no junk reaches the UI.

### Added
- **Candidate status model** (`active` / `expired_grace` / `expired_stale`) and the **Data Gap**
  quarantine tier (previously shipped in `dim_priority_tier` but never assigned). Stale records are
  excluded from every headline KPI, chart, tier board, and default export.
- **`components/quality.py`** (+ pipeline mirror `src/scoring/quality_flags.py`): title/expiration
  flags, `clean_title` (never renders the raw ~800-char FPDS record or embedded vendor address),
  status/bucket derivation.
- **New expiration buckets** with a separated gray `Expired — verify` quarantine bucket.
- **`scripts/rebake_data.py`** — deterministic, idempotent regeneration of every derived artifact
  for both data dirs; snapshot date derived from `pull_timestamp_utc` (never file mtime).
- **`scripts/validate_data.py`** — auditable integrity gate (scorer parity, no-stale-in-tiers,
  bucket/KPI/quality invariants, schema, snapshot/version, CSV↔Parquet) run in CI.
- **App:** `PUBLIC DATA SNAPSHOT` badge (was misleading `LIVE DATA`), snapshot-freshness banner,
  a "Needs verification" strip for stale records with Verify-on-SAM.gov links, an Explorer status
  filter, a **contract-vehicle rollup** (212 SMIT task orders → one vehicle row), status chips and a
  verify callout on Contract Detail, curated default export, and a rewritten Methodology page.
- **`dashboard_kpi_summary`** additive columns: `snapshot_date`, `scorer_version`, active/grace/stale
  counts & values, `top_dod_component_by_active_value`, vehicle/task-order counts, flag totals.
- **CI** (`.github/workflows/ci.yml`), `pyproject.toml` (ruff), pinned `requirements.txt` +
  `requirements-dev.txt`, and new test suites (`test_rescore`, `test_quality`, `test_data_contract`,
  `test_profile`, `test_export`).
- **Docs:** this changelog, `docs/ARCHITECTURE.md`, `docs/SOP.md`, `docs/DATA_DICTIONARY.md`,
  `docs/DATA_PROVENANCE.md`, `docs/DEMO_SCRIPT.md`, and an honest README.

### Changed
- Runtime honesty: the app recomputes runway/status/bucket **against today** at load, so an aging
  snapshot never shows a lapsed contract as active. The mtime-as-data-age fallback was removed.
- Power BI compatibility preserved: every legacy table/column stays populated; all schema changes are
  additive.

### Parity note
The scoring fix lands in **both** `src/scoring/pursuit_score.py` (pipeline) and
`streamlit_app/components/rescore.py` (app), kept byte-parity by `tests/test_rescore.py`, so a future
`run_pipeline.py` run cannot silently revert it.

## [1.0.0] — prior
Original ETL + star schema + Streamlit app: 8-component pursuit score, Competitive Price Range,
incumbent/agency analysis, facts-vs-estimates labeling.
