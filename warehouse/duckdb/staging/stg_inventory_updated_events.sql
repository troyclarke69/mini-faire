create or replace table staging.stg_inventory_updated_events as
select
  event_id::varchar as event_id,
  event_type::varchar as event_type,
  event_ts::timestamptz as event_ts,
  product_id::varchar as product_id,
  delta::integer as delta,
  inventory_count_after::integer as inventory_count_after
from raw.raw_inventory_updated_events
qualify row_number() over (partition by event_id order by event_ts desc) = 1;
