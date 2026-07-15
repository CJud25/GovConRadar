-- ============================================================================
-- 03 · Pursuit-score distribution by priority tier
-- ----------------------------------------------------------------------------
-- BUSINESS QUESTION
--   How does the model's pursuit score distribute across the five priority tiers,
--   and how many candidates sit in each tier?
--
-- TABLES USED
--   fact_recompete_candidates  (fact)   x   dim_priority_tier  (dimension)
--
-- TECHNIQUES
--   CTE · fact<->dimension join · GROUP BY · quantile aggregate
--
-- FACTS vs ESTIMATES
--   ESTIMATE   pursuit_score and priority_tier are MODEL OUTPUTS (scorer v2) — an
--              opinion of fit/urgency, NOT a probability of win and NOT advice.
--   Tiers are recomputed on every load. 'Data Gap' is a QUARANTINE tier for
--   records the data cannot stand behind; it is deliberately not a low score but a
--   held-back one, and is excluded from headline "pursue" counts elsewhere.
-- ============================================================================
WITH scored AS (
    SELECT priority_tier, pursuit_score
    FROM fact_recompete_candidates
    WHERE pursuit_score IS NOT NULL
)
SELECT
    t.tier_sort_order,
    s.priority_tier,
    COUNT(*)                                          AS n_candidates,
    ROUND(MIN(s.pursuit_score), 1)                    AS min_score,
    ROUND(AVG(s.pursuit_score), 1)                    AS avg_score,
    ROUND(quantile_cont(s.pursuit_score, 0.5), 1)     AS median_score,
    ROUND(MAX(s.pursuit_score), 1)                    AS max_score
FROM scored s
JOIN dim_priority_tier t
  ON t.priority_tier = s.priority_tier
GROUP BY t.tier_sort_order, s.priority_tier
ORDER BY t.tier_sort_order;
