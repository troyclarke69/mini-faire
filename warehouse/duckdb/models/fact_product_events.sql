create table if not exists marts.fact_product_events (
  event_key ubigint,
  product_key ubigint,
  event_id varchar,
  event_type varchar,
  event_ts timestamptz,
  event_date date,
  product_id varchar,
  delta integer,
  inventory_count_after integer,
  old_price decimal(12, 2),
  new_price decimal(12, 2),
  loaded_at timestamptz
);

-- Product-anchored events (inventory_updated, price_changed) have no order/retailer
-- context, so they live in their own fact table rather than fact_orders_events.
create or replace temporary table staged_product_events as
select
  event_id, event_type, event_ts, product_id, delta, inventory_count_after,
  null::decimal(12, 2) as old_price, null::decimal(12, 2) as new_price
from staging.stg_inventory_updated_events
union all
select
  event_id, event_type, event_ts, product_id, null::integer as delta, null::integer as inventory_count_after,
  old_price, new_price
from staging.stg_price_changed_events;

create or replace temporary table delta_fact_product_events as
select
  hash(e.event_id) as event_key,
  p.product_key,
  e.event_id,
  e.event_type,
  e.event_ts,
  cast(e.event_ts as date) as event_date,
  e.product_id,
  e.delta,
  e.inventory_count_after,
  e.old_price,
  e.new_price,
  current_timestamp as loaded_at
from staged_product_events e
left join marts.dim_product p on e.product_id = p.product_id
where e.event_ts >= coalesce(
  (
    select max(high_watermark)
    from elt_model_runs
    where model_name = 'fact_product_events'
      and status = 'success'
  ),
  timestamp '1900-01-01'
)
-- See fact_orders.sql for why this OR clause exists: without it, a
-- late-arriving event (e.g. from a Mongo pull run after a later-dated
-- batch/event run already advanced the watermark) would be silently
-- dropped and never reach marts.fact_product_events.
or not exists (select 1 from marts.fact_product_events f where f.event_id = e.event_id);

delete from marts.fact_product_events
where event_id in (select event_id from delta_fact_product_events);

-- Explicit column list rather than `select *` - see fact_orders_events.sql
-- for why: it keeps this insert safe by name even if a future migration
-- appends a column to marts.fact_product_events out of declared order.
insert into marts.fact_product_events (
  event_key, product_key, event_id, event_type, event_ts, event_date,
  product_id, delta, inventory_count_after, old_price, new_price, loaded_at
)
select
  event_key, product_key, event_id, event_type, event_ts, event_date,
  product_id, delta, inventory_count_after, old_price, new_price, loaded_at
from delta_fact_product_events;

insert into elt_model_runs
select
  'fact_product_events' as model_name,
  'marts.fact_product_events' as target_table,
  'incremental_watermark_delete_insert' as load_strategy,
  'event_id' as business_key,
  (select count(*) from staged_product_events) as source_row_count,
  (select count(distinct event_id) from delta_fact_product_events) as affected_key_count,
  (select count(*) from marts.fact_product_events) as target_row_count,
  max(event_ts) as high_watermark,
  coalesce(min(loaded_at), current_timestamp) as started_at,
  coalesce(max(loaded_at), current_timestamp) as completed_at,
  'success' as status
from delta_fact_product_events;
