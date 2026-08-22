from __future__ import annotations

import glob as glob_module
from pathlib import Path

import duckdb

from ingestion.duckdb_utils import connect_with_retry
from ingestion.paths import DUCKDB_PATH, PROJECT_ROOT, RAW_DIR


SQL_DIR = PROJECT_ROOT / "warehouse" / "duckdb"

# Fallback column definitions used only when zero valid files exist yet for a given
# raw table (e.g. a fresh checkout before any ingestion has run for that entity/event
# type, or an event type nobody has produced data for yet). Types are loose
# (varchar/double) because every downstream staging SQL file applies its own explicit
# ::type casts regardless of what lands here - this only exists so `create table ...
# as select from read_json_auto(...)` never has to run against zero files, which
# DuckDB treats as an error rather than an empty result.
EMPTY_TABLE_COLUMNS: dict[str, str] = {
    "raw_retailers": (
        "retailer_id varchar, name varchar, country varchar, category varchar, "
        "signup_date varchar, status varchar"
    ),
    "raw_products": (
        "product_id varchar, brand_id varchar, name varchar, category varchar, "
        "unit_price double, unit_cost double, inventory_count integer, is_active boolean"
    ),
    "raw_orders": (
        "order_id varchar, retailer_id varchar, product_id varchar, order_ts varchar, "
        "quantity integer, gross_amount double, discount_amount double, status varchar"
    ),
    "raw_order_created_events": (
        "event_id varchar, event_type varchar, event_ts varchar, order_id varchar, "
        "retailer_id varchar, product_id varchar, quantity integer, gross_amount double"
    ),
    "raw_order_paid_events": (
        "event_id varchar, event_type varchar, order_id varchar, amount double, event_ts varchar"
    ),
    "raw_orders_shipped_events": (
        "event_id varchar, event_type varchar, order_id varchar, carrier varchar, event_ts varchar"
    ),
    "raw_inventory_updated_events": (
        "event_id varchar, event_type varchar, product_id varchar, delta integer, "
        "inventory_count_after integer, event_ts varchar"
    ),
    "raw_price_changed_events": (
        "event_id varchar, event_type varchar, product_id varchar, old_price double, "
        "new_price double, event_ts varchar"
    ),
}

# Each raw table can be sourced from more than one raw zone: the batch/event zone
# (data/raw/batch/<entity>/... or data/raw/events/<event_type>/...) written by
# batch_ingestion.py / event_ingestion.py, and the flatter Mongo-sourced zone
# (data/raw/<entity>/<run_id>/valid/*.json) written by ingestion/mongo_ingest.py.
# Both land valid records under a `valid/` folder via the same validate+quarantine
# contract (ingestion/validate.py, ingestion/quarantine.py), so they union cleanly.
RAW_TABLE_SOURCES: dict[str, list[Path]] = {
    "raw_retailers": [
        RAW_DIR / "batch" / "retailers" / "**" / "valid" / "*.json",
        RAW_DIR / "retailers" / "**" / "valid" / "*.json",
    ],
    "raw_products": [
        RAW_DIR / "batch" / "products" / "**" / "valid" / "*.json",
        RAW_DIR / "products" / "**" / "valid" / "*.json",
    ],
    "raw_orders": [
        RAW_DIR / "batch" / "orders" / "**" / "valid" / "*.json",
        RAW_DIR / "orders" / "**" / "valid" / "*.json",
    ],
    "raw_order_created_events": [
        RAW_DIR / "events" / "order_created" / "**" / "valid" / "*.json",
        RAW_DIR / "order_created" / "**" / "valid" / "*.json",
    ],
    "raw_order_paid_events": [
        RAW_DIR / "events" / "order_paid" / "**" / "valid" / "*.json",
        RAW_DIR / "order_paid" / "**" / "valid" / "*.json",
    ],
    "raw_orders_shipped_events": [
        RAW_DIR / "events" / "orders_shipped" / "**" / "valid" / "*.json",
        RAW_DIR / "orders_shipped" / "**" / "valid" / "*.json",
    ],
    "raw_inventory_updated_events": [
        RAW_DIR / "events" / "inventory_updated" / "**" / "valid" / "*.json",
        RAW_DIR / "inventory_updated" / "**" / "valid" / "*.json",
    ],
    # price_changed gained a Mongo collection mapping in Phase 4 (config/mongo.yaml),
    # so - like every other entity above - it can now be sourced from either the
    # batch/event zone or the flatter Mongo-sourced zone.
    "raw_price_changed_events": [
        RAW_DIR / "events" / "price_changed" / "**" / "valid" / "*.json",
        RAW_DIR / "price_changed" / "**" / "valid" / "*.json",
    ],
}


def execute_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    con.execute(path.read_text(encoding="utf-8"))


def _resolve_files(patterns: list[Path]) -> list[str]:
    """Expand every glob pattern in Python first so we know up front whether any
    files actually exist - DuckDB's read_json_auto errors on a pattern with zero
    matches rather than returning an empty table."""
    seen: set[str] = set()
    resolved: list[str] = []
    for pattern in patterns:
        for file_path in glob_module.glob(str(pattern), recursive=True):
            normalized = file_path.replace("\\", "/")
            if normalized not in seen:
                seen.add(normalized)
                resolved.append(normalized)
    return resolved


def load_valid_json_to_staging(db_path: Path = DUCKDB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_with_retry(db_path) as con:
        execute_sql_file(con, SQL_DIR / "init.sql")
        con.execute("create schema if not exists raw")
        con.execute("create schema if not exists staging")
        con.execute("create schema if not exists marts")

        for table, patterns in RAW_TABLE_SOURCES.items():
            con.execute(f"drop table if exists raw.{table}")
            files = _resolve_files(patterns)
            if files:
                file_list = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
                con.execute(
                    f"""
                    create table raw.{table} as
                    select * from read_json_auto([{file_list}], union_by_name=true)
                    """
                )
            else:
                con.execute(f"create table raw.{table} ({EMPTY_TABLE_COLUMNS[table]})")


def run_elt(db_path: Path = DUCKDB_PATH) -> None:
    """Run staging rebuilds, incremental marts, and metric view refreshes."""
    with connect_with_retry(db_path) as con:
        for folder in ("staging", "models", "metrics"):
            for path in sorted((SQL_DIR / folder).glob("*.sql")):
                execute_sql_file(con, path)


def rebuild_warehouse(db_path: Path = DUCKDB_PATH) -> None:
    """Refresh raw/staging sources and apply incremental ELT into marts."""
    load_valid_json_to_staging(db_path)
    run_elt(db_path)


if __name__ == "__main__":
    rebuild_warehouse()
    print(f"Built {DUCKDB_PATH}")
