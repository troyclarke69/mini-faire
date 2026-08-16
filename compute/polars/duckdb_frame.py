from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from ingestion.paths import DUCKDB_PATH


def read_duckdb_frame(sql: str, db_path: Path = DUCKDB_PATH) -> pl.DataFrame:
    with duckdb.connect(str(db_path), read_only=True) as con:
        result = con.execute(sql)
        return pl.DataFrame(
            result.fetchall(),
            schema=[column[0] for column in result.description],
            orient="row",
        )

