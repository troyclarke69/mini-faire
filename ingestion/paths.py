from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = PROJECT_ROOT / "contracts"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
DUCKDB_PATH = WAREHOUSE_DIR / "mini_faire.duckdb"

