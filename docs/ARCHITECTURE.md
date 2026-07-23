# Architecture

The pipeline is the single source of truth. It ingests public federal data, builds a star schema, and
both consumers — the Power BI report and the Streamlit app — read that same schema (the Power BI
report is maintained in the private build repo and is not shipped in this public deploy; the CSV
star schema it consumes is). Business logic lives in the pipeline (the app imports the same
scoring/quality library — no mirrors since 2026-07-07), never invented in a consumer.

```mermaid
flowchart TD
    subgraph Sources["Public federal data"]
        USA[USAspending.gov<br/>award bulk CSV / API]
        SAM[SAM.gov<br/>opportunity notices]
    end

    subgraph ETL["Private ETL — run_pipeline.py (src/)"]
        EX[extract → clean → classify]
        RC[recompete candidates]
        PTW[Competitive Price Range<br/>src/scoring/price_to_win.py]
        LINK[opportunity linking<br/>award ↔ notice bridge]
        SCORE[pursuit scoring v2.0.0<br/>src/scoring/pursuit_score.py<br/>+ quality_flags.py]
        EXPORT[powerbi_export.py]
        EX --> RC --> PTW --> LINK --> SCORE --> EXPORT
    end

    subgraph Star["data/powerbi/ — star schema (CSV)"]
        FRC[(fact_recompete_candidates)]
        FSB[(fact_scoring_breakdown)]
        FPTW[(fact_ptw_comparables)]
        FON[(fact_opportunity_notices)]
        BR[(bridge_award_opportunity_links)]
        DIMS[(dim_agency / dim_vendor /<br/>dim_priority_tier / dim_date / …)]
        KPI[(dashboard_kpi_summary)]
        DQ[(data_quality_report)]
    end

    subgraph Rebake["Deterministic post-processing"]
        RB[scripts/rebake_data.py<br/>derive status/flags/buckets,<br/>re-score, rebuild rollups+KPIs]
        VAL[scripts/validate_data.py<br/>integrity gate → CI]
    end

    subgraph Consumers
        APP[Streamlit app<br/>streamlit_app/ — imports the one scorer library,<br/>recomputes runway to TODAY]
        PBI[Power BI report<br/>(private build repo)]
        SAMPLE[assets/sample_data/<br/>CSV + Parquet fallback]
    end

    USA --> EX
    SAM --> EX
    EXPORT --> Star
    Star --> RB --> Star
    Star --> VAL
    Star --> APP
    Star --> PBI
    SAMPLE -. offline fallback .-> APP
```

## Key design decisions

- **One scorer library (Option D, 2026-07-06/07).** `src/scoring/pursuit_score.py` is THE scorer and
  `src/scoring/quality_flags.py` THE quality module; the app imports them (`app.py` puts `src/` on
  `sys.path`), and `streamlit_app/components/rescore.py` keeps only app glue. The former inlined
  scorer/quality/price-to-win mirrors were collapsed. In this public deploy the regression tripwire
  is `scripts/validate_data.py` (recompute parity, max abs diff 0.0); the pytest suites
  (`tests/test_rescore.py`, `tests/test_price_to_win.py::test_app_uses_the_one_engine_and_config`)
  live in the private build repo. The public deploy repo ships `src/` + `config/` for the same reason.
- **Rebake vs. pipeline.** `run_pipeline.py` builds the star schema from raw sources.
  `scripts/rebake_data.py` (private build repo — not shipped in this public repo) deterministically
  regenerates *derived* columns and rollups from the shipped fact tables under scorer v2.0.0 — used to
  re-bake after a scoring change without a full ETL run. Both converge on the same output (the export
  emitter carries the same v2 derivations).
- **Snapshot date, not mtime.** `SNAPSHOT_DATE` = max `pull_timestamp_utc` in
  `fact_opportunity_notices`, stamped into `dashboard_kpi_summary`. The app recomputes runway against
  *today* on load, so an aging deploy never shows a lapsed contract as active.
- **Additive schema only.** Every shipped table/column stays populated so Power BI keeps working; v2
  columns are added, never renamed or dropped.
- **CSV vs. Parquet.** `data/powerbi/` ships **CSV only**; the bundled `assets/sample_data/` ships
  **CSV + Parquet**. The validator diffs the two formats only where both exist.

See `CLAUDE.md` in the private build repo for the deeper pipeline/step details and the deploy-repo sync.
