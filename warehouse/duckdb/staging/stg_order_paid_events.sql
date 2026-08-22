-- Enriches order_paid events (which only carry order_id + amount) by joining back
-- to raw.raw_orders for retailer_id/product_id/quantity. Joins against the raw zone
-- (not staging.stg_orders) so this file has no ordering dependency on other staging
-- SQL files within the same alphabetically-sorted ELT pass.
create or replace table staging.stg_order_paid_events as
select
  e.event_id::varchar as event_id,
  e.event_type::varchar as event_type,
  e.event_ts::timestamptz as event_ts,
  e.order_id::varchar as order_id,
  o.retailer_id::varchar as retailer_id,
  o.product_id::varchar as product_id,
  o.quantity::integer as quantity,
  e.amount::decimal(12, 2) as gross_amount
from raw.raw_order_paid_events e
left join raw.raw_orders o on e.order_id = o.order_id
qualify row_number() over (partition by e.event_id order by e.event_ts desc) = 1;
