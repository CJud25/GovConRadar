# Standard Operating Procedure — GovCon Recompete Radar Snapshot Refresh & Deploy

| Field | Value |
|---|---|
| **Document ID** | SOP-RR-002 |
| **Title** | Refreshing, Validating, and Deploying the GovCon Recompete Radar Public Snapshot |
| **Version** | 2.1 |
| **Effective Date** | 2026-07-03 |
| **Supersedes** | SOP v2.0 / `docs/SOP.md` |
| **Author** | Technical Writing (contract) |
| **Classification** | Public — Portfolio |
| **Applies to product** | GovCon Recompete Radar, Scorer v2.0.0 |

---

## 1. Purpose & Scope

### 1.1 Purpose
This SOP defines the controlled, repeatable procedure for refreshing the public data
snapshot of the **GovCon Recompete Radar** analytics product, validating its integrity, and
deploying it to the public Streamlit Community Cloud application. It exists so that any
qualified operator can perform a refresh identically, with an auditable gate at each step, and
so that no release can silently regress the product's core credibility guarantees.

### 1.2 Scope — in scope
- Regenerating the derived star-schema artifacts from the shipped fact tables.
- Running the integrity validators, test suite, and linter.
- Committing the refreshed snapshot to the project repository.
- Synchronizing the deploy subset to the public repository **`CJud25/GovConRadar`** and
  confirming the live application reflects the new snapshot.

### 1.3 Scope — out of scope
- The private, full ETL pipeline execution (`run_pipeline.py`) is referenced but is only
  required when new bulk source exports have landed; it needs local bulk CSV exports and is
  documented here only at the interface level.
- Accounts, alerting, and scheduled automated refresh belong to a separate private SaaS and
  are **not** covered by this SOP.
- Power BI report (`.pbip`) authoring is out of scope; see `CLAUDE.md` and project memory.

### 1.4 Refresh cadence
Refresh when new USAspending.gov / SAM.gov bulk exports land — practically **monthly**, or
before a demonstration. The application recomputes each contract's runway to *today* on every
load, so an existing snapshot does not become dangerously stale between refreshes; it simply
stops including newly issued awards (the freshness banner discloses this).

---

## 2. Applicability, Audience & Roles

### 2.1 Audience
This procedure is written for the **Operator** who maintains the public snapshot, and for the
**Reviewer/Approver** who authorizes release. Both are expected to work from the project
repository root and to be familiar with the product's data-integrity brand (Section 4.4).

### 2.2 Roles & responsibilities

| Role | Responsibilities | Authority |
|---|---|---|
| **Operator** | Executes Sections 6.1–6.8: rebake, validate, test, lint, commit, deploy sync, confirm reboot. Halts on any red gate and does not proceed. Records the snapshot date in the commit message. | May execute the refresh; may **not** override a failed validator or waive a gate. |
| **Reviewer / Approver** | Confirms the acceptance criteria in Section 7 are met (both validators PASS, tests pass, ruff clean, scorer parity max diff 0.0). Approves the deploy to the public repo. Owns the decision to roll back (Section 9). | Authorizes release; owns rollback decisions; owner-only Streamlit console and repo-visibility actions. |
| **Pipeline/Scoring maintainer** (as needed) | Only when scoring *logic* changes: edits the ONE scorer library (`src/scoring/`) and bumps `SCORER_VERSION` at the source (Section 6.9). | May change scoring logic; owns the single scorer library the app imports. |

> **Segregation note.** The person who runs the refresh should not also be the sole approver
> of a release that changes scoring logic. For a routine data-only refresh, Operator and
> Reviewer may be the same individual provided all Section 7 gates are green.

---

## 3. References & Definitions

### 3.1 Reference documents (authoritative sources for this SOP)

| Reference | Role |
|---|---|
| `docs/SOP.md` | Prior operator runbook — **superseded and absorbed by this document.** |
| `CHANGELOG.md`, entry `[2.0.0]` | The demo-grade honesty overhaul this SOP reflects. |
| `README.md` | Headline figures, quickstart, validation contract. |
| `docs/ARCHITECTURE.md` | Data flow; two-copies-in-parity scorer; snapshot-not-mtime; additive schema. |
| `docs/DATA_PROVENANCE.md` | Sources; facts vs. estimates; the coverage gap behind the expired-record policy. |
| `docs/DATA_DICTIONARY.md` | v2.0.0 derived columns, flags, and KPI overhaul. |
| `scripts/rebake_data.py` | Deterministic regeneration of derived artifacts. |
| `scripts/validate_data.py` | The auditable integrity gate. |
| `CLAUDE.md` | Environment, deploy-repo sync, and deploy invariants. |

### 3.2 Glossary

| Term | Definition |
|---|---|
| **Recompete candidate** | An expiring DoD cyber/IT contract award identified by the pipeline as a potential future recompete opportunity. A row in `fact_recompete_candidates`. |
| **Pursuit score** | An 8-component weighted score (0–100) estimating fit of a candidate against a company profile. Computed by `score_candidate()`; stored per-component in `fact_scoring_breakdown`. An **estimate**, never an official prediction. |
| **Priority tier** | The mapping of a candidate to `Tier 1: Pursue Now`, `Tier 2: Capture Research`, `Tier 3: Monitor`, `Tier 4: Low Priority`, or the quarantine **`Data Gap`** tier. Tiers 1–4 are derived from the pursuit score; **`Data Gap` is assigned by override** (stale / missing end date / garbled), never by score. |
| **Data Gap** | The quarantine tier for records the data cannot stand behind. Data Gap records are excluded from every headline KPI, chart, tier board, and default export; reachable only via "Needs verification" surfaces. |
| **`candidate_status` = `active`** | `days_until_expiration ≥ 0`. On the board and in every headline. |
| **`candidate_status` = `expired_grace`** | Expired within the last 90 days (`−90..−1`). Often mid-recompete or bridged; flagged to verify. Since T17, the Pipeline Explorer's default (trustworthy) view is forward-dated only — grace rows sit behind the "Include excluded rows" toggle, never deleted. |
| **`candidate_status` = `expired_stale`** | Expired more than 90 days ago (`< −90`), **or** missing usable runway. Treated conservatively as dead data → Data Gap quarantine. |
| **Snapshot date** | `SNAPSHOT_DATE` = max `pull_timestamp_utc` in `fact_opportunity_notices`, stamped into `dashboard_kpi_summary`. Derived from the data — **never** file mtime. The runtime source of truth for "data as-of." |
| **Fact** | A read-only value from the public sources: identifiers, awardee/UEI, agencies, dollars, PoP dates, NAICS/PSC, competition & set-aside codes, `source_url`, `ptw_*`. Never mutated by the rebake. |
| **Estimate** | An analytical value, never an official prediction: cyber/IT classification confidence, recompete candidacy/windows, pursuit score & tier, Competitive Price Range, incumbent analysis, SAM.gov link confidence. Labeled as estimates in the app and methodology. |
| **Competitive Price Range** | A range of what comparable work has historically been *won* for (a fact-derived range), which **refuses to estimate** below a comparables floor rather than inventing a number. Not a price-to-win / bid prediction. |
| **Scorer parity** | The guarantee that the app's live re-score (`streamlit_app/components/rescore.py`) reproduces the pipeline-baked scores (`src/scoring/pursuit_score.py`) exactly — max absolute diff 0.0, 100% tier match. Enforced by the validator and `tests/test_rescore.py`. |

---

## 4. Safety, Data-Integrity & PII-Handling Controls

### 4.1 Nature of the data
All source data is **public-domain federal data** (USAspending.gov, SAM.gov). USAspending is
public-domain and needs no API key. SAM.gov's API key is relevant only on the fallback path,
not the bulk path used for routine refreshes.

### 4.2 Free-text PII scrub (mandatory)
Free-text award fields (for example `description_raw`) can leak contracting-officer PII —
names, `.mil` email addresses, phone numbers. `export/powerbi_export.py` runs
`scrub_free_text_pii` (defined in `transform/cleaning.py`) over `FREE_TEXT_PII_COLUMNS` before
writing any shipped table. Dedicated contact-PII columns are excluded from the export schema
entirely.

- **Control:** If a new free-text column is ever added to the export, it MUST be added to the
  scrub coverage before that column ships. Do not ship an unscrubbed free-text column.

### 4.3 Git-history PII rule (critical — the publishing gotcha)
The deploy repository **`CJud25/GovConRadar` is public.** **Scrubbing PII in a *later* commit
does NOT remove it from git history** — anyone can recover the pre-scrub content by SHA.

- **Control:** If data is ever regenerated/scrubbed and republished after PII was present in a
  prior published commit, the deploy repository MUST be republished as a **clean, single
  (orphan) commit** — not a follow-up fix commit. See `CLAUDE.md` → "Data & git notes."

### 4.4 The honesty brand (integrity controls that must not regress)
The product's brand is honesty: every number on screen must survive scrutiny from a capture
professional. Facts are labeled facts; estimates are labeled estimates; and records the data
cannot stand behind are **quarantined, not dressed up as leads.** The following are release
guarantees, each enforced by `scripts/validate_data.py` (Section 8):

- No `expired_stale` record and no `days_until_expiration < −90` record appears in Tiers 1–4.
- No expired (days `< 0`) record appears in a forward expiration bucket.
- Every headline KPI re-derives from the fact tables.
- `title_display` never leaks a raw FPDS record or an `IGF::` code prefix.
- The app's live re-score reproduces the baked scores exactly (parity).

### 4.5 Synthetic / sample-data control
The bundled `assets/sample_data/` is a small synthetic subset (`generate_sample_data.py`),
badged `SAMPLE DATA` in the app, and must **never** be presented as real awards. The `is_demo`
vendor profile is labeled a demo throughout. Never present synthetic vendor/scoring data as
real.

---

## 5. Prerequisites

### 5.1 Environment (Windows)
- Use the **`py` launcher** for all commands in this SOP. On this machine `python` /
  `python3` are the broken Microsoft Store stub and MUST NOT be used. (On Linux/CI, `python`
  is correct.)
- The bundled `venv/` is a Linux venv from the original build sandbox and will **not** run on
  Windows (`Exec format error`) — ignore it; use the base interpreter via `py`.
- Full-path interpreter for Git Bash, if needed, points at the local CPython install
  (e.g. `%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe`).

### 5.2 Dependencies (pinned)
Install the pinned development dependencies before a refresh:

```bash
py -m pip install -r requirements-dev.txt
```

Runtime deploy dependencies are pinned separately in `requirements.txt`
(`streamlit>=1.44`, pandas, numpy, plotly, pyarrow). Python 3.10+ is required.

### 5.3 Working directory & tooling
- Run **all** commands from the project repository root.
- Fresh source data (only when re-running the full ETL) requires local bulk CSV exports; the
  rebake and validators run without the pipeline against the shipped star schema.
- Deploy tooling: GitHub CLI at `C:\Program Files\GitHub CLI\gh.exe`, authenticated as
  `CJud25`.

### 5.4 Entry criteria
Do not begin a refresh unless: (a) the working tree is clean or intentionally staged; (b) the
correct source exports are in place if a pipeline run is intended; and (c) you can run the
validators locally.

---

## 6. Procedure — The Refresh Loop

> **Gating rule (applies to every step below):** each step has an explicit gate. **Do not
> proceed to the next step on a red result.** In particular, a failed (`VALIDATION FAILED`)
> validator is a hard stop — never ship red.

### 6.1 Step 1 — (Conditional) Run the pipeline
Only when new bulk source exports have landed and the underlying facts must change.

```bash
py run_pipeline.py                      # writes data/powerbi/ (facts + dims)
```

- **Input:** local bulk CSV exports (USAspending FY `097` contracts; SAM.gov opportunities).
- **Output:** refreshed `data/powerbi/` star schema.
- **Gate:** pipeline completes without error. If no new source data has landed, **skip this
  step** and rebake the existing shipped facts.

### 6.2 Step 2 — Rebake derived artifacts
Deterministically regenerate every *derived* column and rollup from the shipped fact tables
under scorer v2.0.0. Idempotent; safe to re-run.

```bash
py scripts/rebake_data.py               # both data/powerbi/ and assets/sample_data/
```

- **Input:** `data/powerbi/` and `streamlit_app/assets/sample_data/` fact tables.
- **Output:** regenerated `fact_recompete_candidates`, `fact_scoring_breakdown`,
  `dim_agency`, `dim_vendor`, `dashboard_kpi_summary`, `data_quality_report`. The snapshot
  date is derived from max `pull_timestamp_utc` (never mtime) and stamped into
  `dashboard_kpi_summary`.
- **Gate:** the script prints `Rebake complete.` with a per-table manifest (rows + sha) and
  exits 0. If it cannot derive a snapshot date it exits non-zero and instructs you to pass
  `--snapshot-date YYYY-MM-DD`; supply it only with justification.

### 6.3 Step 3 — Validate (the integrity gate)
Run **both** validator invocations. This is the primary gate.

```bash
py scripts/validate_data.py             # data/powerbi/  (CSV)
py scripts/validate_data.py --sample    # bundled sample (CSV + Parquet)
```

- **Input:** the just-rebaked star schema (and the sample bundle).
- **Output:** a per-invariant `[PASS]`/`[FAIL]` report, ending in either
  `VALIDATION PASSED — all invariants hold.` (exit 0) or
  `VALIDATION FAILED — N issue(s):` (exit 1).
- **Gate:** **both** invocations must print `VALIDATION PASSED`. Any FAIL is a hard stop — go
  to Section 8, remediate the root cause, re-run Step 2, and re-validate. Do not proceed on
  red.

### 6.4 Step 4 — Tests
```bash
py -m pytest -q
```

- **Output:** the full suite passes.
- **Gate:** zero failures. `tests/test_rescore.py` (scorer parity) and the v2 suites
  (`test_quality`, `test_data_contract`, `test_profile`, `test_export`) must pass.

### 6.5 Step 5 — Lint
```bash
py -m ruff check .
```

- **Gate:** ruff reports no issues (clean).

### 6.6 Step 6 — Commit (single clean commit)
```bash
git add -A && git commit -m "data: refresh snapshot <YYYY-MM-DD>"
```

- Use the snapshot date (as stamped by the rebake) in the message.
- **Gate:** commit succeeds; working tree reflects only intended changes.
- **PII reminder:** if this refresh scrubbed PII that had previously been published to the
  public deploy repo, do **not** publish a follow-up fix — apply the orphan-commit rule in
  Section 4.3 to the deploy repo.

### 6.7 Step 7 — Deploy sync to `CJud25/GovConRadar`
The live app deploys from the **separate public repo `CJud25/GovConRadar`**, kept in sync by hand. Since 2026-07-08 the deploy ships
the app **plus its library** and a committed seeded sample — no `data/powerbi/` is committed
(the full snapshot ships as a GitHub Release built by `scripts/build_release.py`).

```bash
gh repo clone CJud25/GovConRadar
# From the project repo, overlay the git-tracked deploy subset (never a raw cp -r —
# this cannot carry __pycache__/, *.pyc, or untracked strays):
git archive master -- streamlit_app src config data/sample scripts/validate_data.py \
  scripts/download_data.py pipeline_demo sql run_sql.py .streamlit docs/ARCHITECTURE.md \
  | tar -x -C <GovConRadar-clone>
# Rebuild the deploy's richer sample from the fresh snapshot, then verify IN the clone:
py scripts/build_sample.py --rows 5000 --out <GovConRadar-clone>/data/sample
(cd <GovConRadar-clone> && python scripts/validate_data.py --sample && python scripts/check_doc_counts.py && python scripts/smoke_app.py)
git add -A && git commit -m "deploy: sync snapshot <YYYY-MM-DD>"
git push
```

> **Control — PUBLIC-artifact PII policy (re-verify on EVERY new snapshot).** The
> contact-title redaction (`transform.cleaning.CONTACT_TITLE_RE`) was enumeration-verified
> against the 2026-07-07 snapshot only. Validator invariant 9 proves the policy was *applied
> with the current rules*, not that the rules are *sufficient* for new data. Before building a
> new sample or Release: re-enumerate marker rows over the fresh snapshot
> (`grep -iE "POC|ATTN|ATT|COR" over candidates/notices titles`), eyeball every hit, and extend
> `CONTACT_TITLE_RE` if new contact-intro forms appear. The Release asset has no CI gate —
> validate the roundtrip locally per `build_release.py`'s docstring before `gh release upload`.

> **Control — never ship build artifacts.** Compiled `__pycache__/*.pyc` files embed the
> absolute local build path (your username). Exclude them from the copy
> (`robocopy … /XD __pycache__`, or delete after `cp -r`), and confirm GovConRadar's
> `.gitignore` carries `__pycache__/` and `*.pyc`. A committed `.pyc` leaks a local path into
> public history (Section 4.3 explains why that is hard to undo).

**Deploy invariants (verify before pushing):**

| Invariant | Requirement |
|---|---|
| Sample completeness | The synced `data/sample/` set **MUST include `fact_ptw_comparables`** (the app's live price-range recompute reads it) **and `fact_opportunity_notices`** (Contract Detail joins it via `bridge_award_opportunity_links` to render linked SAM.gov notices) — `build_sample.py` carries both by construction; `validate_data.py --sample` in the clone is the gate. |
| Config location | **`.streamlit/config.toml` MUST be at the deploy repo ROOT**, not `streamlit_app/.streamlit/`. A copy inside `streamlit_app/` is never loaded. It sets `showErrorDetails="none"` so tracebacks/paths never reach public users. |
| Library completeness | `src/` and **all six** `config/*.yaml` ship together (`utils.config` eagerly loads every yaml at import) and `requirements.txt` carries `PyYAML` — a partial tree crashes the app on first page load. |
| PII policy | `validate_data.py --sample` (invariant 9) passes in the clone: no public-excluded columns, no contact-marked titles. |

- **Gate:** push succeeds and the deploy subset is complete and correctly placed per the table
  above.

### 6.8 Step 8 — Streamlit Cloud reboot & confirm
Streamlit Cloud auto-redeploys on push. If the app was stopped, perform a manual **Reboot** in
the Streamlit dashboard (owner-only action).

- Confirm the live app at `cjudk25.streamlit.app` shows the new **PUBLIC DATA SNAPSHOT** badge
  and the snapshot-freshness banner reflecting `<YYYY-MM-DD>`.
- Note: the app can be **viewer-restricted** (anonymous requests 303 to Streamlit auth)
  independent of repo visibility — set "Who can view" for a public portfolio link.
- **Gate:** the live app loads and reflects the new snapshot date.

### 6.9 Conditional — Scoring-logic changes (parity requirement)
If you changed **scoring logic** (not just data): the scorer is ONE library since the 2026-07-06/07
twin collapses — edit it at the source and keep the residual app glue in sync:

- Edit `src/scoring/pursuit_score.py` (and `src/scoring/quality_flags.py` for quality primitives) —
  the app imports these directly; there are no inlined copies to hand-sync. If `WEIGHTS` or the demo
  profile change, update `config/scoring_weights.yaml` / `vendor_profile_mock.yaml` AND the residual
  mirrors in `streamlit_app/components/rescore.py` (pinned by `tests/test_rescore.py`).
- Bump `SCORER_VERSION` in `src/scoring/pursuit_score.py` (rescore re-exports it; a stale bake
  fails the validator's version check).
- Then run Steps 2–7 (rebake → validate → test → lint → commit → deploy).
- Parity is enforced by `tests/test_rescore.py` and by the validator's parity checks, so a
  future `run_pipeline.py` run cannot silently revert the fix.

---

## 7. Verification & Acceptance

A refresh is **accepted for release** only when every criterion below is objectively true.
"Green" is defined as:

| # | Criterion | Evidence |
|---|---|---|
| 7.1 | Lint clean | `py -m ruff check .` reports no issues. |
| 7.2 | Tests pass | `py -m pytest -q` — zero failures. |
| 7.3 | Primary validator PASS | `py scripts/validate_data.py` prints `VALIDATION PASSED — all invariants hold.` (exit 0). |
| 7.4 | Sample validator PASS | `py scripts/validate_data.py --sample` prints `VALIDATION PASSED`. |
| 7.5 | Scorer parity | `parity:pursuit_score max abs diff == 0.0` and `parity:priority_tier 100% match` both PASS (max diff 0.0 / 100% tier). |
| 7.6 | No stale in tiers | `no expired_stale in Tiers 1-4` and `no days<-90 row in Tiers 1-4` PASS. |
| 7.7 | KPI re-derives | `kpi:*` checks PASS (KPIs tie to the fact tables). |
| 7.8 | Title safety | `title_display never matches raw-record pattern` and `no IGF:: in any title_display` PASS. |
| 7.9 | Snapshot / version | `snapshot_date present & parseable` and `scorer_version matches rescore.SCORER_VERSION` PASS. |
| 7.10 | Deploy integrity | GovConRadar contains `fact_ptw_comparables.csv` + `fact_opportunity_notices.csv`, and `.streamlit/config.toml` is at the repo root. |
| 7.11 | Live confirmation | The live app shows the new snapshot date and the `PUBLIC DATA SNAPSHOT` badge. |

The Reviewer/Approver records acceptance against this checklist before authorizing release. Any
single red criterion blocks release.

---

## 8. Troubleshooting — Validator-Failure Lookup

`scripts/validate_data.py` prints `[PASS]`/`[FAIL]` per invariant and a summary. Diagnose using
the failing invariant name. Remediation almost always means fixing the root cause and re-running
`rebake_data.py`, then re-validating (never editing baked CSVs by hand).

| Failing invariant (as printed) | Likely cause | Remedy |
|---|---|---|
| `parity:pursuit_score max abs diff == 0.0` | Scorer edited but data not re-baked, or the residual `rescore.WEIGHTS` mirror drifted from `config/scoring_weights.yaml`. | Re-run `py scripts/rebake_data.py`; if it persists, reconcile `rescore.WEIGHTS`/profile glue with the config yamls and re-bake. |
| `parity:priority_tier 100% match` | Tier thresholds changed without a re-bake. | Re-bake after any `PRIORITY_TIER_THRESHOLDS` change. |
| `no expired_stale in Tiers 1-4` | Data Gap override not applied. | Check the Data Gap / `is_quarantined` wiring in `scoring/quality_flags.py` + `pursuit_score.py`; re-bake. |
| `no days<-90 row in Tiers 1-4` | Long-expired record leaked into a scored tier. | Verify status derivation (`quality.derive_status`) and the override; re-bake. |
| `buckets partition (all rows in a known bucket)` | A bucket value outside `quality.BUCKET_ORDER`. | Re-bake so buckets are re-derived from days. |
| `no expired (days<0) row in a forward bucket` | Expired row baked into `0-6`/`6-12`/… bucket. | Re-bake (recomputes buckets from snapshot-relative days). |
| `bucket <-> days consistency` | Bucket baked against a different date than current days. | Re-bake (recomputes days from snapshot, then buckets). |
| `bucket_sort consistency` | `expiration_bucket_sort` out of step with the bucket label. | Re-bake. |
| `days_until_expiration matches snapshot recompute` | Days baked against a stale/incorrect snapshot date. | Re-bake; confirm `snapshot_date` from `fact_opportunity_notices` is correct (or pass `--snapshot-date`). |
| `kpi:active_candidate_count` / `kpi:tier_1_count` / `kpi:expired_stale_count` / `kpi:active_pipeline_value within $1` | A KPI formula diverged from the facts. | Reconcile `build_kpi_summary` in `rebake_data.py`; re-bake. |
| `no unflagged garbled titles` | A raw FPDS/pipe-delimited title not flagged. | Check `quality.flag_garbled_title`; re-bake. |
| `title_display never matches raw-record pattern` | `clean_title` let a raw-record remnant through. | Tighten `quality.clean_title`; re-bake. |
| `no IGF:: in any title_display` | An `IGF::` code prefix leaked into the display title. | Fix `clean_title` prefix stripping; re-bake. |
| `schema:<table>` (missing columns) | A required column absent (additive-schema violation, or bad export). | Restore the missing column via the pipeline/export; re-bake; re-validate. |
| `snapshot_date present & parseable` | `dashboard_kpi_summary.snapshot_date` empty/unparseable. | Ensure the rebake stamped a valid snapshot; re-bake. |
| `scorer_version matches rescore.SCORER_VERSION` | `SCORER_VERSION` bumped without a re-bake (rescore re-exports the source constant). | Bump `SCORER_VERSION` in `src/scoring/pursuit_score.py`; re-bake. |
| `csv==parquet:<table>` | CSV and Parquet copies of the sample bundle differ in shape/columns. | Re-bake (regenerates both formats together for the sample dir). |

If a failure cannot be resolved by re-baking and reconciling the named component, do **not**
ship. Escalate to the Reviewer/Approver and, if scoring logic is implicated, the Pipeline/Scoring
maintainer.

---

## 9. Rollback Procedure

The `data/powerbi/` star schema and the Streamlit sample data are versioned in git, so a bad
refresh is always recoverable.

### 9.1 After a commit (preferred — keeps history auditable)
```bash
git revert <commit>
```

### 9.2 Before pushing (discard the working-tree change)
```bash
git checkout -- data/ streamlit_app/assets/sample_data/
```

### 9.3 Mandatory post-rollback verification
Re-run **both** validators after any rollback to confirm the restored state is green:

```bash
py scripts/validate_data.py && py scripts/validate_data.py --sample
```

### 9.4 Deploy rollback
If a bad snapshot already reached `CJud25/GovConRadar`, roll the deploy repo back to its prior
good commit (revert or reset to the last known-good deploy commit), push, and Reboot the app if
needed. **PII caveat:** if the rolled-back content contained PII that should never have been
published, a revert does not erase it from history — apply the orphan-commit rule (Section 4.3).

---

## 10. Change Control & Revision History

### 10.1 Change control
- This SOP is version-controlled with the product. Material changes to the procedure require
  Reviewer/Approver sign-off.
- Any change to scoring logic, the validator invariants, or the deploy invariants MUST be
  reflected here before the next release that depends on it.
- **This document supersedes `docs/SOP.md`**, which is retained for historical reference only.

### 10.2 Revision history

| Version | Date | Author | Summary of change |
|---|---|---|---|
| 1.0 | (prior) | Project team | Original operator runbook: ETL + star schema + Streamlit app; 8-component pursuit score, Competitive Price Range, incumbent/agency analysis, facts-vs-estimates labeling. |
| 2.0 | 2026-07-03 | Project team | Demo-grade honesty overhaul (scorer v2.0.0): candidate-status model, Data Gap quarantine, `rebake_data.py` + `validate_data.py` integrity gate, snapshot-not-mtime, additive KPI overhaul, CI. Captured in `docs/SOP.md`. |
| **2.1** | **2026-07-03** | **Technical Writing (contract)** | Reformatted into a controlled, section-numbered SOP with explicit scope, roles, glossary, PII/data-integrity controls, gated procedure, acceptance criteria, validator-keyed troubleshooting table, and rollback. Supersedes `docs/SOP.md`. |

---

*End of SOP-RR-002, Version 2.1.*
