create or replace view marts.metrics_retailer_daily as
select
  f.order_date,
  r.retailer_id,
  r.retailer_name,
  count(distinct f.order_id) as order_count,
  sum(f.quantity) as units_sold,
  sum(f.gross_amount) as gmv,
  sum(f.net_amount) as net_revenue,
  sum(f.estimated_profit) as estimated_profit,
  avg(f.net_amount) as average_order_value
from marts.fact_orders f
left join marts.dim_retailer r on f.retailer_key = r.retailer_key
group by
  f.order_date,
  r.retailer_id,
  r.retailer_name;
