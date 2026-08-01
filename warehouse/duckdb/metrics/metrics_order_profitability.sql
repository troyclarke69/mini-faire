create or replace view marts.metrics_order_profitability as
select
  order_id,
  retailer_id,
  product_id,
  order_date,
  quantity,
  gross_amount,
  discount_amount,
  net_amount,
  estimated_cost,
  estimated_profit,
  case
    when net_amount = 0 then null
    else estimated_profit::double / net_amount
  end as estimated_margin
from marts.fact_orders;

