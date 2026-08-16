# ⭐ **Next.js Frontend for Mini Faire**

> Build a complete Next.js 14 (App Router) frontend for the Mini Faire analytics platform.  
> The frontend must visualize ingestion metadata, lineage, ELT model runs, semantic metrics, and Polars compute outputs from the FastAPI backend.  
> Use TypeScript, Server Components where possible, Client Components only where required, and TailwindCSS for layout.

---

## ⭐ **1. Framework Requirements**
> Use **Next.js 14 App Router** with:
> - TypeScript  
> - TailwindCSS  
> - Server Components for all static or cached data  
> - Client Components only for charts, interactions, and filters  
> - `fetch()` with `next: { revalidate: X }` for SSR caching  
> - Environment variable for API base URL: `NEXT_PUBLIC_API_URL`

---

## ⭐ **2. Dynamic vs Static Rendering (explicit rules)**

### **Static (Server Components, SSR cached)**
Use static rendering for:
- **Retailer Daily Metrics**  
- **Product Velocity Metrics**  
- **Order Profitability Metrics**  
- **Brand Contribution**  
- **Event Lag Summary**  
- **Retailer Cohort Retention**  
- **Model Runs Summary**  
- **Lineage Graph (table form)**  
- **Ingestion Runs Table**

These endpoints change only when `run_demo.py` or ingestion/ELT runs.  
Use `fetch(url, { next: { revalidate: 30 } })`.

### **Dynamic (Client Components)**
Use dynamic rendering for:
- **Charts** (GMV trend, velocity trend, profitability trend)  
- **Interactive lineage graph**  
- **Quarantine viewer**  
- **Filters** (date range, retailer, product)  
- **Sorting + pagination**  

These require browser interactivity.

---

## ⭐ **3. Pages & Routes**
> Implement the following Next.js routes:

### **Dashboard**
- `/` — Overview dashboard  
- `/retailers` — Retailer daily metrics  
- `/products` — Product velocity + reorder risk  
- `/orders` — Order profitability + event lag  
- `/compute` — Polars compute outputs  
- `/lineage` — Lineage graph + ingestion runs  
- `/quarantine` — Invalid records viewer  
- `/model-runs` — ELT model run history  

---

## ⭐ **4. Component Architecture**
> Create the following components:

### **Server Components**
- `RetailerDailyTable`  
- `ProductVelocityTable`  
- `OrderProfitabilityTable`  
- `ModelRunsTable`  
- `IngestionRunsTable`  
- `LineageTable`  

### **Client Components**
- `GMVChart`  
- `VelocityChart`  
- `ProfitabilityChart`  
- `EventLagChart`  
- `LineageGraph` (D3.js or Cytoscape.js)  
- `QuarantineViewer`  
- `DateRangePicker`  
- `RetailerSelector`  
- `ProductSelector`  

---

## ⭐ **5. API Integration**
> Integrate with the following FastAPI endpoints:

### **Metrics**
- `/metrics/retailer-daily`  
- `/metrics/product-velocity`  
- `/metrics/order-profitability`

### **Compute**
- `/compute/retailer-health`  
- `/compute/product-reorder-risk`  
- `/compute/brand-contribution`  
- `/compute/retailer-cohort-retention`  
- `/compute/event-lag-summary`  
- `/compute/model-runs`

### **Governance**
- `/metadata/ingestion-runs`  
- `/metadata/lineage-edges`  
- `/metadata/quarantine-records`

### **Health**
- `/health`

Use a shared API client under `lib/api.ts`.

---

## ⭐ **6. Dashboard Requirements**
> The dashboard must show:

### **Top-level KPIs**
- Total GMV (last 7 days)  
- Total orders  
- Avg order value  
- Velocity score  
- Profitability margin  
- Event lag median  
- Retailer health score  

### **Charts**
- GMV trend  
- Velocity trend  
- Profitability trend  
- Event lag distribution  

### **Tables**
- Retailer daily metrics  
- Product velocity  
- Order profitability  
- Model runs  
- Ingestion runs  

---

## ⭐ **7. Lineage Visualization**
> Build an interactive lineage graph using:
- nodes: raw → staging → warehouse → metrics → compute → API  
- edges: from `lineage_edges` table  
- color-coded by subsystem  
- hover tooltips showing run_id, duration, and affected rows  

---

## ⭐ **8. Quarantine Viewer**
> Build a page `/quarantine` that:
- lists invalid records  
- shows validation errors  
- shows schema version mismatches  
- links back to ingestion run  
- supports pagination + filtering  

---

## ⭐ **9. Styling & UX**
> Use TailwindCSS with:
- responsive layout  
- left sidebar navigation  
- top header with environment indicator  
- dark mode toggle  
- consistent card components  
- loading skeletons  
- error boundaries  

---

## ⭐ **10. Deliverables**
> Produce:
- full Next.js project structure  
- all pages + components  
- API client  
- chart components  
- lineage graph  
- quarantine viewer  
- environment config  
- README for the frontend  
- instructions for running with FastAPI backend  
