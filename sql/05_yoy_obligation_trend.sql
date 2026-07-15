-- ============================================================================
-- 05 · Year-over-year obligation trend (award signings)  [window]
-- ----------------------------------------------------------------------------
-- BUSINESS QUESTION
--   How have total DoD cyber/IT obligated dollars moved year over year across the
--   contract awards captured in this snapshot?
--
-- TABLES USED
--   fact_contract_awards  (fact)
--
-- TECHNIQUES
--   CTE · LAG() window · GROUP BY
--
-- FACTS vs ESTIMATES
--   FACT   date_signed and total_obligated_amount are FPDS-reported.
--   NOTE   this is SNAPSHOT COVERAGE, not a market total: it trends only the
--          cyber/IT-relevant awards captured in this data set, so the earliest and
--          most-recent years can be partial. It is a descriptive historical trend,
--          NOT a forecast of future obligations.
-- ============================================================================
WITH yearly AS (
    SELECT
        EXTRACT(YEAR FROM CAST(date_signed AS DATE))    AS award_year,
        COUNT(*)                                        AS awards,
        SUM(total_obligated_amount)                     AS obligated
    FROM fact_contract_awards
    WHERE date_signed IS NOT NULL
      AND total_obligated_amount IS NOT NULL
    GROUP BY EXTRACT(YEAR FROM CAST(date_signed AS DATE))
)
SELECT
    award_year,
    awards,
    ROUND(obligated / 1e6, 2)                                        AS obligated_musd,
    ROUND(LAG(obligated) OVER (ORDER BY award_year) / 1e6, 2)        AS prior_year_musd,
    ROUND((obligated - LAG(obligated) OVER (ORDER BY award_year)) / 1e6, 2)
                                                                     AS yoy_change_musd,
    ROUND(100.0 * (obligated - LAG(obligated) OVER (ORDER BY award_year))
                / NULLIF(LAG(obligated) OVER (ORDER BY award_year), 0), 1)
                                                                     AS yoy_change_pct
FROM yearly
ORDER BY award_year;
