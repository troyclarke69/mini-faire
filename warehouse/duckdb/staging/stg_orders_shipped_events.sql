-- Enriches orders_shipped events (order_id + carrier) by joining back to raw.raw_orders
-- for retailer_id/product_id/quantity/gross_amount. See stg_order_paid_events.sql for
-- why this joins raw.raw_orders rather than staging.stg_orders.
create or replace table staging.stg_orders_shipped_events as
select
  e.event_id::varchar as event_id,
  e.event_type::varchar as event_type,
  e.event_ts::timestamptz as event_ts,
  e.order_id::varchar as order_id,
  o.retailer_id::varchar as retailer_id,
  o.product_id::varchar as product_id,
  o.quantity::integer as quantity,
  o.gross_amount::decimal(12, 2) as gross_amount,
  e.carrier::varchar as carrier
from raw.raw_orders_shipped_events e
left join raw.raw_orders o on e.order_id = o.order_id
qualify row_number() over (partition by e.event_id order by e.event_ts desc) = 1;
