from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = PROJECT_ROOT / "contracts"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
DUCKDB_PATH = WAREHOUSE_DIR / "mini_faire.duckdb"
# Small JSON state files for the Phase 4 streaming services (heartbeats,
# resume tokens) - see ingestion/heartbeat.py.
STATE_DIR = DATA_DIR / "state"
# Phase 6 (PHASE6-ML.md Section 1): pickled model artifacts for ml/registry.py.
# Registry rows store metadata/params/metrics in DuckDB (ml.model_registry);
# the fitted estimator itself is pickled here, one file per model_id, so
# orchestration/ml_inference_flow.py can load the active model without
# refitting it on every inference pass.
ML_MODELS_DIR = DATA_DIR / "ml_models"
# Phase 7 (PHASE7-DEPLOYMENT.md Section 2): tenant-scoped raw zone.
# ingestion/tenant_ingest.py writes each tenant's validated/quarantined
# records under TENANTS_RAW_DIR / <tenant_id> / ... - the same shape as
# RAW_DIR's batch/events layout, just nested one level deeper by tenant so a
# tenant's raw files are physically separable (for storage/cloud_storage.py's
# per-tenant prefix, export, and retention operations) even though the
# DuckDB warehouse itself uses row-level tenant_id filtering rather than a
# fully mirrored per-tenant table set - see multi_tenant/tenant_manager.py's
# module docstring for the pooled-vs-silo isolation policy this reflects.
TENANTS_RAW_DIR = RAW_DIR / "tenants"

