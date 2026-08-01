create or replace table staging.stg_products as
select
  product_id::varchar as product_id,
  brand_id::varchar as brand_id,
  name::varchar as product_name,
  category::varchar as product_category,
  unit_price::decimal(12, 2) as unit_price,
  unit_cost::decimal(12, 2) as unit_cost,
  inventory_count::integer as inventory_count,
  is_active::boolean as is_active
from raw.raw_products
qualify row_number() over (partition by product_id order by product_id) = 1;

