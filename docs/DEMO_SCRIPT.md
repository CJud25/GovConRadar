# 5-minute demo script

Audience: a defense contractor's BD / capture lead. Goal: show that this tool tells the truth —
including when it doesn't know. Three "aha" beats.

## 0. Frame (30s)

> "This finds DoD cyber/IT contracts coming up for recompete, scores them for *your* company, and — the
> part that matters — it's built so every number survives your scrutiny. Facts are facts, estimates are
> labeled, and records it can't stand behind are quarantined, not padded into the pipeline."

Open the app on **Home**. Point at the badge: **SAMPLE DATA** on the deployed app (a committed
5,000-candidate seeded subsample — honestly labeled), or **PUBLIC DATA SNAPSHOT** with the as-of date
when running locally on the full snapshot (`py scripts/download_data.py`).

## Beat 1 — Score it for *your* company (90s)

1. Read the headline. On the shipped sample: **~820 active candidates (~$6.5B) across ~401 vehicles**,
   with **~4,044 stale records quarantined** (counts drift by a few rows day-to-day because runway
   windows recompute to today; the full 2026-07-15 snapshot: 5,712 active ≈ $49.4B,
   29,393 quarantined). Note the collapsed **"Needs verification"** strip — "these aren't in any of
   my numbers; they're here to audit, with a Verify-on-SAM.gov link each."
2. Go to **Your Company**, enter a real profile (NAICS 541512, a couple capabilities, a past-performance
   agency). Save.
3. Back on Home / Explorer: **every score and the tier board recompute live** against that profile;
   the demo baseline is retained for a before/after read.

> "Nothing was hardcoded — change the company, the board changes. And the shareable URL carries the
> profile, so your whole capture team sees the same board."

## Beat 2 — The price range that refuses to guess (90s)

1. Open any **Contract Detail** with a Competitive Price Range.
2. Show the Low / Market Median / High — "a range of what comparable work was actually **won** for, a
   fact from USAspending — **not** a price-to-win. Competitor bids are never public, so we don't pretend."
3. Open one with **insufficient comparables**: the panel **refuses** — "below the comparables floor it
   says *insufficient*, rather than inventing a number. The incumbent's run-rate is a separate reference
   line, never blended in."

> "A tool that invents a price-to-win is lying to you. This one shows its work and stops when the data
> runs out."

## Beat 3 — It admits its own limits (60s)

1. Open **Methodology**. Show the **graduated expired-record policy** (active / grace / stale) and the
   **90-day** rationale tied to the lookback-window coverage gap.
2. Show the **Data Gap** tier and the flags glossary. Then: "Remember that 2003-era record? Under the old
   scorer it sat in Tier 1 with a perfect data-quality score. Now it's Data Gap, its garbled title is
   cleaned, and it's out of every headline — reachable only under Needs-Verification."

> "This is the difference between a dashboard that looks impressive and one your capture reviews can
> actually defend. When it doesn't know, it tells you."

## Optional closer (30s)

Show the **vehicle rollup** in Explorer — up to 164 near-identical task orders collapse to one vehicle
row ("you pursue the vehicle, not 164 rows") — and the **curated export** ("clean CSV, no surrogate keys,
raw record never leaves the building"). Mention CI + `scripts/validate_data.py`: "the honesty is enforced
by tests, not just claimed."
