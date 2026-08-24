# ⭐ **Machine Learning Layer (Forecasting, Clustering, Recommendations)**

> Implement Phase 6 of Mini Faire by adding a modular **machine learning subsystem** that performs forecasting, clustering, recommendation modeling, anomaly classification, and retailer/product segmentation.  
>
> All additions must integrate cleanly with the existing ingestion → validation → quarantine → metadata → ELT → compute → anomalies → monitoring → API → frontend architecture.

---

## ⭐ **SECTION 1 — ML Infrastructure & Model Registry**

### 🎯 Goal  
Add a unified ML infrastructure layer to train, store, version, and serve models.

### 🔧 Requirements  
Create:

```
ml/registry.py
```

It must:

- store model metadata  
- store model versions  
- store training parameters  
- store evaluation metrics  
- store feature schema  
- store training timestamps  
- support “active model” selection  
- support rollback to previous versions  

Add:

```
ml/models/
```

Containing:

- forecasting models  
- clustering models  
- recommendation models  
- anomaly classifiers  

Add:

```
ml/features/
```

Containing:

- feature builders  
- feature schemas  
- feature validation  

Add config:

```
config/ml.yaml
```

---

## ⭐ **SECTION 2 — Feature Engineering Layer**

### 🎯 Goal  
Create a unified feature store for ML models.

### 🔧 Requirements  
Create:

```
ml/features/build_features.py
```

It must generate:

### **Retailer Features**
- daily GMV  
- velocity  
- event lag  
- anomaly counts  
- inventory volatility  
- price volatility  
- health score  

### **Product Features**
- velocity  
- reorder risk  
- inventory volatility  
- price elasticity  
- anomaly counts  

### **Order Features**
- lifecycle duration  
- payment lag  
- shipping lag  

### **Event Features**
- event frequency  
- event type distribution  

Write features to:

```
warehouse/ml/features
```

Emit lineage edges.

---

## ⭐ **SECTION 3 — Forecasting Models**

### 🎯 Goal  
Predict future marketplace behavior.

### 🔧 Requirements  
Create:

```
ml/models/forecasting.py
```

Implement forecasting for:

### **GMV Forecast**
- daily GMV  
- weekly GMV  
- retailer‑level GMV  

### **Velocity Forecast**
- product velocity  
- retailer velocity  

### **Inventory Forecast**
- stockout prediction  
- reorder prediction  

### **Price Forecast**
- price trend prediction  

Use models such as:

- Prophet  
- ARIMA  
- Holt‑Winters  
- LightGBM  
- RandomForestRegressor  

Store forecasts in:

```
warehouse/ml/forecasts
```

Emit lineage edges.

---

## ⭐ **SECTION 4 — Clustering Models**

### 🎯 Goal  
Segment retailers and products into meaningful groups.

### 🔧 Requirements  
Create:

```
ml/models/clustering.py
```

Implement clustering for:

### **Retailer Segmentation**
- high‑velocity  
- low‑velocity  
- high‑GMV  
- low‑GMV  
- anomaly‑prone  
- stable  

### **Product Segmentation**
- fast movers  
- slow movers  
- high margin  
- low margin  
- volatile inventory  
- stable inventory  

Use models:

- KMeans  
- DBSCAN  
- Gaussian Mixture Models  

Store clusters in:

```
warehouse/ml/clusters
```

Emit lineage edges.

---

## ⭐ **SECTION 5 — Recommendation Models**

### 🎯 Goal  
Add marketplace recommendation intelligence.

### 🔧 Requirements  
Create:

```
ml/models/recommendations.py
```

Implement:

### **Product Recommendations**
- “products frequently bought together”  
- “products similar to X”  
- “products trending in category Y”  

### **Retailer Recommendations**
- “retailers similar to X”  
- “retailers likely to grow”  

Use models:

- item‑item similarity  
- collaborative filtering  
- cosine similarity  
- matrix factorization  

Store recommendations in:

```
warehouse/ml/recommendations
```

Emit lineage edges.

---

## ⭐ **SECTION 6 — Anomaly Classification Model**

### 🎯 Goal  
Upgrade Phase 5 anomaly detection with ML‑based classification.

### 🔧 Requirements  
Create:

```
ml/models/anomaly_classifier.py
```

It must classify anomalies into:

- GMV spike  
- GMV drop  
- velocity anomaly  
- inventory anomaly  
- price anomaly  
- event lag anomaly  
- retailer health anomaly  

Use models:

- RandomForestClassifier  
- GradientBoostingClassifier  
- XGBoost  

Store classifications in:

```
warehouse/ml/anomaly_classifications
```

Emit lineage edges.

---

## ⭐ **SECTION 7 — ML Training Orchestration**

### 🎯 Goal  
Add orchestration for training, evaluation, and deployment.

### 🔧 Requirements  
Create:

```
orchestration/ml_training_flow.py
```

It must:

- build features  
- train models  
- evaluate models  
- store metrics  
- register models  
- activate new models  
- rollback on failure  
- emit lineage edges  
- append training runs to `elt_model_runs`  

Add:

```
orchestration/ml_inference_flow.py
```

It must:

- load active models  
- run inference  
- write predictions to warehouse  
- emit lineage edges  

---

## ⭐ **SECTION 8 — FastAPI ML Endpoints**

### 🎯 Goal  
Expose ML predictions to the frontend.

### 🔧 Requirements  
Create:

```
api/ml_api.py
```

Endpoints:

- `/ml/forecasts`  
- `/ml/clusters`  
- `/ml/recommendations`  
- `/ml/anomalies/classified`  
- `/ml/models`  
- `/ml/features`  

Add WebSocket/SSE push for:

- new forecasts  
- new clusters  
- new recommendations  
- new anomaly classifications  

---

## ⭐ **SECTION 9 — Frontend ML Dashboards (Next.js)**

### 🎯 Goal  
Add ML dashboards to the UI.

### 🔧 Requirements  
Create pages:

```
frontend/pages/ml/forecasts.tsx
frontend/pages/ml/clusters.tsx
frontend/pages/ml/recommendations.tsx
frontend/pages/ml/anomalies.tsx
frontend/pages/ml/models.tsx
```

Add components:

- ForecastChart  
- ClusterMap  
- RecommendationList  
- AnomalyClassificationTable  
- ModelRegistryTable  

Add TypeScript types:

- Forecast  
- Cluster  
- Recommendation  
- AnomalyClassification  
- ModelMetadata  

Integrate WebSocket/SSE for live updates.

---

## ⭐ **SECTION 10 — Deliverables**

The agent must produce:

- ML infrastructure  
- feature store  
- forecasting models  
- clustering models  
- recommendation models  
- anomaly classifier  
- ML training + inference orchestration  
- FastAPI ML endpoints  
- Next.js ML dashboards  
- updated configs  
- updated documentation  
- updated lineage diagrams  
- updated README sections  

Everything must run via:

```powershell
python orchestration/ml_training_flow.py
python orchestration/ml_inference_flow.py
```

And ML dashboards must update live.

---

# ⭐ **NEXT NATURAL STEP (Phase 7 Preview)**

Once Phase 6 is complete, Mini Faire is ready for:

> **Phase 7 — Cloud Deployment & Multi‑Tenant Mode**  
> Deploy backend + frontend, add user accounts, add retailer‑specific dashboards, add multi‑tenant isolation.
