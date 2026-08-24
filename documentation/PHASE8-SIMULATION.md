# ⭐ Marketplace Simulation & Digital Twin Mode  
This is the phase where Mini Faire evolves from “analytics + ML” into a **full simulation engine** capable of modeling:

- retailer behavior  
- product lifecycle dynamics  
- pricing strategies  
- inventory strategies  
- demand curves  
- event chains  
- marketplace shocks  
- what‑if scenarios  
- counterfactuals  
- agent interactions  

This is the moment Mini Faire becomes a **marketplace digital twin** — a system where you can *simulate the future*, not just analyze the past.

Below is the **Phase 8 Master Prompt**, written in the same Staff‑level style as Phases 3–7.

---

# ⭐ **PHASE 8 MASTER PROMPT — Marketplace Simulation & Digital Twin Engine**

> Implement Phase 8 of Mini Faire by adding a **marketplace simulation engine**, **agent‑based modeling**, **scenario planning**, **counterfactual analysis**, and a **digital‑twin layer** that models retailer, product, and marketplace behavior over time.  
>
> All additions must integrate cleanly with the existing ingestion → streaming → monitoring → ML → multi‑tenant → cloud architecture.

---

## ⭐ SECTION 1 — Digital Twin Core Engine

### 🎯 Goal  
Create a digital‑twin representation of the marketplace.

### 🔧 Requirements  
Create:

```
simulation/digital_twin.py
```

It must:

- maintain state for each retailer  
- maintain state for each product  
- maintain inventory levels  
- maintain pricing curves  
- maintain demand curves  
- maintain event lifecycle states  
- maintain anomaly history  
- maintain ML predictions  
- maintain tenant isolation  

State must update:

- per event  
- per synthetic tick  
- per real‑time ingestion  
- per simulation step  

---

## ⭐ SECTION 2 — Agent‑Based Modeling (ABM)

### 🎯 Goal  
Model retailers and products as **agents** with strategies.

### 🔧 Requirements  
Create:

```
simulation/agents/
  retailer_agent.py
  product_agent.py
  marketplace_agent.py
```

Agents must have:

### **Retailer Strategies**
- pricing strategy  
- inventory strategy  
- promotion strategy  
- fulfillment strategy  
- anomaly response strategy  
- ML‑driven strategy (optional)  

### **Product Strategies**
- price elasticity  
- demand response  
- inventory decay  
- velocity curve  

### **Marketplace Strategies**
- global demand shocks  
- seasonal effects  
- category trends  
- competitor pressure  

Agents must interact with:

- synthetic generator  
- ML predictions  
- anomaly engine  
- monitoring metrics  
- tenant isolation  

---

## ⭐ SECTION 3 — Scenario Engine (What‑If Simulator)

### 🎯 Goal  
Allow users to run “what‑if” simulations.

### 🔧 Requirements  
Create:

```
simulation/scenario_engine.py
```

It must support:

### **Scenario Types**
- price change  
- inventory change  
- demand shock  
- supply chain delay  
- retailer outage  
- product launch  
- promotion event  
- competitor entry  
- competitor exit  

### **Outputs**
- predicted GMV  
- predicted velocity  
- predicted inventory levels  
- predicted anomalies  
- predicted retailer health  
- predicted cluster movement  
- predicted recommendations  

Write results to:

```
warehouse/simulation/scenario_results
```

Emit lineage edges.

---

## ⭐ SECTION 4 — Counterfactual Engine

### 🎯 Goal  
Model “what would have happened if X didn’t happen?”

### 🔧 Requirements  
Create:

```
simulation/counterfactuals.py
```

It must:

- remove events from history  
- modify events  
- replay ML predictions  
- replay agent behavior  
- recompute metrics  
- compare actual vs counterfactual  

Store results in:

```
warehouse/simulation/counterfactuals
```

---

## ⭐ SECTION 5 — Simulation Orchestration

### 🎯 Goal  
Add orchestration for running simulations.

### 🔧 Requirements  
Create:

```
orchestration/simulation_flow.py
```

It must:

- load digital twin  
- load agents  
- load ML models  
- run scenario engine  
- run counterfactual engine  
- run simulation ticks  
- write results  
- emit lineage edges  
- append simulation runs to `elt_model_runs`  

---

## ⭐ SECTION 6 — FastAPI Simulation API

### 🎯 Goal  
Expose simulation capabilities to the frontend.

### 🔧 Requirements  
Create:

```
api/simulation_api.py
```

Endpoints:

- `/simulation/run`  
- `/simulation/scenarios`  
- `/simulation/counterfactuals`  
- `/simulation/state`  
- `/simulation/agents`  
- `/simulation/results`  

Add WebSocket/SSE push for:

- simulation progress  
- simulation results  
- scenario outcomes  
- counterfactual outcomes  

---

## ⭐ SECTION 7 — Frontend Simulation Dashboards (Next.js)

### 🎯 Goal  
Add a full simulation UI.

### 🔧 Requirements  
Create pages:

```
frontend/pages/simulation/index.tsx
frontend/pages/simulation/scenarios.tsx
frontend/pages/simulation/counterfactuals.tsx
frontend/pages/simulation/agents.tsx
frontend/pages/simulation/results.tsx
```

Add components:

- ScenarioBuilder  
- CounterfactualBuilder  
- SimulationTimeline  
- AgentStrategyEditor  
- DigitalTwinVisualizer  
- SimulationResultCharts  

Add TypeScript types:

- SimulationState  
- Scenario  
- Counterfactual  
- AgentStrategy  
- SimulationResult  

Integrate WebSocket/SSE for live updates.

---

## ⭐ SECTION 8 — Deliverables

The agent must produce:

- digital twin engine  
- agent‑based modeling layer  
- scenario engine  
- counterfactual engine  
- simulation orchestration  
- FastAPI simulation API  
- Next.js simulation dashboards  
- updated configs  
- updated documentation  
- updated lineage diagrams  
- updated README sections  

Everything must run via:

```powershell
python orchestration/simulation_flow.py
```

And simulation dashboards must update live.

---

# ⭐ What comes after Phase 8?

Once Phase 8 is complete, Mini Faire is ready for:

> **Phase 9 — Autonomous Marketplace Agents**  
> retailer bots, pricing bots, inventory bots, demand bots, anomaly‑response bots.

> **Phase 10 — Full Marketplace Optimizer**  
> an end‑to‑end strategy engine that optimizes the entire marketplace.
