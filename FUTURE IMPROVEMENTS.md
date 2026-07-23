# GovCon Recompete Radar — Current Capabilities & Future Improvements

*This document closes out the case-study phase of the project. Part 1 describes what the application does today and why it is built the way it is. Part 2 lays out where it goes next — the analytical roadmap, the platform evolution, and where it sits in the competitive landscape. In keeping with the product's core principle, this document does not oversell: facts are stated as facts, plans are stated as plans.*

---

## Part 1 — Where the Application Stands Today

GovCon Recompete Radar is a Streamlit analytics application built entirely on public federal data — USAspending.gov contract awards and SAM.gov opportunity notices. It identifies expiring Department of Defense cybersecurity and IT contracts, estimates their recompete windows, and scores each one for pursuit fit against a user-supplied company profile. As of the 2026-07-05 snapshot this document was written against, the app surfaced roughly 3,100 active recompete candidates representing about $38B in obligated value across more than 1,000 contract vehicles, with a further ~440 recently-expired records held in a flagged grace state and ~1,100 stale records quarantined out of every headline number until they can be verified. (The 2026-07-07 FY2019–2026 snapshot expanded that to 5,764 active ≈ $50.0B across 1,502 vehicles, with 29,343 quarantined — see the README for current numbers.)

### The feature set

**Monday Briefing (Home).** A single-screen orientation: honest headline KPIs computed from the fact tables (never hardcoded), the near-term expiration picture, and Timing / Where / Who breakdowns of the active pipeline.

**Pipeline Explorer.** A search-first workbench with global search, shareable filter chips, a ranked candidate table with runway columns, a contract-vehicle rollup that collapses task orders under their parent IDV into a single row, and a capture calendar view. Rows deep-link directly into Contract Detail.

**Contract Detail.** A per-candidate capture profile: the full transparent scoring breakdown (every component, weight, and raw score visible), linked SAM.gov notices where they exist, the incumbent's other expiring contracts, an interactive Competitive Price Range panel, and a downloadable one-page Capture Brief.

**Competitive Price Range.** A statistically disciplined alternative to black-box "price-to-win" numbers. The pipeline selects historical comparables, then the app recomputes the range live — winsorized percentiles with a bootstrapped confidence interval and an explicit data-strength grade — as the user excludes bad-fit comparables, projects across an expected contract term, or applies a disclosed competition scenario. Below a minimum comparables floor, the app **refuses to emit a range** rather than inventing one.

**Score-as-Your-Company.** Enter a company profile once — capabilities, NAICS/PSC, past-performance DoD components, comfortable contract value, geography — and every pursuit score and the tier board recompute live. The app is explicit that roughly 55% of the score is profile-driven and 45% is intrinsic to the contract, and it preserves the labeled demo baseline for before/after comparison.

**Incumbent Landscape & Market Map.** Incumbent concentration analysis, per-vendor expiring exposure, and a market map spanning geography, DoD components, component-by-PSC mix, and fiscal-year obligation trends — all computed on the reportable set so quarantined records never inflate a vendor's footprint.

**Data quality as a first-class surface.** A graduated expired-record policy (active / grace / stale), quality flags for garbled and code-prefix titles, a "needs verification" strip on analytical views, and a Data Gap tier that quarantines unreliable records out of the pursuit pipeline entirely.

### What makes it different

Three design decisions distinguish this application from typical BD dashboards, at any price point.

**It is auditable by construction.** The shipped `scripts/validate_data.py` re-derives every headline KPI from the raw fact tables and proves that the app's live scoring engine reproduces the baked pipeline scores exactly (maximum difference: 0.0). CI runs this gate on every push. The integrity contract is code, not marketing copy.

**It refuses to guess.** Records the data cannot stand behind are quarantined, not dressed up as leads. Price estimates below the comparables floor are declined, not fabricated. Facts (sourced from federal APIs, with provenance preserved) and estimates (analytical, always labeled) never blur.

**It corrected itself in public.** Scorer v1 gave every expired contract maximum urgency — inflating the headline to 4,695 candidates and $64.6B and producing a meaningless perfect data-quality average. Version 2.0.0 fixed the logic, cut Tier 1 from 118 candidates to 26, and documented the whole correction in the changelog. The product's brand is honesty, and the changelog is the receipt.

The application shares a Power BI-style star schema with a companion Power BI report (private build repo), ships a synthetic sample dataset so it runs anywhere with no pipeline or API access, and recomputes every contract's runway against *today* on load so an aging deployment never presents a lapsed contract as active.

---

## Part 2 — Future Improvements

> **The gate that governs every prediction idea below (reconciled with the project's
> "Deliberately Not Built" ledger — `docs/ROADMAP.md` in the source repo):** a backtested
> recompete-outcome model ships only behind a pinned gate — no published probability before
> (a) a labeled-record threshold and (b) a published out-of-sample backtest with its n and
> interval. Until then the honest output is deterministic signals + coverage-gated Unknown.
> The working model for this discipline already ships: the Competitive Price Range's
> outcome-coverage harness (`ptw_backtest`) is built and tested but dormant until the data
> can support it.

The current application analyzes the past with unusual rigor. The roadmap's purpose is to point that same rigor forward — toward prediction, early warning, and capture decision support — without ever compromising the fact/estimate discipline that defines the product.

### Tier 1 — Forward-looking signals (near term)

**Agency acquisition forecast integration.** Federal agencies publish procurement forecasts — OSDBU forecasts, component long-range acquisition estimates, and similar planning documents — months to years ahead of solicitation. Aggregating and normalizing these sources, then linking forecast entries to existing expiring-award records, converts the Radar from a retrospective tool into a forward-looking one using entirely public data. The unglamorous parsing and entity-matching work involved is precisely where this pipeline's architecture is strongest.

**Sources Sought and RFI monitoring.** Formal solicitations post late in the acquisition cycle; Sources Sought notices and RFIs post six to eighteen months earlier and are the practical trigger for capture activity. Extending the existing award-to-notice fuzzy-matching pipeline to these earlier notice types directly attacks the current low notice-linkage rate and adds a genuine early-warning lane.

**A backtested recompete-outcome model.** The historical award data already contains the labels: for every contract that expired in prior fiscal years, what actually happened — recompeted, extended, bridged, consolidated, or ended. Training an outcome model on that history and **publishing the backtest results** produces a validated predictive claim rather than a rule-of-thumb estimate. Consistent with the calibration gate already defined for this project, no model ships before a sufficient labeled-record threshold is met, and every published prediction carries its measured historical accuracy.

### Tier 2 — Deep signals (medium term)

**Budget-to-contract linkage.** DoD budget justification documents (the P-1, R-1, and O-1 exhibits) are public. Linking program-element funding trends to recompete candidates surfaces signals no rule-based scorer can see — a recompete whose parent program lost funding in the latest budget request is a very different pursuit than one whose program is growing. Scoped to the DoD cyber/IT vertical, this is ambitious but tractable, and it is the roadmap's most defensible item.

**Incumbent vulnerability scoring.** The ingredients are already in the schema: option-year exhaustion, ceiling burn rate, modification and bridge-extension patterns, competition posture, and offers received, augmented with public protest history. This converts "who holds the expiring work" into "how firmly they hold it" — the question capture managers actually ask.

### Tier 3 — Ecosystem intelligence (longer term)

**A teaming graph from subaward data.** Public subaward records reveal real prime/sub relationships by agency and NAICS. Mapping them gives users a data-derived view of who teams with whom — the seed of a genuine teaming-intelligence capability built from verifiable public records.

**Government-published contacts.** Incorporating the point-of-contact information that agencies themselves publish on notices, while continuing to exclude any scraped or non-public personal data, closes the "who do I call" gap within the product's existing privacy posture.

### Platform evolution — from case study to SaaS

The architecture was built for this transition. Nearly everything above is pipeline work: new signals land as new columns and fact tables in the star schema, the rebake and validation gates extend to cover them, and the Streamlit layer simply renders more baked, verified data. The staged platform path is:

1. **Alerting bridge.** A scheduled job diffs each new snapshot against the last and emails a digest — new forecast matches, new Sources Sought links, candidates entering their recompete window. This delivers real early-warning value on existing infrastructure.
2. **Persistence layer.** Authentication, saved company profiles, and per-user alert preferences via a managed backend, with payments handled by a standard billing provider — triggered by validated demand, not built on speculation.
3. **Scale headroom.** Parquet-backed storage and in-process analytical querying provide roughly an order of magnitude of data growth before any front-end migration is warranted.

The validation gate is deliberate and unsentimental: a fixed design-partner target within a fixed window, at a real price, with a documented kill decision if the market doesn't answer. That discipline is itself part of the case study.

### Positioning — where this fits in the market

The government market-intelligence landscape is served well at the top. Analyst-driven platforms maintain large research teams that speak directly with agencies and track opportunities years before solicitation; policy-intelligence platforms connect legislative and budget developments to procurement at a breadth no independent tool can match. Those are genuinely valuable products, and this project does not pretend to replicate human analyst networks or all-market coverage.

What this project offers is a **different validation model.** Enterprise platforms ask users to trust analyst judgment; this application ships the code that proves its numbers. Every KPI re-derives from source facts. Every score decomposes into visible, weighted components. Every price range discloses its comparables, its confidence interval, and its data strength — and declines to answer when the evidence is thin. As the roadmap adds prediction, the same standard applies: forecasts ship with published backtests, so users can verify accuracy themselves rather than take it on faith.

Three durable advantages follow. **Auditability** — in a market where the defensibility of numbers is a recurring, documented pain point for contractors, a tool whose every figure can survive scrutiny is not a nice-to-have; it is the product. **Depth over breadth** — by committing to the DoD cyber/IT vertical, the Radar can pursue signal sources (budget exhibits, vertical-specific vulnerability patterns) that horizontal platforms cannot economically build for every market they cover. **Accessibility** — enterprise intelligence platforms price at levels that exclude exactly the small and mid-size contractors who most need an edge; a rigorous tool at a fraction of that cost expands who gets to compete well, rather than reshuffling advantages among those who already can.

The one-line positioning, today and on the roadmap: **enterprise platforms validate with analysts you can't audit; the Radar validates with code and backtests you can run yourself.**

---

*GovCon Recompete Radar is built on public-domain federal data. All pursuit scores, tiers, recompete windows, predictions, and price ranges are analytical estimates, clearly labeled as such, and never official government predictions.*
