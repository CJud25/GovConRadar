# GovCon Recompete Radar — Scoring Methodology

## Pursuit Score (0-100)

A weighted composite of 8 component scores, each independently computed and
stored in `fact_scoring_breakdown.csv` for full transparency:

| Component | Weight | What it measures |
|---|---|---|
| Capability match | 25% | Overlap between the candidate's NAICS/PSC/title and the mock vendor's preferred NAICS/PSC/capabilities |
| Expiration urgency | 20% | How soon the selected expiration date arrives (0 days = 100, 24+ months = 10) |
| Estimated value | 15% | Fit against the vendor's comfortable contract-value range (sweet spot, not "bigger is always better") |
| Agency fit | 10% | Whether the **DoD component** (sub-agency: Army/Navy/DISA/…) matches the vendor's past-performance list. Keys on the component, not the constant top-tier "Department of Defense". |
| Set-aside/competition fit | 10% | Driven by the recovered FPDS `type_of_set_aside` code and `extent_competed_code` (competed = A/D/F). A set-aside is the strongest signal; full-and-open is a weaker positive. |
| Recompete confidence | 10% | Blend of expiration-date basis (potential > current > unknown) and classification confidence |
| Location fit | 5% | Whether place of performance is in the vendor's stated states served |
| Data quality | 5% | Penalizes each flagged data-quality issue on the underlying award record |

## Priority tiers

- **Tier 1: Pursue Now** — pursuit score ≥ 80
- **Tier 2: Capture Research** — 65–79
- **Tier 3: Monitor** — 50–64
- **Tier 4: Low Priority** — below 50

## Mock vendor profile — 100% synthetic

`VENDOR_PROFILE_SYNTHETIC` (defined in `config/vendor_profile_mock.yaml` and
loaded via `src/utils/config.py`) is a fictional small/mid-sized IT/cyber
contractor built for portfolio scoring demonstration only. It is not a real
company, and no field in it should be read as representing any actual
business. See `config/vendor_profile_mock.yaml` for the full profile.

## Incumbent vulnerability score

An ESTIMATE (0–100) built only from public signals already present in
`fact_recompete_candidates.csv`: the value-weighted share of an incumbent's
**forward book** that expires within the next 6 months.

- **Numerator** — the vendor's obligated value on rows with
  `0 ≤ days_until_expiration ≤ 180`.
- **Denominator** — the vendor's obligated value on rows with
  `days_until_expiration ≥ 0` (the forward, known-dated book). Expired history
  never enters the denominator, so loading more historical award data cannot
  move a vendor's score unless their forward book changes.
- **Unknown is unforgeable.** When the score cannot be computed honestly it is
  empty — never an imputed 0.0 or a neutral middle — and `vulnerability_basis`
  names the reason:
  - `insufficient_expiration_coverage` — less than 50% of the vendor's total
    obligated value carries a usable expiration date. Checked first: with most
    of a book undated, claiming "no forward book" would be an overclaim.
    Untriggerable on the current full snapshot (0 rows) — armor for sparser
    future data.
  - `no_forward_book` — the vendor's known-dated forward book is worth $0
    (includes vendors whose entire dated book is already expired).
  - `value_weighted_near_term_share` — the score is present.
- **Disclosure columns** — `pct_value_expired` and
  `pct_value_unknown_expiration` name the shares of the vendor's total book
  that the forward denominator excludes.

**Always read the score next to `number_of_cyber_it_awards` (n).** Any surface
showing the score must show n beside it: a 100.0 on a one-contract vendor
(their entire book is one expiring award) must not masquerade as a 100.0 on a
twelve-contract vendor. The distribution is expectedly **bimodal** — ~42% of
full-snapshot vendors (1,589 of 3,774) hold a single contract, so their score
is 0.0 or 100.0 by construction. That is the true shape of the portfolio, not
a defect.

This is never a factual prediction that an incumbent will lose a recompete.
