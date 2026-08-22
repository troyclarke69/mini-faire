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
  carrier varchar,
  loaded_at timestamptz
);

-- Migration for warehouses built before order_paid/orders_shipped were unioned in.
alter table marts.fact_orders_events add column if not exists carrier varchar;

-- Order lifecycle events (order_created, order_paid, orders_shipped) share one grain:
-- one row per event, optionally enriched with retailer/product/amount context.
create or replace temporary table staged_orders_events as
select event_id, event_type, event_ts, order_id, retailer_id, product_id, quantity, gross_amount, null::varchar as carrier
from staging.stg_order_created_events
union all
select event_id, event_type, event_ts, order_id, retailer_id, product_id, quantity, gross_amount, null::varchar as carrier
from staging.stg_order_paid_events
union all
select event_id, event_type, event_ts, order_id, retailer_id, product_id, quantity, gross_amount, carrier
from staging.stg_orders_shipped_events;

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
  e.carrier,
  current_timestamp as loaded_at
from staged_orders_events e
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
)
-- See fact_orders.sql for why this OR clause exists: without it, a
-- late-arriving event (e.g. from a Mongo pull run after a later-dated
-- batch/event run already advanced the watermark) would be silently
-- dropped and never reach marts.fact_orders_events.
or not exists (select 1 from marts.fact_orders_events f where f.event_id = e.event_id);

delete from marts.fact_orders_events
where event_id in (select event_id from delta_fact_orders_events);

-- Explicit column list (not `select *`): warehouses built before `carrier`
-- existed got it appended by the ALTER above, which puts it *after*
-- loaded_at physically, while delta_fact_orders_events selects it *before*
-- loaded_at. A positional `insert ... select *` would then shove a carrier
-- string like "DHL" into the loaded_at (timestamptz) slot and blow up with
-- `invalid timestamp field format: "DHL"`. Naming both sides makes the
-- insert safe regardless of the target table's physical column order.
insert into marts.fact_orders_events (
  event_key, retailer_key, product_key, event_id, event_type, event_ts,
  event_date, order_id, retailer_id, product_id, quantity, gross_amount,
  carrier, loaded_at
)
select
  event_key, retailer_key, product_key, event_id, event_type, event_ts,
  event_date, order_id, retailer_id, product_id, quantity, gross_amount,
  carrier, loaded_at
from delta_fact_orders_events;

insert into elt_model_runs
select
  'fact_orders_events' as model_name,
  'marts.fact_orders_events' as target_table,
  'incremental_watermark_delete_insert' as load_strategy,
  'event_id' as business_key,
  (select count(*) from staged_orders_events) as source_row_count,
  (select count(distinct event_id) from delta_fact_orders_events) as affected_key_count,
  (select count(*) from marts.fact_orders_events) as target_row_count,
  max(event_ts) as high_watermark,
  coalesce(min(loaded_at), current_timestamp) as started_at,
  coalesce(max(loaded_at), current_timestamp) as completed_at,
  'success' as status
from delta_fact_orders_events;
