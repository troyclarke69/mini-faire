# Deploying RMAP: Fly.io (backend) + Netlify (frontend)

This is the step-by-step guide for taking RMAP from "runs locally against `.\.venv\Scripts\python.exe`" to a real, reachable v1 deployment: the FastAPI backend on Fly.io, the Next.js frontend on Netlify. It assumes you've already got the app running locally per the README's Quick Start.

**Naming note.** The product is branded **RMAP** in this guide and in the UI; the repository and Python package underneath are still named `mini-faire`/`mini_faire` (see [MASTER_GUIDE.md](MASTER_GUIDE.md) for the same note in more detail). Fly.io resource names below use `rmap-*` to match the brand — that's a naming choice, not a different codebase.

This guide focuses on the two platforms you named. `infra/cloud/` also has manifests for Render, Azure Container Apps, and a from-scratch Terraform/AWS setup, plus a generic `docker-compose.cloud.yaml` for a single-host deploy — none of those are covered here, but the same considerations in Part 1 below apply to all of them.

---

## Part 1: What you're actually deploying (read this first)

This section is the "considerations" — the things that aren't obvious from the individual scripts, and that would otherwise bite you mid-deploy.

**1. Only the API server runs by default.** `infra/cloud/fly.toml` deploys exactly one process: `uvicorn api.metrics_api:app`. None of the orchestration scripts (`realtime_flow.py`, `synthetic_flow.py`, `ml_training_flow.py`, `simulation_flow.py`, `agent_flow.py`, etc.) run automatically just because the backend is deployed — same as locally, each is a script you choose to run. Part 4 below covers exactly how to run them once deployed.

**2. The DuckDB warehouse is one file, and Fly volumes are single-node.** `ingestion/duckdb_utils.py` already documents DuckDB as single-writer; a Fly Volume is regional block storage attached to exactly one Machine. `fly.toml` already reflects this: `min_machines_running = 1`, no autoscaling beyond one machine. **Do not** raise the machine count for the backend app — a second machine would mean a second, disconnected copy of the volume, not a shared one. If you outgrow this, `config/database.yaml`'s Postgres backend is the documented escape hatch (see `database/cloud_db.py`), not more Fly machines.

**3. The hand-written 4-day sample dataset is not in the Docker image.** `scripts/run_demo.py` and its underlying `ingest_all_batches()`/`ingest_all_events()` read from `data/batch/` and `data/events/` — the sample JSON files checked into the repo. `infra/cloud/Dockerfile.backend` never copies `data/` into the image (by design — `data/` is a runtime volume, not a build input), so **a freshly deployed backend starts with a completely empty warehouse**. Don't expect `scripts/run_demo.py`'s sample data to just be there; see Part 3, step 5 for how to seed it instead.

**4. `scripts/*.py` aren't in any Docker image at all.** `run_demo.py`, `seed_demo_tenant.py`, and `seed_tenant_orders.py` are local-dev convenience scripts — none of the `infra/cloud/Dockerfile.*` files copy `scripts/`. Once deployed, use the `orchestration.*_flow` module entry points instead (all of `orchestration/` **is** copied into the backend and orchestration images). If you want the seeded demo login in production anyway, see the callout in step 5c below.

**5. The seeded demo login is explicitly not a production pattern.** `scripts/seed_demo_tenant.py`'s own docstring says so directly: it publishes a fixed, known password. That's fine for a local demo; it is not something to run against your production database. Real users should go through RMAP's normal `/signup` flow (`POST /auth/signup`, or the `/signup` page in the UI).

**6. "Continuous" vs. "one-shot" scripts matter for how you run them.** `realtime_flow.py` and `synthetic/stream_generator.py` are long-lived processes ("runs continuously until interrupted" — README's own words). Everything else in `orchestration/` (`synthetic_flow.py`, `mongo_flow.py`, `ml_training_flow.py`, `ml_inference_flow.py`, `simulation_flow.py`, `agent_flow.py`) is a one-shot script that runs to completion and exits — exactly like running it locally. This distinction drives which SSH pattern you use in Part 4.

**7. CORS has to be told about your real frontend domain.** `api/metrics_api.py` only allows `localhost`/`127.0.0.1` origins by default (that's all local dev needs). Once the frontend is on a real Netlify domain, the browser's direct WebSocket/SSE/auth calls to the backend (Live Mode, login) will be blocked by CORS until you set `CORS_ALLOWED_ORIGINS` — covered in Part 3, step 6, after you know your Netlify URL.

**8. Two things were fixed as part of getting this deployment-ready**, both real gaps that predate Phase 8/9 and were never caught because nothing had actually build-tested these images end to end:
   - `infra/cloud/Dockerfile.backend` and `Dockerfile.orchestration` were missing `COPY autonomy/` and `COPY simulation/`. `api/metrics_api.py` mounts `api/autonomy_api.py` and `api/simulation_api.py`, both of which import directly from those two packages — without the fix, the backend container would crash with `ModuleNotFoundError` the moment it started.
   - `pyproject.toml`'s `[tool.setuptools.packages.find]` include list was missing `"autonomy*"` (the same gap it already documents having happened for `simulation*` in Phase 8). Fixed alongside the Dockerfile changes.

   Both are already fixed in this checkout — mentioned here so you know why, not as something you still need to do.

**9. The frontend's Docker build output and Netlify's build output are different, on purpose.** `frontend/next.config.mjs` only sets `output: "standalone"` (needed for `Dockerfile.frontend`) when `NEXT_OUTPUT_STANDALONE=true` is set, which only that Dockerfile sets. Netlify's own build never sets it, so the same `next.config.mjs` produces the right output for both paths — nothing you need to configure, just don't remove the conditional.

---

## Part 2: Prerequisites

- A Fly.io account and the `flyctl` CLI (`fly auth login` once installed).
- A Netlify account, with this GitHub repository ([github.com/troyclarke69/mini-faire](https://github.com/troyclarke69/mini-faire)) accessible to it.
- A **new** JWT signing secret for production — don't reuse whatever `JWT_SECRET_KEY` you're using locally (see `DoNOTpUSH.txt`, which is already gitignored and should stay purely local). Generate one with:
  ```bash
  openssl rand -base64 32
  ```
- Optional, only if you're using these features: a `MONGO_PASSWORD` (MongoDB Atlas), `SLACK_WEBHOOK_URL` (alert delivery), `POSTGRES_PASSWORD` (if you've turned on `config/database.yaml`'s Postgres backend). Local defaults for all three are "off" — skip anything you're not using.

---

## Part 3: Deploy the backend to Fly.io

All commands run from the repository root.

**1. Create the app.**
```bash
fly apps create rmap-backend
```
`infra/cloud/fly.toml` already names the app `rmap-backend` and pins `primary_region = "iad"`. Fly app names are globally unique across every Fly customer — if `rmap-backend` is taken, pick another name and update `app = "..."` in `infra/cloud/fly.toml` to match before continuing.

**2. Create the volume** that will hold the DuckDB warehouse (`data/warehouse/mini_faire.duckdb`), matching `fly.toml`'s `[[mounts]]` block (`source = "rmap_data"`, mounted at `/app/data`):
```bash
fly volumes create rmap_data --app rmap-backend --region iad --size 1
```
1 GB is comfortable for a demo-scale synthetic dataset; resize later with `fly volumes extend` if you need more.

**3. Set secrets** (never commit these — same posture `config/*.yaml` already takes locally with env vars):
```bash
fly secrets set --app rmap-backend JWT_SECRET_KEY="<the value you generated above>"

# Only if you use these:
fly secrets set --app rmap-backend MONGO_PASSWORD="..."
fly secrets set --app rmap-backend SLACK_WEBHOOK_URL="..."
fly secrets set --app rmap-backend POSTGRES_PASSWORD="..."
```
Leave `CORS_ALLOWED_ORIGINS` for step 6 — you don't know your Netlify URL yet.

**4. Deploy:**
```bash
fly deploy --config infra/cloud/fly.toml
```
No `--dockerfile` flag — `infra/cloud/fly.toml`'s own `[build]` section already declares it. See the first entry in Troubleshooting below if you get a "Dockerfile not found" error with a doubled path (most often from adding `--dockerfile infra/cloud/Dockerfile.backend` back in by hand).

**5. Verify and seed data.**
```bash
curl https://rmap-backend.fly.dev/health
# -> {"status":"ok"}
```
The warehouse is empty at this point (see Part 1, point 3). Seed it with the synthetic generator — a one-shot script, so `fly ssh console -C` is the right tool (it runs the command and exits):
```bash
fly ssh console --app rmap-backend -C "python -m orchestration.synthetic_flow"
```
This writes a full deterministic synthetic dataset (retailers, products, orders, the full event chain, price changes, inventory volatility — `config/synthetic.yaml` controls the shape) straight through ingestion → warehouse → compute, onto the mounted volume. Re-run it any time you want more data; it's additive, same as running it locally.

Optionally, layer on the later phases' data the same way — each of these is also one-shot and safe with `fly ssh console -C`:
```bash
fly ssh console --app rmap-backend -C "python orchestration/ml_training_flow.py"
fly ssh console --app rmap-backend -C "python orchestration/ml_inference_flow.py"
fly ssh console --app rmap-backend -C "python orchestration/simulation_flow.py"
fly ssh console --app rmap-backend -C "python orchestration/agent_flow.py"
```
Order matters a little: run `ml_training_flow.py` before `ml_inference_flow.py` (inference needs a registered model to load), and both before `agent_flow.py`/`simulation_flow.py` if you want the digital twin to reflect real forecasts/clusters rather than an empty `ml.*` schema (they degrade gracefully to an empty result either way — nothing errors, the dashboards just have less to show).

**5c. Skip the seeded demo login.** Per Part 1, point 5, don't run `seed_demo_tenant.py` against production (it isn't even in the image). If you want a quick way in for yourself, sign up normally: `POST /auth/signup` via the `/signup` page once the frontend is live, or directly against the API:
```bash
curl -X POST https://rmap-backend.fly.dev/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"a-real-password","tenant_name":"My Workspace"}'
```

**6. Update CORS once the frontend has a real URL** — come back to this after Part 4:
```bash
fly secrets set --app rmap-backend CORS_ALLOWED_ORIGINS="https://<your-site-name>.netlify.app"
```
`fly secrets set` redeploys the app automatically to pick up the new value. Multiple origins are comma-separated (e.g. if you later add a custom domain): `CORS_ALLOWED_ORIGINS="https://rmap.netlify.app,https://rmap.yourdomain.com"`.

---

## Part 4: Deploy the frontend to Netlify

**1. Connect the repository.** In the Netlify dashboard: **Add new site → Import an existing project**, and point it at [github.com/troyclarke69/mini-faire](https://github.com/troyclarke69/mini-faire). Netlify reads `netlify.toml` at the repo root (`base = "frontend"`), which now also explicitly declares the Next.js Runtime (`[[plugins]] package = "@netlify/plugin-nextjs"`) — nothing to configure by hand. That plugin is what turns `.next/`'s build output into servable routes; without it, `npm run build` (plain `next build`) still succeeds, but nothing gets published or routed, and the site 404s everywhere with no error in the build log — a real deploy of this repo hit exactly that (Site configuration showed "Runtime: Not set", "Publish directory: Not set"), which is why the plugin is now pinned explicitly here instead of left to auto-detection.

**After connecting, verify the Runtime actually took.** Site configuration → Build & deploy → Build settings should show **Runtime: Next.js** once a deploy has run with the `[[plugins]]` block above. If it still shows "Not set" after a deploy, use **Trigger deploy → Clear cache and deploy site** (not a plain retry) — a site that was first connected before this block existed can have a stale build cache that skips re-evaluating plugins.

**2. Set the API URL *before* the first build.** In **Site configuration → Environment variables**, add:
```
NEXT_PUBLIC_API_URL = https://rmap-backend.fly.dev
```
Next.js inlines `NEXT_PUBLIC_*` variables into the client bundle at build time — set this before the first deploy, or trigger a fresh deploy after adding it (a later request-time change won't retroactively apply to an already-built bundle).

**3. Deploy.** Netlify builds and gives you a URL — `https://<site-name>.netlify.app` by default, or a custom domain if you configure one.

**4. Go back to Part 3, step 6** and set `CORS_ALLOWED_ORIGINS` to this URL.

**5. Verify.** Open the Netlify URL. Dashboards (Retailer Daily, Product Velocity, Compute, ML, Simulation, Autonomy, the new Docs page) should load with whatever data you seeded in Part 3. Toggle **Live Mode** in the header and check the browser console — a clean WebSocket connection to `/realtime/ws` (no CORS error) confirms step 6 took effect. Live Mode "not having much new to show" is expected unless you've set up the continuous pipeline in Part 5 below — see Part 1, point 6.

---

## Part 5: Keeping data fresh in production

**Recommended default for v1: run things on demand.** Every one-shot script in Part 3, step 5 can be re-run anytime against the live volume the same way — `fly ssh console --app rmap-backend -C "..."`. This mirrors exactly how you already run things locally (opening a new terminal per script), just over SSH instead of PowerShell. No extra infrastructure, and it's the lowest-risk option for a v1.

**Advanced / optional: a continuously running pipeline.** If you want Live Mode to update on its own the way it does locally with `realtime_flow.py` running, be careful about *where* you run it: adding `realtime_flow.py` as a second Fly process group (a `[processes]` block in `fly.toml`) would put it on its **own separate Machine with its own separate volume** — Fly volumes are node-local and can't be shared across Machines, so that second process would silently be writing to a different, empty DuckDB file, not the one the API serves. That defeats the point.

The only way to keep one shared DuckDB file *and* run something continuously alongside the API is to run it **inside the same Machine** as the backend (same container, same mounted volume) — for example, backgrounding it over the same SSH session that's already attached to that Machine:
```bash
fly ssh console --app rmap-backend
# inside the session:
nohup python -m orchestration.realtime_flow > /tmp/realtime_flow.log 2>&1 &
disown
exit
```
This is a reasonable v1-scale way to keep the demo "live" — but it isn't supervised: a Fly deploy or a Machine restart kills that background process, and you'd need to SSH back in and restart it. A more durable version of this same idea (baking a small supervisor — e.g. `supercronic` for the one-shot jobs on a schedule, plus a background loop for `realtime_flow.py` — directly into `Dockerfile.backend`'s entrypoint) is a reasonable next step if the on-demand/manual pattern above starts feeling like too much upkeep, but it's a real change to the container's architecture, not something this guide makes for you.

---

## Secrets & environment variable checklist

| Variable | Where it's set | Required? |
|---|---|---|
| `JWT_SECRET_KEY` | `fly secrets set` (backend) | Yes — falls back to an insecure dev default with a loud warning otherwise (`config/auth.yaml`) |
| `CORS_ALLOWED_ORIGINS` | `fly secrets set` (backend) | Yes, once the frontend has a real domain (Part 3, step 6) |
| `NEXT_PUBLIC_API_URL` | Netlify env var (frontend) | Yes — the frontend has no other way to find the backend |
| `MONGO_PASSWORD` | `fly secrets set` (backend) | Only if using MongoDB as a source |
| `SLACK_WEBHOOK_URL` | `fly secrets set` (backend) | Only for Slack alert delivery |
| `POSTGRES_PASSWORD` | `fly secrets set` (backend) | Only if `config/database.yaml`'s Postgres backend is enabled |

---

## Troubleshooting

**`fly deploy` fails with `dockerfile '...\infra\cloud\infra\cloud\Dockerfile.backend' not found`** (a doubled path). `fly.toml`'s `[build] dockerfile` field is resolved relative to the directory *containing `fly.toml`* (`infra/cloud/`), not the repo root — even though the Docker build *context* stays at the repo root by default regardless of where `--config` points ([Fly's own configuration reference](https://fly.io/docs/reference/configuration/) confirms these are two different base directories). If that field (or a `--dockerfile` flag on the command line) is written as `"infra/cloud/Dockerfile.backend"`, it resolves to `infra/cloud/infra/cloud/Dockerfile.backend`. Fix: use a bare filename — `dockerfile = "Dockerfile.backend"` in `fly.toml`, and drop `--dockerfile` from the deploy command entirely (`fly.toml`'s own `[build]` section already declares it: `fly deploy --config infra/cloud/fly.toml`). Already fixed in this repo's own `fly.toml`/`fly.frontend.toml`/`infra/cloud/deploy.sh` — this note is here in case you're working from an older copy or pass `--dockerfile` by hand with the full path.

**Build context warning ("Build context is 1.0 GB across 39,442 files...").** This means `.dockerignore` isn't excluding `.venv/`, `frontend/node_modules/`, `data/`, etc. from what gets uploaded to the builder on every deploy — slower builds, not a failure. A root-level `.dockerignore` covering these is included in this repo; if you don't have it, add one (see `.dockerignore` at the repo root for the current list).

**Dashboards load but every table is empty.** You haven't seeded the volume yet — see Part 3, step 5. This is expected right after a fresh deploy (Part 1, point 3).

**Browser console shows a CORS error / Live Mode won't connect.** `CORS_ALLOWED_ORIGINS` isn't set, or doesn't exactly match your Netlify URL (scheme + host, no trailing slash). See Part 3, step 6.

**Backend crashes on startup with `ModuleNotFoundError`.** If you're building from a checkout older than this deployment pass, you're missing the `Dockerfile.backend`/`Dockerfile.orchestration`/`pyproject.toml` fixes described in Part 1, point 8 — pull the current versions of those three files.

**`fly deploy` succeeds but `/health` times out.** Check `fly logs --app rmap-backend` for the actual startup error — most often a missing secret (`JWT_SECRET_KEY`) or the volume not being attached (confirm with `fly volumes list --app rmap-backend`).

**A script run via `fly ssh console -C` says a table doesn't exist / returns nothing.** Most read paths in this codebase (`query_safe()` in `api/db.py`) degrade a missing table to an empty result rather than erroring — this usually just means an earlier stage in the pipeline hasn't run yet (e.g. running `agent_flow.py` before ever running `synthetic_flow.py`). Run the stages in the order shown in Part 3, step 5.
