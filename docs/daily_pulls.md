# Runbook — Daily pulls from USAspending + SAM.gov

**Status:** documentation + a runnable script stub. No scheduled automation is
switched on in this repo. This runbook shows how to get **daily** raw pulls from
**both** public sources and how to wire a scheduler around them when you decide to.

---

## 1. What already exists

This build already carries the code to pull from both sources — what's missing is
only a *scheduler* and a deliberate decision to run one.

| Source | Client | Key required? | Notes |
|---|---|---|---|
| **USAspending.gov** (awards / "Govspending") | `src/api_clients/public_data.fetch_usaspending_awards` | No | Keyless live API with retry/backoff. The reliable daily path. |
| **USAspending.gov** (bulk) | `src/api_clients/usaspending_bulk.load_usaspending_awards_from_csv` | No | "Custom Award Data" bulk CSVs (agency `097` = DoD). Preferred over the API when present. |
| **SAM.gov** (opportunities live) | `src/api_clients/public_data.fetch_sam_opportunities` | Yes (`SAM_GOV_API_KEY`) | `api.sam.gov` was **unreachable from the original build sandbox** — treat as best-effort. |
| **SAM.gov** (opportunities bulk) | `src/api_clients/sam_bulk.load_sam_opportunities_from_csv` | No | The public "Contract Opportunities" full CSV, **published daily**. The dependable daily SAM path. |

`src/extract/awards.run_extraction()` orchestrates a **single** pull from both
sources at once: it prefers the local bulk exports (no key, no rate limits) and
falls back to the live APIs when a bulk file is absent. Each run writes timestamped
raw JSON envelopes via `save_raw_pull` (never overwrites a prior pull).

### What this repo does *not* have
- **No scheduler.** The only workflow (`.github/workflows/ci.yml`) is a
  data-integrity/boot gate on push/PR — it is not a cron.
- **No snapshot rebuild driver.** The SOP's `run_pipeline.py` / `scripts/rebake_data.py`
  live in the *private project repo*, not here. This repo is the published product
  (app + library + committed sample), synced by hand. So a pull here produces **raw
  envelopes only** — it does not transform → rebake → validate → republish the app's
  snapshot.
- **By design.** Per the README Scope and SOP §1.3–1.4, this product is a *static,
  periodically-refreshed* snapshot (cadence ~monthly, no runtime API calls). Scheduled
  refresh is explicitly assigned to a separate private SaaS. Adding daily automation is
  a conscious departure from that architecture — that's why it isn't on by default.

---

## 2. Run a pull by hand

```bash
py scripts/daily_pull.py                 # -> data/raw/ (default)
py scripts/daily_pull.py --out data/raw  # explicit output dir
py scripts/daily_pull.py --date 2026-07-20   # override the pull's reference "today"
```

`scripts/daily_pull.py` is a thin wrapper over `run_extraction()`. It prints a
per-source count and the path of each raw envelope it wrote. It does **not** commit,
publish, or rebuild the snapshot.

> **Tip:** `data/raw/` is not currently git-ignored. Either add it to `.gitignore`
> or point `--out` at an out-of-tree directory so daily pulls don't get committed by
> accident.

### For a true *daily* pull of each source
- **USAspending:** works as-is via the keyless API fallback (or drop fresh bulk CSVs
  matching the `usaspending_bulk.contracts_globs` in `config/sources.yaml`).
- **SAM.gov:** the reliable daily path is the bulk CSV. Download the latest public
  "Contract Opportunities" full CSV each day to the path named by
  `sam_bulk.contract_opportunities_csv` in `config/sources.yaml` (default:
  `SAM.gov data/ContractOpportunitiesFullCSV.csv`) **before** running the pull. To use
  the live SAM API instead, set `SAM_GOV_API_KEY` and remove/relocate the bulk CSV so
  the loader falls through to the API.

---

## 3. Wire a scheduler (choose one — none is enabled here)

### Option A — GitHub Actions cron (template, not committed as a workflow)

Save as `.github/workflows/daily-pull.yml` **only when you intend to turn it on**.
Add `SAM_GOV_API_KEY` as a repo secret if you want the live SAM path; decide where the
pulls should land (build artifact as below, a release asset, or a commit to a data branch).

```yaml
name: Daily pull
on:
  schedule:
    - cron: "0 11 * * *"   # 11:00 UTC daily (USAspending updates roughly daily)
  workflow_dispatch: {}      # allow manual runs
permissions:
  contents: read
jobs:
  pull:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - name: Pull USAspending + SAM (raw envelopes)
        env:
          SAM_GOV_API_KEY: ${{ secrets.SAM_GOV_API_KEY }}   # omit to use the SAM bulk CSV path
        run: py scripts/daily_pull.py --out data/raw
      - uses: actions/upload-artifact@v4
        with:
          name: daily-raw-pull
          path: data/raw/
```

> The SAM bulk CSV is large; a CI runner would need to download it in a prior step for
> the bulk SAM path. Without the key *and* without the CSV, the SAM side degrades
> gracefully to zero notices (the pipeline still succeeds) — so the out-of-the-box CI
> pull covers USAspending reliably and SAM only if you supply one of the two.

### Option B — local scheduler

- **cron (Linux/macOS):** `0 6 * * * cd /path/to/GovConRadar && py scripts/daily_pull.py --out data/raw >> daily_pull.log 2>&1`
- **Windows Task Scheduler / systemd timer:** run the same `py scripts/daily_pull.py` command daily.

---

## 4. From raw pull to a refreshed app (the missing half)

A daily *pull* only produces raw envelopes. To turn those into the app's live snapshot
you still need the transform → rebake → validate → deploy loop documented in
`docs/SOP_Recompete_Radar_v2.1.md` §6 — whose `run_pipeline.py` / `rebake_data.py` are
**not** in this repo. Fully automating "daily fresh app" therefore means either porting
those drivers here or running them in the private project repo. That's a larger change
than this runbook (and was left as a separate decision — see `FUTURE IMPROVEMENTS.md` #1,
the scheduled alerting bridge).
