-- ============================================================================
-- 01 · Recompete candidates expiring in the next 12 months, by NAICS
-- ----------------------------------------------------------------------------
-- BUSINESS QUESTION
--   Which NAICS industries carry the most DoD cyber/IT recompete pipeline that
--   comes up for award inside the next 12 months, and what is it worth?
--
-- TABLES USED
--   fact_recompete_candidates  (fact)   x   dim_naics  (dimension)
--
-- TECHNIQUES
--   CTE · fact<->dimension join · GROUP BY
--
-- FACTS vs ESTIMATES
--   FACT       total_obligated_amount is the reported FPDS obligation (public data).
--   ESTIMATE   the recompete/expiration window (days_until_expiration,
--              selected_expiration_date) is DERIVED from contract end dates — an
--              estimate of when the work re-competes, not a confirmed solicitation date.
--   Runway is recomputed to *today*; only candidate_status = 'active' rows are
--   counted here (expired-grace / expired-stale rows are held out of the forward
--   pipeline and surfaced only for verification).
-- ============================================================================
WITH expiring_soon AS (
    SELECT
        CAST(naics AS VARCHAR)      AS naics_code,
        candidate_id,
        total_obligated_amount
    FROM fact_recompete_candidates
    WHERE candidate_status = 'active'
      AND days_until_expiration BETWEEN 0 AND 365
)
SELECT
    e.naics_code,
    n.naics_description,
    COUNT(*)                                        AS candidates_next_12mo,
    ROUND(SUM(e.total_obligated_amount) / 1e6, 2)   AS pipeline_obligated_musd
FROM expiring_soon e
LEFT JOIN dim_naics n
       ON CAST(n.naics_code AS VARCHAR) = e.naics_code
GROUP BY e.naics_code, n.naics_description
ORDER BY candidates_next_12mo DESC, pipeline_obligated_musd DESC;
