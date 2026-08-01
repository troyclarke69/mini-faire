# ⭐ **Ingestion Metadata & Lineage**

> Implement a complete ingestion metadata + lineage subsystem for the Mini Faire data platform.  
> All components must be deterministic, auditable, idempotent, reproducible, and integrated into batch + event ingestion flows.

### **1. Create DuckDB metadata table**
> Add `governance/ingestion_runs.sql` defining table `ingestion_runs` with fields:  
> `run_id`, `source_type`, `entity`, `file_path`, `schema_version`,  
> `valid_count`, `invalid_count`, `started_at`, `completed_at`,  
> `status`, `error_message`.  
> Table must support repeated inserts and full auditability.

### **2. Build metadata emitter**
> Add `ingestion/metadata.py` with function `emit_ingestion_metadata(...)` that:  
> - generates UUID `run_id`  
> - records timestamps  
> - writes metadata row into DuckDB  
> - stores schema version, counts, status, and errors  
> - is safe for retries and repeated runs.

### **3. Add schema version registry**
> Create `governance/schema_versions.json` containing version numbers for:  
> `retailer`, `product`, `order`, `order_created`.  
> Ingestion must read this file and embed schema version into metadata + quarantine outputs.

### **4. Implement quarantine error preservation**
> In `ingestion/quarantine.py`, write invalid records to:  
> `raw/<source>/<entity>/quarantine/YYYY/MM/DD/run_<run_id>/`.  
> For each invalid record, also write `<record>.errors.json` containing:  
> - validation errors  
> - schema version  
> - run_id  
> Quarantine must never drop data.

### **5. Add run IDs to all ingestion output paths**
> Modify ingestion code so valid + invalid outputs include:  
> `run_<run_id>` in directory paths.  
> Output paths must be deterministic and reproducible.

### **6. Integrate metadata into DAGs**
> Modify Airflow/Prefect DAGs so each run:  
> - generates a run_id  
> - passes run_id through all tasks  
> - calls `emit_ingestion_metadata()` on success  
> - calls `emit_ingestion_metadata()` on failure  
> DAGs must be idempotent and retry‑safe.

### **7. Add lineage documentation**
> Create `governance/lineage.md` documenting raw → staging → warehouse → metrics.  
> Include transformation descriptions, dependency mapping, and a Mermaid lineage diagram for both batch and event flows.

### **8. Add metadata inspection examples**
> Update README with SQL examples:  
> ```
> SELECT * FROM ingestion_runs ORDER BY started_at DESC;
> ```  
> Include instructions for inspecting run IDs, schema versions, and error logs.
