MODEL (
  name orders.fct_daily_revenue,
  kind FULL,
  grain order_date,
  audits (
    not_null(columns := (order_date, order_count, revenue)),
    unique_values(columns := (order_date))
  )
);

SELECT
  ordered_at_utc::DATE AS order_date,
  count(*)::BIGINT AS order_count,
  sum(amount)::DECIMAL(18, 2) AS revenue
FROM orders.stg_orders
GROUP BY ordered_at_utc::DATE
