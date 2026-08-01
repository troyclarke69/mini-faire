create or replace view marts.metrics_product_velocity as
select
  p.product_id,
  p.product_name,
  p.product_category,
  count(distinct f.order_id) as order_count,
  coalesce(sum(f.quantity), 0) as units_sold,
  p.inventory_count,
  case
    when p.inventory_count = 0 then null
    else coalesce(sum(f.quantity), 0)::double / p.inventory_count
  end as inventory_velocity
from marts.dim_product p
left join marts.fact_orders f on p.product_key = f.product_key
group by
  p.product_id,
  p.product_name,
  p.product_category,
  p.inventory_count;
