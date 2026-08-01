# ⭐ **Incremental ELT (DuckDB Edition)**

> Implement a complete incremental ELT subsystem for the Mini Faire data platform.  
> All components must be deterministic, idempotent, lineage‑aware, and compatible with existing ingestion metadata + run IDs.

---

## **1. Add incremental staging loaders**
> Update all staging SQL files under `warehouse/duckdb/staging/` so they:  
> - load only *new* raw files for the current `run_id`  
> - include `run_id`, `ingested_at`, and `schema_version` columns  
> - dedupe by natural keys (`retailer_id`, `product_id`, `order_id`, `event_id`)  
> - enforce type casting and null‑safety  
> Staging tables must be append‑only and partition‑aware.

---

## **2. Implement incremental fact table merges**
> Update all fact models under `warehouse/duckdb/models/` to use incremental merge patterns:  
> - `INSERT OR REPLACE` for DuckDB  
> - dedupe using natural keys  
> - filter staging rows using:  
>   `WHERE stg.ingested_at > (SELECT COALESCE(MAX(ingested_at), '1900-01-01') FROM fact_table)`  
> - generate surrogate keys using `uuid()` or hash of natural keys  
> - maintain `created_at` and `updated_at` timestamps  
> Fact tables must be fully idempotent.

---

## **3. Add incremental dimension updates**
> Update `dim_retailer` and `dim_product` to support incremental SCD‑like behavior:  
> - detect changes in descriptive attributes  
> - update existing rows when attributes change  
> - preserve stable surrogate keys  
> - maintain `effective_from` and `effective_to` timestamps  
> Dimensions must support repeatable runs without duplication.

---

## **4. Add event → order reconciliation**
> In `fact_orders_events.sql`, implement logic to:  
> - dedupe events by `event_id`  
> - merge events into order lifecycle  
> - compute event lag (`event_timestamp - ingested_at`)  
> - update order status incrementally  
> - maintain event history  
> This must be idempotent and safe for micro‑batch ingestion.

---

## **5. Add incremental metric refresh**
> Update metric SQL under `warehouse/duckdb/metrics/` to:  
> - compute metrics only from newly updated facts  
> - use `CREATE OR REPLACE VIEW` for idempotency  
> - include lineage references to upstream fact tables  
> Metrics must refresh deterministically after each DAG run.

---

## **6. Integrate incremental ELT into DAGs**
> Modify Airflow/Prefect DAGs so ELT tasks:  
> - accept `run_id` from ingestion  
> - load staging incrementally  
> - merge facts incrementally  
> - refresh metrics  
> - emit lineage edges for each transformation  
> DAGs must be retry‑safe and idempotent.

---

## **7. Add ELT lineage edges**
> Update lineage emission so each ELT step writes edges into `lineage_edges` table:  
> - `raw → staging`  
> - `staging → fact`  
> - `fact → metrics`  
> Include:  
> - `run_id`  
> - `source_table`  
> - `target_table`  
> - `transformation_name`  
> - `duration_ms`  
> Lineage must reflect incremental dependencies.

---

## **8. Add ELT audit queries to README**
> Update README with SQL examples:  
> ```
> SELECT * FROM ingestion_runs ORDER BY started_at DESC;
> SELECT * FROM lineage_edges ORDER BY occurred_at DESC;
> SELECT * FROM fact_orders ORDER BY updated_at DESC LIMIT 20;
> SELECT * FROM dim_retailer WHERE effective_to IS NULL;
> ```  
> Include instructions for verifying incremental loads and surrogate key stability.
