-- ============================================================================
-- 06 · Expiring-vs-active pipeline mix by fiscal quarter  [window]
-- ----------------------------------------------------------------------------
-- BUSINESS QUESTION
--   Bucketed by the FISCAL QUARTER each contract expires in, how does the
--   recompete pipeline split between still-active (forward) candidates and
--   already-expired records held back for verification — and what is each worth?
--
-- TABLES USED
--   fact_recompete_candidates  (fact)   x   dim_date  (dimension)
--
-- TECHNIQUES
--   CTE · fact<->dimension join · GROUP BY · running-total window
--
-- FACTS vs ESTIMATES
--   FACT       obligation dollars (total_obligated_amount) are FPDS-reported.
--   ESTIMATE   the expiration date — and therefore the fiscal-quarter bucket — is
--              DERIVED from contract end dates; 'active' vs 'expired' is recomputed
--              to today on every load.
--   Expired rows are surfaced ONLY for verification and are excluded from the
--   forward headline pipeline totals shown elsewhere in the product.
-- ============================================================================
WITH candidate_dated AS (
    SELECT
        d.fiscal_year,
        d.fiscal_quarter,
        d.fiscal_period_label,
        c.candidate_status,
        c.total_obligated_amount
    FROM fact_recompete_candidates c
    JOIN dim_date d
      ON d.date_key = c.expiration_date_key
),
by_quarter AS (
    SELECT
        fiscal_year,
        fiscal_quarter,
        fiscal_period_label,
        COUNT(*)                                                        AS candidates,
        SUM(CASE WHEN candidate_status = 'active' THEN 1 ELSE 0 END)    AS active_count,
        SUM(CASE WHEN candidate_status <> 'active' THEN 1 ELSE 0 END)   AS expired_verify_count,
        ROUND(SUM(total_obligated_amount) / 1e6, 2)                     AS pipeline_musd
    FROM candidate_dated
    GROUP BY fiscal_year, fiscal_quarter, fiscal_period_label
)
SELECT
    fiscal_period_label,
    candidates,
    active_count,
    expired_verify_count,
    pipeline_musd,
    ROUND(SUM(pipeline_musd) OVER (ORDER BY fiscal_year, fiscal_quarter), 2)
                                                                        AS cumulative_pipeline_musd
FROM by_quarter
ORDER BY fiscal_year, fiscal_quarter;
