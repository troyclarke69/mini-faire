create table if not exists marts.dim_product (
  product_key ubigint,
  product_id varchar,
  brand_id varchar,
  product_name varchar,
  product_category varchar,
  unit_price decimal(12, 2),
  unit_cost decimal(12, 2),
  inventory_count integer,
  is_active boolean,
  loaded_at timestamptz
);

create or replace temporary table delta_dim_product as
select
  hash(product_id) as product_key,
  product_id,
  brand_id,
  product_name,
  product_category,
  unit_price,
  unit_cost,
  inventory_count,
  is_active,
  current_timestamp as loaded_at
from staging.stg_products;

delete from marts.dim_product
where product_id in (select product_id from delta_dim_product);

insert into marts.dim_product
select * from delta_dim_product;

insert into elt_model_runs
select
  'dim_product' as model_name,
  'marts.dim_product' as target_table,
  'incremental_delete_insert' as load_strategy,
  'product_id' as business_key,
  (select count(*) from staging.stg_products) as source_row_count,
  (select count(distinct product_id) from delta_dim_product) as affected_key_count,
  (select count(*) from marts.dim_product) as target_row_count,
  null::timestamptz as high_watermark,
  min(loaded_at) as started_at,
  max(loaded_at) as completed_at,
  'success' as status
from delta_dim_product;
