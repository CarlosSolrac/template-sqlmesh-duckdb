AUDIT (name assert_positive_amount);

SELECT *
FROM @this_model
WHERE amount <= 0
