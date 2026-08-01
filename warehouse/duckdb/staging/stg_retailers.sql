create or replace table staging.stg_retailers as
select
  retailer_id::varchar as retailer_id,
  name::varchar as retailer_name,
  country::varchar as country,
  category::varchar as retailer_category,
  signup_date::date as signup_date,
  status::varchar as status
from raw.raw_retailers
qualify row_number() over (partition by retailer_id order by signup_date desc) = 1;

