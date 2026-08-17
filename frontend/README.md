# Mini Faire Frontend

Next.js 14 (App Router) UI for the Mini Faire retail marketplace analytics platform. It visualizes ingestion metadata, lineage, ELT/Polars compute runs, and semantic metrics served by the FastAPI backend in `../api/metrics_api.py`.

## Prerequisites

- Node.js 18.18+ (Node 20/22 both work)
- The Mini Faire demo data pipeline already run once from the repo root, so `data/warehouse/mini_faire.duckdb` exists:

  ```powershell
  .\.venv\Scripts\python.exe scripts\run_demo.py
  ```

## Run

Start the FastAPI backend from the repo root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.metrics_api:app --reload
```

In a second terminal, start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:3000`. The backend must be running first — every page fetches from it at request time (or build time for `npm run build`/`npm run start`).

## Configuration

`NEXT_PUBLIC_API_URL` points the frontend at the FastAPI backend. Copy `.env.example` to `.env.local` and adjust if the API isn't running on the default `http://127.0.0.1:8000`:

```
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

## Project Structure

```
frontend/
├── app/                     # App Router routes (one folder per page)
│   ├── page.tsx             # / — overview dashboard
│   ├── retailers/           # /retailers
│   ├── products/            # /products
│   ├── orders/              # /orders
│   ├── compute/             # /compute
│   ├── lineage/             # /lineage
│   ├── quarantine/          # /quarantine
│   ├── model-runs/          # /model-runs
│   ├── layout.tsx           # shell: sidebar nav, header, theme toggle
│   ├── loading.tsx          # route-level skeleton
│   └── error.tsx            # route-level error boundary
├── components/
│   ├── tables/               # Server Components — fetch + render cached tables
│   ├── charts/                # Client Components — SVG bar charts
│   ├── filters/                # Client Components — date/retailer/product filters
│   ├── LineageGraph.tsx        # Client Component — interactive SVG lineage graph
│   └── QuarantineViewer.tsx    # Client Component — paginated quarantine record viewer
├── lib/
│   ├── api.ts                # shared fetch client + formatters (money/number/percent)
│   └── types.ts               # TypeScript types mirroring the FastAPI response shapes
└── .env.example
```

## Rendering strategy

Following the platform's static/dynamic split:

- **Tables** (`components/tables/*`) are `async` Server Components. Each one calls `lib/api.ts`, which uses `fetch(url, { next: { revalidate: 30 } })` so Next.js caches and revalidates the data every 30 seconds — no client JS is shipped for them.
- **Charts, the lineage graph, the quarantine viewer, and filters** (`components/charts/*`, `LineageGraph.tsx`, `QuarantineViewer.tsx`, `components/filters/*`) are `"use client"` components. They receive already-fetched rows as props from their parent Server Component/page and handle interactivity (hover tooltips, pagination, dropdown/date filtering) in the browser.
- Filter dropdowns and date ranges filter the **chart** immediately above/below them client-side (see `RetailerFilterPanel`, `ProductFilterPanel`, `OrdersFilterPanel`). The **tables** intentionally stay on the cached server-rendered path per the platform's static-table rule — they reflect the full most-recent dataset from the warehouse, refreshed on the `revalidate: 30` cache window, independent of the client-side chart filters.

## API surface used

All requests go through `lib/api.ts`. Endpoints (see the backend's `README.md`/`/docs` for schemas):

| Area | Endpoints |
| --- | --- |
| Metrics | `/metrics/retailer-daily`, `/metrics/product-velocity`, `/metrics/order-profitability` |
| Compute | `/compute/retailer-health`, `/compute/product-reorder-risk`, `/compute/brand-contribution`, `/compute/retailer-cohort-retention`, `/compute/event-lag-summary`, `/compute/model-runs` |
| Governance | `/metadata/ingestion-runs`, `/metadata/lineage-edges`, `/metadata/elt-model-runs`, `/metadata/quarantine-records` |
| Health | `/health` |

All fetches happen server-side (inside Server Components/route handlers running on the Next.js server), so no CORS configuration is required on the FastAPI backend — the browser never calls it directly.

## Notable implementation choices

- The lineage graph (`components/LineageGraph.tsx`) renders inline SVG rather than pulling in D3/Cytoscape, to keep the dependency footprint minimal for a demo; nodes are color-coded by subsystem (`raw`, `staging`, `marts`, `api`, source data) and edges show a tooltip-style detail panel on hover.
- `lib/api.ts` fails soft: a non-OK response or network error returns an empty array rather than throwing, so a page renders its `EmptyState` instead of crashing if a given warehouse table/view hasn't been populated yet.
- Icons from `lucide-react` are rendered inside the Server Component (`app/layout.tsx`) and passed to the client `NavLink` as a rendered `ReactNode`, not as a component reference — passing a function/component reference itself across the Server→Client boundary is not serializable and throws `Functions cannot be passed directly to Client Components...`.

## Troubleshooting

- **"Warehouse not built" / empty tables everywhere**: run `scripts/run_demo.py` from the repo root first, then restart `uvicorn`.
- **`ECONNREFUSED` during `npm run dev`/`npm run build`**: the FastAPI backend isn't running, or `NEXT_PUBLIC_API_URL` doesn't match its address. Pages will still render (with empty states) since `lib/api.ts` swallows fetch errors.
- **`Functions cannot be passed directly to Client Components...`**: this means a Server Component is passing a function (including a component reference like a Lucide icon) as a prop into a `"use client"` component. Either render the element server-side and pass the resulting `ReactNode`, or move the function/reference so it originates inside the client component itself.
