# ⭐ **Alerts, Anomalies & Monitoring**

> Implement Phase 5 of Mini Faire by adding **anomaly detection**, **alerting**, **monitoring dashboards**, **schema‑drift detection**, **ingestion latency tracking**, and **compute/ELT reliability metrics**.  
>
> All additions must integrate cleanly with the existing ingestion → validation → quarantine → metadata → ELT → compute → API → frontend architecture, including the real‑time streaming layer from Phase 4.

---

## ⭐ **SECTION 1 — Anomaly Detection Engine**

### 🎯 Goal  
Add a modular anomaly‑detection subsystem that continuously evaluates incoming data for unusual patterns.

### 🔧 Requirements  
Create:

```
anomalies/detector.py
```

It must detect anomalies in:

- GMV spikes/drops  
- order velocity changes  
- inventory stockouts  
- price anomalies  
- event lag spikes  
- retailer health degradation  
- ingestion volume anomalies  
- quarantine rate anomalies  

Detection methods:

- rolling mean + std deviation  
- EWMA (exponential weighted moving average)  
- percentile‑based thresholds  
- z‑score  
- optional ML model (later phase)

Each anomaly must produce:

- anomaly_id  
- anomaly_type  
- severity  
- timestamp  
- entity_id (retailer/product/order)  
- metadata  

Write anomalies to:

```
warehouse/anomalies/anomaly_events
```

Emit lineage edges for each anomaly.

---

## ⭐ **SECTION 2 — Alerting System (Slack/Webhooks)**

### 🎯 Goal  
Send alerts when anomalies occur or when system reliability degrades.

### 🔧 Requirements  
Create:

```
alerts/dispatcher.py
```

It must support:

- Slack webhook  
- generic webhook  
- console fallback  

Alert types:

- anomaly detected  
- ingestion failure  
- ingestion latency threshold exceeded  
- ELT failure  
- compute failure  
- schema drift detected  
- quarantine rate spike  
- MongoDB change‑stream disconnect  
- synthetic generator failure  

Alert payload must include:

- timestamp  
- severity  
- entity  
- anomaly metadata  
- lineage reference  
- link to frontend dashboard  

Add config:

```
config/alerts.yaml
```

---

## ⭐ **SECTION 3 — Monitoring Metrics & Reliability Tracking**

### 🎯 Goal  
Track ingestion, ELT, compute, and streaming reliability.

### 🔧 Requirements  
Create:

```
monitoring/metrics.py
```

It must compute:

### **Ingestion Metrics**
- ingestion latency  
- ingestion throughput  
- ingestion error rate  
- quarantine rate  
- schema drift frequency  
- change‑stream lag  

### **ELT Metrics**
- ELT run duration  
- ELT failure rate  
- ELT incremental volume  
- ELT high‑watermark lag  

### **Compute Metrics**
- compute run duration  
- compute failure rate  
- compute incremental volume  

### **Streaming Metrics**
- synthetic generator event rate  
- MongoDB change‑stream event rate  
- streaming backlog  
- streaming lag  

Write metrics to:

```
warehouse/monitoring/system_metrics
```

Emit lineage edges.

---

## ⭐ **SECTION 4 — Schema Drift Detection**

### 🎯 Goal  
Detect when incoming documents differ from expected JSONSchema.

### 🔧 Requirements  
Create:

```
monitoring/schema_drift.py
```

It must:

- compare incoming raw JSON fields to schema  
- detect missing fields  
- detect new fields  
- detect type mismatches  
- detect enum violations  
- detect timestamp format issues  

Write drift events to:

```
warehouse/monitoring/schema_drift_events
```

Trigger alerts via dispatcher.

---

## ⭐ **SECTION 5 — FastAPI Monitoring & Alerts API**

### 🎯 Goal  
Expose monitoring + anomaly + alert data to the frontend.

### 🔧 Requirements  
Create:

```
api/monitoring_api.py
```

Endpoints:

- `/monitoring/system-metrics`  
- `/monitoring/anomalies`  
- `/monitoring/schema-drift`  
- `/monitoring/alerts`  
- `/monitoring/health`  
- `/monitoring/streaming-status`  

Add WebSocket/SSE push:

- new anomalies  
- new alerts  
- new monitoring metrics  
- streaming lag updates  
- ingestion latency updates  

---

## ⭐ **SECTION 6 — Frontend Monitoring Dashboards (Next.js)**

### 🎯 Goal  
Add a full monitoring section to the frontend.

### 🔧 Requirements  
Create pages:

```
frontend/pages/monitoring/index.tsx
frontend/pages/monitoring/anomalies.tsx
frontend/pages/monitoring/system.tsx
frontend/pages/monitoring/schema-drift.tsx
frontend/pages/monitoring/alerts.tsx
```

Each page must:

- use WebSocket/SSE for live updates  
- show charts + tables  
- show severity indicators  
- show lineage references  
- show timestamps  
- show trend lines  
- show anomaly history  

Add components:

```
frontend/components/monitoring/AnomalyTable.tsx
frontend/components/monitoring/SystemMetricsChart.tsx
frontend/components/monitoring/SchemaDriftTable.tsx
frontend/components/monitoring/AlertsFeed.tsx
frontend/components/monitoring/StreamingStatus.tsx
```

Add TypeScript types:

```
AnomalyEvent
SystemMetric
SchemaDriftEvent
AlertEvent
```

---

## ⭐ **SECTION 7 — Integration with Real‑Time Orchestration**

### 🎯 Goal  
Tie anomaly detection + monitoring into the real‑time ingestion pipeline.

### 🔧 Requirements  
Modify:

```
orchestration/realtime_flow.py
```

Add:

- anomaly detection after compute  
- monitoring metric updates after ingestion/ELT/compute  
- alert dispatch on anomalies  
- alert dispatch on failures  
- lineage emission for monitoring events  

---

## ⭐ **SECTION 8 — Deliverables**

The agent must produce:

- anomaly detection engine  
- alerting system  
- monitoring metrics subsystem  
- schema drift detection  
- FastAPI monitoring API  
- Next.js monitoring dashboards  
- real‑time integration  
- updated configs  
- updated documentation  
- updated lineage diagrams  
- updated README sections  

Everything must run via:

```powershell
python synthetic/stream_generator.py
python ingestion/mongo_change_stream.py
python orchestration/realtime_flow.py
```

And monitoring must update live in the frontend.
