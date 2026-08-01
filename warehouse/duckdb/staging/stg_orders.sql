create or replace table staging.stg_orders as
select
  order_id::varchar as order_id,
  retailer_id::varchar as retailer_id,
  product_id::varchar as product_id,
  order_ts::timestamptz as order_ts,
  quantity::integer as quantity,
  gross_amount::decimal(12, 2) as gross_amount,
  discount_amount::decimal(12, 2) as discount_amount,
  status::varchar as status
from raw.raw_orders
qualify row_number() over (partition by order_id order by order_ts desc) = 1;

