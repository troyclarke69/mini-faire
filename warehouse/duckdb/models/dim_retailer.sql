create table if not exists marts.dim_retailer (
  retailer_key ubigint,
  retailer_id varchar,
  retailer_name varchar,
  country varchar,
  retailer_category varchar,
  signup_date date,
  status varchar,
  loaded_at timestamptz
);

create or replace temporary table delta_dim_retailer as
select
  hash(retailer_id) as retailer_key,
  retailer_id,
  retailer_name,
  country,
  retailer_category,
  signup_date,
  status,
  current_timestamp as loaded_at
from staging.stg_retailers;

delete from marts.dim_retailer
where retailer_id in (select retailer_id from delta_dim_retailer);

insert into marts.dim_retailer
select * from delta_dim_retailer;

insert into elt_model_runs
select
  'dim_retailer' as model_name,
  'marts.dim_retailer' as target_table,
  'incremental_delete_insert' as load_strategy,
  'retailer_id' as business_key,
  (select count(*) from staging.stg_retailers) as source_row_count,
  (select count(distinct retailer_id) from delta_dim_retailer) as affected_key_count,
  (select count(*) from marts.dim_retailer) as target_row_count,
  null::timestamptz as high_watermark,
  min(loaded_at) as started_at,
  max(loaded_at) as completed_at,
  'success' as status
from delta_dim_retailer;
