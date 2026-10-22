AUDIT (name assert_revenue_below_100);

SELECT * FROM @this_model WHERE revenue > 100
