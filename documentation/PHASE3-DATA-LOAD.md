# ⭐ **Complete Phase 3 of Mini Faire**

> Extend Mini Faire by implementing three major enhancements:
> **(1) Multi‑day raw JSON ingestion**,  
> **(2) MongoDB ingestion as an upstream source**, and  
> **(3) A synthetic data generator that produces realistic marketplace activity**.  
>
> All additions must integrate cleanly with the existing ingestion → validation → quarantine → metadata → ELT → compute → API → frontend architecture.

---

# ⭐ **SECTION 1 — Multi‑Day Raw JSON Ingestion**

### **Goal**
Add realistic multi‑day marketplace data across retailers, products, orders, and event chains.

### **Requirements**
* NOTE: Items already (partially/completely) done.

*1. Create new raw JSON files under:
   ```
   data/batch/<entity>/YYYY/MM/DD/*.json
   data/events/<event_type>/YYYY/MM/DD/HH/*.json
   ```
*2. Add data for at least **4 days**:
   - 2026‑08‑15  
   - 2026‑08‑16  
   - 2026‑08‑17  
   - 2026‑08‑18  

*3. Include:
   - new retailers  
   - new products  
   - new orders  
   - event chains:
     - `order_created`
     - `order_paid`
     - `orders_shipped`
     - `inventory_updated`

4. Include **invalid records** to populate quarantine.

5. Ensure ingestion metadata + lineage edges reflect multi‑day runs.

6. Ensure ELT model runs increment for each day.

7. Ensure frontend charts show multi‑day trends.

---

# ⭐ **SECTION 2 — MongoDB Ingestion Layer**

### **Goal**
Add MongoDB as a realistic upstream source of raw marketplace data.

## **Connection String**
mongodb+srv://troy:<db_password>@cluster0.orxh2.gcp.mongodb.net/?appName=Cluster0
Database created: 'rmap'
No collections created yet.

### **Requirements**

#### **A. Add MongoDB config**
Create:
```
config/mongo.yaml
```
With:
- connection string  
- collections  
- batch size  
- watermark field  
- optional filters  
- schedule  

#### **B. Add MongoDB ingestion module**
Create:
```
ingestion/mongo_ingest.py
```

It must:
- connect to MongoDB  
- pull documents from configured collections  
- write each document as a raw JSON file into:
  ```
  data/raw/<entity>/<run_id>/<uuid>.json
  ```
- emit ingestion metadata  
- emit lineage edges  
- run validation  
- run quarantine  

#### **C. Add MongoDB → raw zone mapping**
Map MongoDB collections to entities:
- `retailers`
- `products`
- `orders`
- `order_created`
- `order_paid`
- `orders_shipped`
- `inventory_updated`

#### **D. Add MongoDB orchestration**
Create:
```
orchestration/mongo_flow.py
```

It must:
- read `mongo.yaml`  
- pull new documents  
- write raw JSON  
- trigger existing ingestion pipeline  

#### **E. Add MongoDB change stream ingestion (optional but recommended)**
Implement:
```
mongo_ingest_change_stream.py
```

It must:
- listen for inserts/updates  
- treat them as events  
- write raw JSON  
- trigger ingestion  

This simulates real‑time event ingestion.

---

# ⭐ **SECTION 3 — Synthetic Data Generator**

### **Goal**
Create a synthetic marketplace simulator that generates realistic retailers, products, orders, and events.

### **Requirements**

#### **A. Add generator module**
Create:
```
synthetic/generator.py
```

It must generate:
- retailers  
- products  
- orders  
- event chains  
- inventory updates  
- price changes  
- anomalies  
- seasonality  
- multi‑day data  

#### **B. Add generator config**
Create:
```
config/synthetic.yaml
```

With:
- number of retailers  
- number of products  
- daily order volume  
- event probabilities  
- inventory volatility  
- price volatility  
- anomaly frequency  
- date range  

#### **C. Add generator → raw zone writer**
Create:
```
synthetic/write_raw.py
```

It must:
- write generated documents into:
  ```
  data/batch/<entity>/YYYY/MM/DD/*.json
  data/events/<event_type>/YYYY/MM/DD/HH/*.json
  ```
- emit ingestion metadata  
- emit lineage edges  

#### **D. Add synthetic orchestration**
Create:
```
orchestration/synthetic_flow.py
```

It must:
- read `synthetic.yaml`  
- generate data  
- write raw JSON  
- trigger ingestion pipeline  

#### **E. Add synthetic → MongoDB writer (optional)**
Create:
```
synthetic/write_mongo.py
```

It must:
- insert synthetic documents into MongoDB  
- allow MongoDB ingestion to pick them up  
- allow change streams to emit events  

This enables real‑time simulation later.

---

# ⭐ **SECTION 4 — Integration Requirements**

### **A. Validation**
All synthetic + MongoDB documents must pass through:
- JSONSchema validation  
- quarantine  
- metadata  
- lineage  

### **B. ELT**
ELT must:
- incrementally load new staging tables  
- dedupe by natural keys  
- dedupe by event IDs  
- append audit rows to `elt_model_runs`  

### **C. Compute**
Polars compute must:
- incorporate new event types  
- compute lifecycle metrics  
- compute inventory velocity  
- compute reorder risk  
- compute retailer health  
- compute event lag summary  

### **D. API**
FastAPI must:
- expose new metrics  
- expose new compute outputs  
- expose new lineage edges  
- expose new ingestion metadata  

### **E. Frontend**
Next.js must:
- show multi‑day charts  
- show new event types  
- show new compute metrics  
- show new lineage edges  
- show new ingestion runs  
- show new quarantine records  

---

# ⭐ **SECTION 5 — Deliverables**

The agent must produce:

- all new Python modules  
- all new YAML configs  
- all new JSONSchema files  
- all new SQL (staging + ELT)  
- all new Polars transforms  
- all new FastAPI endpoints  
- all new frontend updates  
- updated README sections  
- updated governance documentation  
- updated lineage documentation  

Everything must be fully integrated and runnable via:

```powershell
.\.venv\Scripts\python.exe scripts\run_demo.py
```
