from __future__ import annotations

from pathlib import Path

import duckdb

from ingestion.paths import DUCKDB_PATH, PROJECT_ROOT, RAW_DIR


SQL_DIR = PROJECT_ROOT / "warehouse" / "duckdb"


def execute_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    con.execute(path.read_text(encoding="utf-8"))


def load_valid_json_to_staging(db_path: Path = DUCKDB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        execute_sql_file(con, SQL_DIR / "init.sql")
        con.execute("create schema if not exists raw")
        con.execute("create schema if not exists staging")
        con.execute("create schema if not exists marts")

        for table, pattern in {
            "raw_retailers": RAW_DIR / "batch" / "retailers" / "**" / "valid" / "*.json",
            "raw_products": RAW_DIR / "batch" / "products" / "**" / "valid" / "*.json",
            "raw_orders": RAW_DIR / "batch" / "orders" / "**" / "valid" / "*.json",
            "raw_order_created_events": RAW_DIR / "events" / "order_created" / "**" / "valid" / "*.json",
        }.items():
            con.execute(f"drop table if exists raw.{table}")
            glob_pattern = str(pattern).replace("\\", "/")
            con.execute(
                f"""
                create table raw.{table} as
                select * from read_json_auto('{glob_pattern}', union_by_name=true)
                """
            )


def run_elt(db_path: Path = DUCKDB_PATH) -> None:
    """Run staging rebuilds, incremental marts, and metric view refreshes."""
    with duckdb.connect(str(db_path)) as con:
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
