# Data provenance

Where every number comes from, and how the shipped/sample data is produced.

## Sources (all public federal data)

- **USAspending.gov** — DoD (agency code 097) contract awards, via local FY bulk CSV exports
  (`FY*_097_Contracts_*`) with a live-API fallback. Bulk rows carry
  `period_of_performance_potential_end_date`, so per-award detail hydration is skipped. Provides
  identifiers, dollars, dates, agency/subagency, incumbent/UEI, NAICS/PSC, competition & set-aside codes.
- **SAM.gov** — Contract Opportunity notices (`ContractOpportunitiesFullCSV.csv`), fuzzy-matched to
  awards in `bridge_award_opportunity_links`. Provides the opportunity-notice lane and the
  `pull_timestamp_utc` that defines the snapshot date.

USAspending is public-domain and needs no key. SAM.gov's API needs a key only on the fallback path.

## Facts vs. estimates

- **Facts** (from the sources, read-only): PIID, awardee/UEI, agencies, obligated amount & ceiling,
  PoP dates, NAICS, PSC, pricing type, set-aside type, extent competed, offers received, `source_url`.
- **Estimates** (analytical, never official predictions): cyber/IT classification confidence, recompete
  candidacy and windows, the pursuit score & tier, the Competitive Price Range, incumbent analysis, and
  SAM.gov link confidence. All are labeled as estimates in the app and in
  [`methodology_notes.md`](methodology_notes.md).

## Known coverage gap (why the expired-record policy exists)

USAspending's extraction filters on `last_modified_date` over a lookback window, **not** on PoP end
date (the endpoint has no server-side PoP-end filter). Contracts with no obligation/modification
activity in that window are systematically absent — the single largest coverage gap
(`data_quality_report.known_limitation_1`). This is precisely why a contract expired **months-to-decades**
ago is treated as **`expired_stale` / Data Gap**: given the lookback window, such a record has almost
certainly already been re-awarded and is dead data, not a lead. A contract expired only weeks ago
(`expired_grace`) is often mid-recompete or bridged, so it stays on the board, flagged to verify.

## Derived data & the rebake

`scripts/rebake_data.py` (private build repo — not shipped in this public repo) regenerates every
derived column and rollup deterministically from the fact tables (see [`DATA_DICTIONARY.md`](DATA_DICTIONARY.md)).
It never invents facts — it only derives from shipped columns. `scripts/validate_data.py` then proves
every KPI re-derives from the facts and the app's live re-score reproduces the baked scores exactly.

## Sample data (`streamlit_app/assets/sample_data/`)

The bundled sample is a small synthetic subset produced by `streamlit_app/generate_sample_data.py`
(which runs real transform/scoring logic over mock inputs), then passed through the same
`rebake_data.py` (private build repo — not shipped in this public repo) so it carries identical v2
columns. It lets the app run on Streamlit Community Cloud
with **no pipeline and no API access**. It is clearly badged `SAMPLE DATA` in the app and must never be
presented as real awards. The `is_demo` vendor profile is likewise labeled a demo throughout.
