-- Tenant-aware ELT (PHASE7-DEPLOYMENT.md Section 2).
--
-- Runs after ingestion/tenant_ingest.py's refresh_tenant_raw_tables() has
-- (re)built raw.raw_tenant_orders from every tenant's ingested order files
-- (each row already tagged with tenant_id - see that module's docstring for
-- why "orders" is the one entity carried end-to-end through this file,
-- compute/polars/tenant_metrics.py, and ml/tenant_models/, while ingestion
-- itself stays generic across all entity types).
--
-- Mirrors the shape of the classic single-tenant pipeline
-- (staging/stg_orders.sql -> models/fact_orders.sql ->
-- metrics/metrics_retailer_daily.sql) with tenant_id carried through every
-- layer instead of being collapsed away - this is the "pooled" isolation
-- policy in practice (see multi_tenant/tenant_manager.py's module
-- docstring): every tenant's rows live in these same staging/marts tables,
-- distinguished only by the tenant_id column, not by a per-tenant schema.

create schema if not exists staging;
create schema if not exists marts;

create or replace table staging.stg_tenant_orders as
select
  tenant_id::varchar as tenant_id,
  order_id::varchar as order_id,
  retailer_id::varchar as retailer_id,
  product_id::varchar as product_id,
  try_cast(order_ts as timestamp) as order_ts,
  quantity::integer as quantity,
  gross_amount::double as gross_amount,
  coalesce(discount_amount, 0)::double as discount_amount,
  status::varchar as status
from raw.raw_tenant_orders
where tenant_id is not null and order_id is not null
qualify row_number() over (partition by tenant_id, order_id order by order_ts desc) = 1;

create table if not exists marts.fact_tenant_orders (
  tenant_id varchar,
  order_id varchar,
  retailer_id varchar,
  product_id varchar,
  order_date date,
  order_ts timestamp,
  quantity integer,
  gross_amount double,
  discount_amount double,
  net_amount double,
  status varchar,
  loaded_at timestamptz
);

create or replace temporary table delta_fact_tenant_orders as
select
  tenant_id,
  order_id,
  retailer_id,
  product_id,
  cast(order_ts as date) as order_date,
  order_ts,
  quantity,
  gross_amount,
  discount_amount,
  gross_amount - discount_amount as net_amount,
  status,
  current_timestamp as loaded_at
from staging.stg_tenant_orders;

delete from marts.fact_tenant_orders
where (tenant_id, order_id) in (select tenant_id, order_id from delta_fact_tenant_orders);

insert into marts.fact_tenant_orders
select * from delta_fact_tenant_orders;

-- Tenant usage / revenue rollup - the metric a tenant-scoped dashboard
-- (frontend/lib/tenant.ts's tenant switcher context, Section 4) and
-- observability's "tenant usage" tracking (Section 8) both read from.
create or replace view marts.metrics_tenant_daily as
select
  tenant_id,
  order_date,
  count(distinct order_id) as order_count,
  sum(quantity) as units_sold,
  sum(gross_amount) as gmv,
  sum(net_amount) as net_revenue,
  avg(net_amount) as average_order_value
from marts.fact_tenant_orders
group by tenant_id, order_date;

insert into elt_model_runs
select
  'fact_tenant_orders' as model_name,
  'marts.fact_tenant_orders' as target_table,
  'incremental_delete_insert' as load_strategy,
  'tenant_id,order_id' as business_key,
  (select count(*) from staging.stg_tenant_orders) as source_row_count,
  (select count(*) from delta_fact_tenant_orders) as affected_key_count,
  (select count(*) from marts.fact_tenant_orders) as target_row_count,
  null::timestamptz as high_watermark,
  min(loaded_at) as started_at,
  max(loaded_at) as completed_at,
  'success' as status
from delta_fact_tenant_orders;
