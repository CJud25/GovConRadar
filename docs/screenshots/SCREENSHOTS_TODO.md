# Screenshots — capture list

The README and docs reference the PNG files below. This is the exact shot list — drop the PNGs in at
these paths and the galleries complete themselves (the `<img>` tags already point here).

> **Status (2026-07-16): 11 shots ship.** Four were re-captured 2026-07-16 on the **full 2026-07-15
> public snapshot** (live mode, "PUBLIC DATA SNAPSHOT" badge, 1440×900, Playwright bundled chromium)
> because the UI they show changed: `explorer_new_columns.png` (now shows the Displacement k-of-N and
> Prime-path columns plus the "Displacement signals, then score" ordering lens),
> `explorer_vehicles.png` (rollup on the full snapshot: 249 vehicles · 587 task orders),
> `detail_price_range.png` (cid `RC-CONT_AWD_HT003825C0006_9700_-NONE-_-NONE-`, `ptw_basis=comparables`
> — shows the new "Incumbent displacement signals" panel above the range), and `detail_refusal.png`
> (cid `RC-CONT_AWD_W911QX24F0037_9700_W911QX23D0007_9700`, `ptw_basis=insufficient`). Every
> re-captured shot was visually re-verified for title PII before shipping. The other seven still date
> from 2026-07-08/11 on the **5,000-row deploy sample** (SAMPLE DATA badge, snapshot 2026-07-07); KPI
> values in those may drift slightly from the current bake because runway windows recompute to the
> capture date. `home_custom.png` uses a synthetic "Summit Data Systems" profile via the `?p=` deep
> link. **PII control: after ANY sample rebuild, visually re-verify every title-rendering shot
> (runway chart, explorer tables, detail headers) before it ships.**

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
