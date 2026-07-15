-- ============================================================================
-- 04 · Competitive Price Range — historical comparable WON ranges by PSC
-- ----------------------------------------------------------------------------
-- BUSINESS QUESTION
--   For each Product/Service Code (PSC), what is the range of dollars that
--   COMPARABLE cyber/IT work has HISTORICALLY BEEN WON (obligated) for — a
--   defensible reference band, published only where enough comparables exist?
--
-- TABLES USED
--   fact_ptw_comparables  (fact)   x   dim_psc  (dimension)
--
-- TECHNIQUES
--   CTE · fact<->dimension join · GROUP BY · HAVING (minimum-sample-size guard)
--
-- FACTS vs ESTIMATES  — READ THIS
--   This is a **Competitive Price Range, NOT a price-to-win and NOT a bid
--   prediction.** Every dollar here is a FACT: an actual obligated (won) amount on
--   a historically comparable award drawn from FPDS. Competitor bid amounts are
--   never public, so this query never estimates a bid — it summarizes what similar
--   scopes have HISTORICALLY BEEN WON for. The HAVING clause REFUSES to publish a
--   range for any PSC with fewer than 8 comparables, rather than inventing a number
--   from thin data.
-- ============================================================================
WITH comps AS (
    SELECT
        CAST(comp_psc AS VARCHAR)   AS psc_code,
        comp_obligated
    FROM fact_ptw_comparables
    WHERE comp_obligated IS NOT NULL
      AND comp_obligated > 0
)
SELECT
    c.psc_code,
    p.psc_description,
    COUNT(*)                                                AS n_comparables,
    ROUND(MIN(c.comp_obligated) / 1e6, 3)                   AS won_min_musd,
    ROUND(quantile_cont(c.comp_obligated, 0.25) / 1e6, 3)   AS won_p25_musd,
    ROUND(quantile_cont(c.comp_obligated, 0.50) / 1e6, 3)   AS won_median_musd,
    ROUND(quantile_cont(c.comp_obligated, 0.75) / 1e6, 3)   AS won_p75_musd,
    ROUND(MAX(c.comp_obligated) / 1e6, 3)                   AS won_max_musd
FROM comps c
LEFT JOIN dim_psc p
       ON CAST(p.psc_code AS VARCHAR) = c.psc_code
GROUP BY c.psc_code, p.psc_description
HAVING COUNT(*) >= 8                       -- minimum-sample-size guard
ORDER BY n_comparables DESC;
