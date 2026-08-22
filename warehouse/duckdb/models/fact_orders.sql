create table if not exists marts.fact_orders (
  order_key ubigint,
  retailer_key ubigint,
  product_key ubigint,
  order_id varchar,
  retailer_id varchar,
  product_id varchar,
  order_ts timestamptz,
  order_date date,
  quantity integer,
  gross_amount decimal(12, 2),
  discount_amount decimal(12, 2),
  net_amount decimal(12, 2),
  estimated_cost decimal(12, 2),
  estimated_profit decimal(12, 2),
  status varchar,
  loaded_at timestamptz
);

create or replace temporary table delta_fact_orders as
select
  hash(o.order_id) as order_key,
  r.retailer_key,
  p.product_key,
  o.order_id,
  o.retailer_id,
  o.product_id,
  o.order_ts,
  cast(o.order_ts as date) as order_date,
  o.quantity,
  o.gross_amount,
  o.discount_amount,
  o.gross_amount - o.discount_amount as net_amount,
  (o.quantity * p.unit_cost)::decimal(12, 2) as estimated_cost,
  (o.gross_amount - o.discount_amount - (o.quantity * p.unit_cost))::decimal(12, 2) as estimated_profit,
  o.status,
  current_timestamp as loaded_at
from staging.stg_orders o
left join marts.dim_retailer r on o.retailer_id = r.retailer_id
left join marts.dim_product p on o.product_id = p.product_id
where o.order_ts >= coalesce(
  (
    select max(high_watermark)
    from elt_model_runs
    where model_name = 'fact_orders'
      and status = 'success'
  ),
  timestamp '1900-01-01'
)
-- The watermark filter above is a reprocessing-volume optimization, not a
-- correctness gate: staging is fully rebuilt from raw every run anyway (see
-- README's Incremental ELT section), so nothing is saved by dropping a row
-- outright. Without this OR clause, an order whose order_ts falls *before*
-- the current watermark - e.g. a Mongo pull or synthetic backfill landing
-- after a later-dated batch/event run already advanced the watermark past
-- it - would never make it into delta_fact_orders and would silently never
-- appear in marts.fact_orders, even though it passed validation. Any
-- business key not yet present in the target table is always included,
-- regardless of how "late" it arrived relative to prior runs.
or not exists (select 1 from marts.fact_orders f where f.order_id = o.order_id);

delete from marts.fact_orders
where order_id in (select order_id from delta_fact_orders);

insert into marts.fact_orders
select * from delta_fact_orders;

insert into elt_model_runs
select
  'fact_orders' as model_name,
  'marts.fact_orders' as target_table,
  'incremental_watermark_delete_insert' as load_strategy,
  'order_id' as business_key,
  (select count(*) from staging.stg_orders) as source_row_count,
  (select count(distinct order_id) from delta_fact_orders) as affected_key_count,
  (select count(*) from marts.fact_orders) as target_row_count,
  max(order_ts) as high_watermark,
  coalesce(min(loaded_at), current_timestamp) as started_at,
  coalesce(max(loaded_at), current_timestamp) as completed_at,
  'success' as status
from delta_fact_orders;
