MODEL (
  name orders.stg_orders,
  kind FULL,
  grain order_id,
  audits (
    not_null(columns := (order_id, customer_id, ordered_at_utc)),
    unique_values(columns := (order_id)),
    assert_positive_amount
  )
);

SELECT
  order_id::INTEGER AS order_id,
  customer_id::INTEGER AS customer_id,
  amount::DECIMAL(12, 2) AS amount,
  timezone('UTC', ordered_at) AS ordered_at_utc
FROM raw.orders
QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY ordered_at DESC) = 1
