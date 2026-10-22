AUDIT (name assert_no_tied_versions);

SELECT order_id, ordered_at
FROM raw.orders
GROUP BY order_id, ordered_at
HAVING count(DISTINCT (customer_id, amount)) > 1
