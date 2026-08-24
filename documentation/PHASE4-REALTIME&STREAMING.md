# ⭐ **Real‑Time Ingestion & Streaming Simulation**

> Implement Phase 4 of Mini Faire by adding **real‑time ingestion**, **MongoDB change‑stream streaming**, **continuous synthetic event generation**, **incremental ELT + compute refresh**, and **live frontend dashboards**.  
>
> All additions must integrate cleanly with the existing ingestion → validation → quarantine → metadata → ELT → compute → API → frontend architecture.

---

## ⭐ **SECTION 1 — Continuous Synthetic Streaming Generator**

### **Goal**
Upgrade the synthetic generator from batch mode to **continuous streaming mode**, producing realistic marketplace events every few seconds.

### **Requirements**
Create:

```
synthetic/stream_generator.py
```

It must:

- run an infinite or scheduled loop  
- generate events based on probabilities:
  - order_created  
  - order_paid  
  - orders_shipped  
  - inventory_updated  
  - price_changed  
- generate realistic timestamps  
- generate realistic lifecycle delays  
- insert events into MongoDB (preferred) **OR** write directly to:
  ```
  data/events/<event_type>/YYYY/MM/DD/HH/*.json
  ```
- emit ingestion metadata  
- emit lineage edges  
- support graceful shutdown  
- support configurable frequency via `config/synthetic.yaml`

### **Config updates**
Extend `config/synthetic.yaml`:

```yaml
streaming:
  enabled: true
  events_per_minute: 30
  lifecycle_probabilities:
    order_paid: 0.95
    orders_shipped: 0.90
    inventory_updated: 0.40
    price_changed: 0.10
```

---

## ⭐ **SECTION 2 — MongoDB Change Stream Ingestion**

### **Goal**
Add a real‑time ingestion path using MongoDB change streams as a pseudo‑Kafka.

### **Requirements**
Create:

```
ingestion/mongo_change_stream.py
```

It must:

- connect to MongoDB  
- watch collections:
  - retailers  
  - products  
  - orders  
  - events  
  - inventory  
  - pricing  
- convert change events into raw JSON  
- write them to:
  ```
  data/raw/<entity>/<run_id>/<uuid>.json
  ```
- trigger ingestion pipeline  
- emit lineage edges  
- handle:
  - inserts  
  - updates  
  - deletes  
  - replacements  
- support resume tokens  
- support backoff + retry  
- support configurable watch list via `config/mongo.yaml`

### **Config updates**
Extend `config/mongo.yaml`:

```yaml
change_streams:
  enabled: true
  collections:
    - orders
    - events
    - inventory
    - pricing
```

---

## ⭐ **SECTION 3 — Real‑Time Orchestration Layer**

### **Goal**
Add a real‑time orchestration flow that reacts to new raw files or new MongoDB events.

### **Requirements**
Create:

```
orchestration/realtime_flow.py
```

It must:

- detect new raw JSON files  
- detect new MongoDB change‑stream events  
- run staging loaders incrementally  
- run incremental ELT:
  - dedupe by natural keys  
  - dedupe by event IDs  
  - append audit rows to `elt_model_runs`  
- run Polars compute incrementally  
- emit lineage edges  
- update high‑watermarks  
- run continuously or on a schedule  
- support concurrency limits  
- support backpressure handling  

### **Integration**
This flow must be triggered by:

- synthetic streaming generator  
- MongoDB change streams  
- manual batch ingestion  

---

## ⭐ **SECTION 4 — Incremental ELT + Compute Refresh**

### **Goal**
Make ELT + compute run **incrementally** as new events arrive.

### **Requirements**

#### **A. ELT**
Update DuckDB ELT SQL to:

- process only new staging rows  
- dedupe by natural keys  
- dedupe by event IDs  
- maintain high‑watermarks  
- append audit rows to `elt_model_runs`  

#### **B. Compute**
Update Polars compute to:

- refresh metrics incrementally  
- compute lifecycle metrics in near‑real‑time  
- compute inventory velocity  
- compute reorder risk  
- compute event lag summary  
- compute retailer health  
- append compute runs to `elt_model_runs`  

#### **C. Metadata**
Emit lineage edges for:

- streaming ingestion  
- incremental ELT  
- incremental compute  

---

## ⭐ **SECTION 5 — FastAPI Real‑Time API Layer**

### **Goal**
Expose real‑time updates to the frontend.

### **Requirements**
Add:

```
api/realtime_api.py
```

It must expose:

### **A. WebSocket endpoint**
- push ingestion run updates  
- push ELT model run updates  
- push compute run updates  
- push new metrics  
- push new lineage edges  

### **B. SSE endpoint (optional alternative)**
- same functionality as WebSocket  
- simpler client integration  

### **C. Health checks**
Add `/realtime/health` to verify streaming services are running.

---

## ⭐ **SECTION 6 — Live Frontend Dashboards (Next.js)**

### **Goal**
Make the frontend **live**, updating automatically as new data arrives.

### **Requirements**

#### **A. Add WebSocket/SSE client**
Create:

```
frontend/lib/realtime.ts
```

It must:

- connect to FastAPI WebSocket/SSE  
- listen for:
  - ingestion updates  
  - ELT updates  
  - compute updates  
  - lineage updates  
  - metric updates  
- expose a hook:
  ```ts
  useRealtimeUpdates()
  ```

#### **B. Update pages**
Modify:

- `/` overview dashboard  
- `/compute`  
- `/lineage`  
- `/orders`  
- `/products`  
- `/retailers`  

Each must:

- auto‑refresh charts  
- auto‑refresh tables  
- auto‑refresh lineage graph  
- show live ingestion counters  
- show live compute run timestamps  

#### **C. Add “Live Mode” toggle**
Add a UI toggle:

```
Live Mode: ON/OFF
```

When ON:

- charts update automatically  
- tables revalidate faster  
- lineage graph animates new edges  

---

## ⭐ **SECTION 7 — Deliverables**

The agent must produce:

- synthetic streaming generator  
- MongoDB change‑stream ingestion  
- real‑time orchestration  
- incremental ELT updates  
- incremental compute updates  
- FastAPI WebSocket/SSE endpoints  
- Next.js real‑time client  
- live dashboards  
- updated configs  
- updated documentation  
- updated lineage diagrams  
- updated README sections  

Everything must run via:

```powershell
.\.venv\Scripts\python.exe scripts\run_demo.py
```

And streaming services must run via:

```powershell
python synthetic/stream_generator.py
python ingestion/mongo_change_stream.py
python orchestration/realtime_flow.py
```
