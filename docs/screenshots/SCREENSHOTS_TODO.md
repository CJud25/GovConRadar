# Screenshots — capture list

The README and docs reference the PNG files below. This is the exact shot list — drop the PNGs in at
these paths and the galleries complete themselves (the `<img>` tags already point here).

> **Status (2026-07-11): 11 shots ship** — the seven below (captured 2026-07-08, 1440×900,
> bundled-chromium via Playwright) plus four 2026-07 feature shots (`detail_obligation_pace.png`,
> `detail_reason_codes.png`, `incumbents_concentration.png`, `explorer_new_columns.png`). Shots are
> illustrative of the app on the **5,000-row deploy sample** (SAMPLE DATA badge, snapshot 2026-07-07);
> KPI values in older shots may drift slightly from the currently-committed bake because runway windows
> recompute to the capture date. `home_custom.png` (re-shot 2026-07-11 on the redacted sample) uses a
> synthetic "Summit Data Systems" profile via the `?p=` deep link; `detail_refusal.png` uses an active
> `ptw_basis=insufficient` candidate. **PII control: after ANY sample rebuild, visually re-verify every
> title-rendering shot (runway chart, explorer tables, detail headers) before it ships.**

## How to capture

```bash
py -m pip install playwright && py -m playwright install chromium
py -m streamlit run streamlit_app/app.py --server.headless true --server.port 8501
# then, in a script, navigate to each URL below at the given viewport and screenshot.
```

Viewport: **1440 × 900** for all shots. The app is deep-linkable by design, so each shot is just a URL.

## Shots

| File | Page / URL (relative to the running app) | What to show |
|---|---|---|
| `home_demo.png` | `/` (Home, demo profile) | Hero headline, 4 KPI cards, the collapsed "Needs verification" strip. |
| `home_custom.png` | `/?p=<base64 profile>` then Home | Same board after entering a custom company — scores recomputed (before/after). |
| `explorer_vehicles.png` | Explorer, **Roll up by contract vehicle** checked | The vehicle rollup table; header "X vehicles · Y task orders". |
| `detail_price_range.png` | `/?cid=<a candidate with ptw_basis=comparables>` → Contract Detail | Competitive Price Range (Low / Market Median / High) visible. |
| `detail_refusal.png` | `/?cid=<a candidate with ptw_basis=insufficient>` → Contract Detail | The "insufficient comparables" refusal state. |
| `company_form.png` | Your Company | The profile form (the score-as-your-company entry point). |
| `methodology.png` | Methodology | The graduated expired-record policy table + flags glossary. |

## Deep-link helpers

- A candidate id for the price-range shot: run
  `py -c "import pandas as pd; d=pd.read_csv('data/powerbi/fact_recompete_candidates.csv'); print(d[d.ptw_basis=='comparables'].candidate_id.iloc[0])"`
  and use `?cid=<that>`.
- For the refusal shot use `ptw_basis=='insufficient'` in the same query.
- A base64 profile for `?p=` is produced by the app's "Your Company" save (copy the address bar).

Once the PNGs exist here, no code changes are needed — the README image paths already resolve.
