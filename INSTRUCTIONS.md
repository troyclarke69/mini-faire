### *Retail Marketplace Analytics Platform (Mini Faire)*  
### *DuckDB Edition — End‑to‑End Data Platform Demo*

---

## **Overview**
This repository contains a complete, minimal, end‑to‑end data platform demo called **Retail Marketplace Analytics Platform (Mini Faire)**.  
It is designed to showcase Staff‑level data platform architecture patterns including:

- Batch ingestion  
- Event ingestion (micro‑batch)  
- Data contracts (JSONSchema)  
- Validation + quarantine  
- Orchestration (Airflow or Prefect)  
- Warehouse modeling (Snowflake‑style, implemented in DuckDB)  
- Distributed compute (Polars or PySpark‑style transformations)  
- Metadata + lineage  
- Semantic layer + metrics  
- Optional lightweight API for metric exposure  

The goal is to demonstrate **architecture, correctness, reliability, and extensibility**, not production‑grade infrastructure.

---

## **Repository Structure**

```
mini-faire/
│
├── README.md
│
├── data/
│   ├── batch/
│   │   ├── retailers/
│   │   │   └── YYYY/MM/DD/retailers.json
│   │   ├── products/
│   │   │   └── YYYY/MM/DD/products.json
│   │   └── orders/
│   │       └── YYYY/MM/DD/orders.json
│   │
│   └── events/
│       └── order_created/
│           └── YYYY/MM/DD/HH/*.json
│
├── contracts/
│   ├── retailer.schema.json
│   ├── product.schema.json
│   ├── order.schema.json
│   └── order_created.schema.json
│
├── ingestion/
│   ├── batch_ingestion.py
│   ├── event_ingestion.py
│   ├── validate.py
│   ├── quarantine.py
│   └── metadata.py
│
├── orchestration/
│   ├── airflow/
│   │   ├── batch_marketplace_ingestion_elt.py
│   │   └── events_order_microbatch_elt.py
│   └── prefect/
│       ├── batch_flow.py
│       └── event_flow.py
│
├── warehouse/
│   ├── duckdb/
│   │   ├── init.sql
│   │   ├── staging/
│   │   │   ├── stg_retailers.sql
│   │   │   ├── stg_products.sql
│   │   │   └── stg_orders.sql
│   │   ├── models/
│   │   │   ├── dim_retailer.sql
│   │   │   ├── dim_product.sql
│   │   │   ├── fact_orders.sql
│   │   │   └── fact_orders_events.sql
│   │   └── metrics/
│   │       ├── metrics_retailer_daily.sql
│   │       ├── metrics_product_velocity.sql
│   │       └── metrics_order_profitability.sql
│
├── compute/
│   ├── polars/
│   │   ├── transform_orders.py
│   │   ├── transform_events.py
│   │   └── compute_metrics.py
│   └── pyspark/
│       └── spark_transform_example.py
│
├── governance/
│   ├── lineage.md
│   ├── ingestion_runs.sql
│   └── schema_versions.json
│
├── api/
│   └── metrics_api.py
│
└── diagrams/
    ├── architecture.png
    ├── ingestion_flow.png
    └── warehouse_model.png
```

---

## **1. Domain Definition**
The platform simulates a marketplace similar to Faire, with:

### **Entities**
- Retailers  
- Products  
- Orders  

### **Events**
- `order_created`  
- `order_paid`  
- `order_shipped`  
- `inventory_updated`  
- `price_changed`

Batch files represent daily snapshots; event files represent micro‑batched marketplace events.

---

## **2. Ingestion Layer**

### **Batch Ingestion**
Daily JSON files stored under:

```
raw/batch/<entity>/YYYY/MM/DD/<entity>.json
```

### **Event Ingestion**
Micro‑batched events stored under:

```
raw/events/<event_type>/YYYY/MM/DD/HH/*.json
```

### **Data Contracts**
JSONSchema files define strict schemas for:

- retailer  
- product  
- order  
- order_created event  

### **Validation + Quarantine**
Each ingestion path must:

- Validate records against JSONSchema  
- Write valid → `raw/.../valid/`  
- Write invalid → `raw/.../quarantine/`  
- Emit metadata → `raw/.../metadata/`  

Metadata includes:

- record counts  
- error counts  
- schema version  
- timestamps  
- batch ID  

---

## **3. Orchestration Layer**

Use **Airflow** or **Prefect**.

### **DAG 1 — Batch Ingestion + ELT**
Name: `batch_marketplace_ingestion_elt`  
Schedule: daily (2 AM)

Tasks:

1. detect_new_files  
2. validate_batch_files  
3. write_valid_and_quarantine  
4. load_raw_to_staging  
5. ELT staging → warehouse  
6. emit_metadata  

### **DAG 2 — Event Micro‑Batch + ELT**
Name: `events_order_microbatch_elt`  
Schedule: every 5 minutes

Tasks:

1. read_event_batch  
2. validate_events  
3. write_valid_and_quarantine  
4. load_events_to_staging  
5. ELT staging → fact_orders_events  
6. emit_metadata  

Both DAGs must be:

- idempotent  
- retry‑safe  
- clearly separated into raw → staging → warehouse  

---

## **4. Warehouse Layer (DuckDB)**

DuckDB simulates Snowflake‑style modeling.

### **Schema**
Create:

- `dim_retailer`  
- `dim_product`  
- `fact_orders`  
- `fact_orders_events`  

### **ELT Patterns**
Implement:

- incremental loads  
- deduplication  
- type casting  
- surrogate keys  
- optional SCD handling  

---

## **5. Distributed Compute Layer**

Use **Polars** or **PySpark** to implement transformations:

- GMV  
- order counts  
- inventory velocity  
- retailer health score  

These transformations feed the warehouse models and semantic layer.

---

## **6. Governance + Metadata**

Create:

### **Metadata Table**
`ingestion_runs` with:

- run_id  
- source (batch/events)  
- file/batch name  
- valid_count  
- invalid_count  
- schema_version  
- started_at  
- completed_at  
- status  

### **Lineage**
Optional lineage documentation or OpenLineage integration.

---

## **7. Semantic Layer + Metrics**

Define SQL views or dbt models for:

- `metrics_retailer_daily`  
- `metrics_product_velocity`  
- `metrics_order_profitability`  

Expose metrics via:

- Metabase  
- Superset  
- or a minimal API (`metrics_api.py`)

---

## **8. Deliverables**

The repository should include:

- Architecture diagrams  
- JSONSchema contracts  
- Sample data  
- Airflow/Prefect DAGs  
- DuckDB schema + ELT SQL  
- Polars/Python transformations  
- Metadata + lineage tables  
- Semantic layer SQL  
- Optional API  

Focus on:

- correctness  
- reliability  
- clarity  
- Staff‑level architectural patterns  

---

## **9. Non‑Goals**

This demo intentionally avoids:

- production‑grade infrastructure  
- full UI  
- real Kafka/Snowflake unless explicitly added  
- heavy cloud setup  

The goal is architectural clarity, not scale.

---

## **10. How to Use This Repository**

1. Populate `data/batch` and `data/events` with sample JSON files.  
2. Run validation scripts to generate valid/quarantine outputs.  
3. Execute Airflow or Prefect DAGs to orchestrate ingestion + ELT.  
4. Initialize DuckDB using `warehouse/duckdb/init.sql`.  
5. Run staging + warehouse SQL models.  
6. Run Polars/Python transformations.  
7. Query metrics via SQL or the API.  
8. Review lineage + metadata outputs.  
9. Explore diagrams for architecture understanding.
