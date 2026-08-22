create or replace table staging.stg_price_changed_events as
select
  event_id::varchar as event_id,
  event_type::varchar as event_type,
  event_ts::timestamptz as event_ts,
  product_id::varchar as product_id,
  old_price::decimal(12, 2) as old_price,
  new_price::decimal(12, 2) as new_price
from raw.raw_price_changed_events
qualify row_number() over (partition by event_id order by event_ts desc) = 1;
