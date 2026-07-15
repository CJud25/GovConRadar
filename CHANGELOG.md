# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project versions the **scorer**
(`SCORER_VERSION`) alongside the app.

## [2.3.1] — 2026-07-15 — Clean-history republish, provenance manifest, PSC catalog

### Security
- **Repository history recreated as one clean commit.** Pre-redaction data (contracting-officer
  personnel titles) was still reachable by raw SHA on dangling commits even after the releases and
  tags that referenced them were deleted — repository delete+recreate is GitHub's only true purge.
  The full development history lives in the private source repo; this public repo now carries a
  single deploy commit per release.

### Added
- **Release provenance manifest** — the data-snapshot zip now carries `manifest.json` (SHA-256 +
  row count per table, plus a source-freshness block); `scripts/download_data.py` verifies every
  table against it on extraction and refuses an asset without one.
- **PSC descriptions backfilled from the GSA PSC manual** (two committed vintages: current and the
  pre-restructure legacy edition): `dim_psc` unknown descriptions went 1,306 → 0, with
  classification and tiers byte-identical (tripwire-verified).
- CI: an **import-walk** over every shipped `src/**/*.py`, advisory **pip-audit**, blocking
  **gitleaks**, and `scripts/check_doc_counts.py` — pinned prose numbers asserted against
  generated values on every push.

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
- `requirements.txt` gains `rapidfuzz` + `requests` (imported by the shipped library; previously
  installed only transitively).
- Stale pinned counts corrected across README and docs; `docs/case-study.md` deleted (two
  release-cycles stale in every pinned number and duplicative of the README).

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
- **Notice-clock** (live response-window countdown for the linked-notice subset) and a
  **Sources Sought / RFI early-warning lane** (staged on the SAM bulk refresh; never fabricates).
- App surfaces: Terminated (verify) / Bridge / Ceiling +N% chips with the fixed disclosure
  "DoD FPDS reporting lags ~90 days; termination signals are ≥3 months old."; size-standard-risk
  badge; "How we compare" methodology copy; `docs/ROADMAP.md` with the no-ML-probabilities gate.
- Validator: generic public-artifact policy (invariant 9 iterates every entry) + a presence-gated
  mods honesty block (triple-equivalences, ghost-fix contract both directions, evidence-id
  uniqueness).

### Data
- Snapshot 2026-07-13 (FY2019–FY2026 intake unchanged). **Upgraders: delete `data/powerbi/`
  before running `scripts/download_data.py`** — stale CSV siblings from a prior snapshot would
  otherwise sit beside the fresh parquet extraction.

## [2.2.0] — 2026-07-11 — Three honesty-first reads + hardened PII redaction

### Added
- **Obligation pace** — descriptive obligation-vs-clock read per order (Detail bar, Explorer chip);
  refuses on ~98% of orders rather than guess. Baked-only; the app never recomputes it.
- **Reason codes** — the pursuit score explained as ● fact / ◐ estimate / ○ not-reported chips,
  recomputed live; never a fabricated number on missing data.
- **Incumbent concentration** — top-incumbent share of expiring obligated dollars per DoD component;
  thin markets show "Unknown". Descriptive only — no HHI number, no market-power bands.
- Validator invariant groups 10–12 for the three reads, plus a **PII drift canary** over public
  titles — `--sample` now runs **69 checks**.

### Security
- **Title redaction extended** (public artifacts only): beyond contact-intro forms (POC:/TPOC/CO:/
  CS:/COR:/spelled-out), personnel-naming office codes now redact structurally — the suffixed
  PK office family (PKA/PKF/PKH/PKS/PKP plus 4-letter forms like PKAA/PKAB and the transposed
  PHK), two-dash PKB forms, the whole Navy N102 office family, slash-path bare-PK offices, and
  leading-surname chains before an office code. Rules are enumeration-verified against the full
  snapshot; an independent validator canary — and a hard PII gate inside the Release builder —
  fail the build if a name-shaped token reaches any public artifact.

## [2.1.0] — 2026-07-08 — One library, sample-first data, FY2019–2026 snapshot (data dated 2026-07-07)

### Changed
- **The app now ships with its library.** `src/` + `config/` are included and the app imports the
  same scoring/quality/price-range code the pipeline runs (the inlined scorer, quality, and PTW
  mirrors were collapsed at the source — one source of truth, enforced by identity tests there).
  `requirements.txt` adds `PyYAML`.
- **Sample-first data.** The committed `data/powerbi/` snapshot is replaced by a 5,000-candidate
  seeded, referentially-intact subsample at `data/sample/` (same schema, KPIs recomputed for the
  sample, badged `SAMPLE DATA` in the app). The full 2026-07-07 snapshot (FY2019–2026:
  35,964 candidates, 5,764 active-forward ≈ $50.0B, 29,343 quarantined) is too large to commit
  (~476 MB CSV) and ships as the `data-snapshot-2026-07-07` GitHub Release (that release and tag
  were later deleted — the tag pointed at a pre-redaction commit; superseded by
  `data-snapshot-2026-07-15`); `scripts/download_data.py` fetches it into `data/powerbi/` for
  full-data local runs.
- The prior committed snapshot (2026-07-05) predates scoring fixes in the current library and no
  longer satisfies scorer parity — removed rather than shipped stale.

### Security
- Published artifacts (sample + Release) exclude `fact_contract_awards.description_raw` and
  `classification_reason`, and whole-redact the handful of titles carrying an explicit
  point-of-contact intro (POC:/ATTN:/COR TO — contracting-officer person names). Continues the
  2026-07-06 scrub-co-names decision; enforced in the source repo's `build_sample.py` +
  `build_release.py` tooling and by the shipped validator (invariant 9, runs in this repo's CI).

## [2.0.1] — 2026-07-05 — Portfolio-review fixes

### Fixed
- **Price-range strength on loose comparables.** Any tier-D competitive-price-range (which drops the
  incumbent size-band mask) is now labeled **Weak** rather than at most Moderate — a tier-D comparable
  set can sit far outside the incumbent's own run-rate and must not read as trustworthy.
- **Expired contracts no longer bucket forward.** Expiration bucketing is unified so a past-due
  contract reads `"Expired — verify"` (never the forward `"0-6 Months"` window); missing-end-date rows
  fall to the Data Gap tier.

### Changed
- **Data rebaked** on the 2026-07-05 public snapshot; all `validate_data.py` invariants pass.
  Tier 1 26 → 25 (a two-day date shift; scorer parity max-abs-diff 0.0).

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
