create table if not exists marts.fact_orders_events (
  event_key ubigint,
  retailer_key ubigint,
  product_key ubigint,
  event_id varchar,
  event_type varchar,
  event_ts timestamptz,
  event_date date,
  order_id varchar,
  retailer_id varchar,
  product_id varchar,
  quantity integer,
  gross_amount decimal(12, 2),
  loaded_at timestamptz
);

create or replace temporary table delta_fact_orders_events as
select
  hash(e.event_id) as event_key,
  r.retailer_key,
  p.product_key,
  e.event_id,
  e.event_type,
  e.event_ts,
  cast(e.event_ts as date) as event_date,
  e.order_id,
  e.retailer_id,
  e.product_id,
  e.quantity,
  e.gross_amount,
  current_timestamp as loaded_at
from staging.stg_order_created_events e
left join marts.dim_retailer r on e.retailer_id = r.retailer_id
left join marts.dim_product p on e.product_id = p.product_id
where e.event_ts >= coalesce(
  (
    select max(high_watermark)
    from elt_model_runs
    where model_name = 'fact_orders_events'
      and status = 'success'
  ),
  timestamp '1900-01-01'
);

delete from marts.fact_orders_events
where event_id in (select event_id from delta_fact_orders_events);

insert into marts.fact_orders_events
select * from delta_fact_orders_events;

insert into elt_model_runs
select
  'fact_orders_events' as model_name,
  'marts.fact_orders_events' as target_table,
  'incremental_watermark_delete_insert' as load_strategy,
  'event_id' as business_key,
  (select count(*) from staging.stg_order_created_events) as source_row_count,
  (select count(distinct event_id) from delta_fact_orders_events) as affected_key_count,
  (select count(*) from marts.fact_orders_events) as target_row_count,
  max(event_ts) as high_watermark,
  coalesce(min(loaded_at), current_timestamp) as started_at,
  coalesce(max(loaded_at), current_timestamp) as completed_at,
  'success' as status
from delta_fact_orders_events;
