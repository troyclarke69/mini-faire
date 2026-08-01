create or replace table staging.stg_order_created_events as
select
  event_id::varchar as event_id,
  event_type::varchar as event_type,
  event_ts::timestamptz as event_ts,
  order_id::varchar as order_id,
  retailer_id::varchar as retailer_id,
  product_id::varchar as product_id,
  quantity::integer as quantity,
  gross_amount::decimal(12, 2) as gross_amount
from raw.raw_order_created_events
qualify row_number() over (partition by event_id order by event_ts desc) = 1;

