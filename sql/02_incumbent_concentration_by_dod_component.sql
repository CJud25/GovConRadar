-- ============================================================================
-- 02 · Incumbent concentration by DoD Component (top-incumbent share)  [window]
-- ----------------------------------------------------------------------------
-- BUSINESS QUESTION
--   Within each DoD Component, how concentrated is the active recompete pipeline
--   in its single largest incumbent? A high top-incumbent share flags a component
--   whose pipeline hinges on one soft/vulnerable incumbent.
--
-- TABLES USED
--   fact_recompete_candidates  (fact)
--   NOTE: 'agency' is the constant 'DEPARTMENT OF DEFENSE'; the real discriminator
--   is SUBAGENCY, which this product labels the DoD Component. All agency-level
--   analysis groups by subagency (dod_component).
--
-- TECHNIQUES
--   CTE · RANK() window · SUM() OVER window · GROUP BY
--
-- FACTS vs ESTIMATES
--   FACT       obligation dollars (total_obligated_amount) are FPDS-reported.
--   ESTIMATE   membership in the "recompete candidate" set and each contract's
--              recompete timing are model-derived estimates, not confirmed recompetes.
--   The share is a factual computation over that estimated candidate set.
-- ============================================================================
WITH vendor_component AS (
    SELECT
        subagency                    AS dod_component,
        incumbent_vendor,
        COUNT(*)                     AS candidate_count,
        SUM(total_obligated_amount)  AS vendor_value
    FROM fact_recompete_candidates
    WHERE candidate_status = 'active'
      AND incumbent_vendor IS NOT NULL
    GROUP BY subagency, incumbent_vendor
),
ranked AS (
    SELECT
        *,
        RANK() OVER (PARTITION BY dod_component ORDER BY vendor_value DESC) AS vendor_rank,
        SUM(vendor_value) OVER (PARTITION BY dod_component)                 AS component_value
    FROM vendor_component
)
SELECT
    dod_component,
    COUNT(*)                                                       AS distinct_incumbents,
    ROUND(MAX(component_value) / 1e6, 2)                           AS component_pipeline_musd,
    MAX(CASE WHEN vendor_rank = 1 THEN incumbent_vendor END)       AS top_incumbent,
    ROUND(100.0 * MAX(CASE WHEN vendor_rank = 1 THEN vendor_value END)
                / MAX(component_value), 1)                         AS top_incumbent_share_pct
FROM ranked
GROUP BY dod_component
ORDER BY component_pipeline_musd DESC;
