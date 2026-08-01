# Warehouse Model

```mermaid
erDiagram
  DIM_RETAILER ||--o{ FACT_ORDERS : places
  DIM_PRODUCT ||--o{ FACT_ORDERS : contains
  DIM_RETAILER ||--o{ FACT_ORDERS_EVENTS : emits
  DIM_PRODUCT ||--o{ FACT_ORDERS_EVENTS : referenced_by
```

