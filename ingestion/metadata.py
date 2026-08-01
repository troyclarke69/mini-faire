from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from ingestion.paths import DUCKDB_PATH


@dataclass(frozen=True)
class IngestionRun:
    run_id: str
    source: str
    entity: str
    file_name: str
    source_path: str
    source_content_sha256: str
    partition_path: str
    contract_name: str
    valid_count: int
    invalid_count: int
    schema_version: str
    valid_path: str
    quarantine_path: str
    metadata_path: str
    started_at: str
    completed_at: str
    duration_ms: int
    status: str


@dataclass(frozen=True)
class LineageEdge:
    run_id: str
    source_node: str
    target_node: str
    edge_type: str
    entity: str
    created_at: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def duration_ms(started_at: str, completed_at: str) -> int:
    delta = parse_utc(completed_at) - parse_utc(started_at)
    return int(delta.total_seconds() * 1000)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_metadata(path: Path, run: IngestionRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(run), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upsert_ingestion_run(run: IngestionRun, db_path: Path = DUCKDB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            create table if not exists ingestion_runs (
              run_id varchar primary key,
              source varchar,
              entity varchar,
              file_name varchar,
              source_path varchar,
              source_content_sha256 varchar,
              partition_path varchar,
              contract_name varchar,
              valid_count integer,
              invalid_count integer,
              schema_version varchar,
              valid_path varchar,
              quarantine_path varchar,
              metadata_path varchar,
              started_at timestamptz,
              completed_at timestamptz,
              duration_ms integer,
              status varchar
            )
            """
        )
        existing_columns = {
            row[1] for row in con.execute("pragma table_info('ingestion_runs')").fetchall()
        }
        expected_columns = {
            "source_path": "varchar",
            "source_content_sha256": "varchar",
            "partition_path": "varchar",
            "contract_name": "varchar",
            "valid_path": "varchar",
            "quarantine_path": "varchar",
            "metadata_path": "varchar",
            "duration_ms": "integer",
        }
        for column_name, column_type in expected_columns.items():
            if column_name not in existing_columns:
                con.execute(f"alter table ingestion_runs add column {column_name} {column_type}")

        con.execute(
            """
            insert or replace into ingestion_runs (
              run_id,
              source,
              entity,
              file_name,
              source_path,
              source_content_sha256,
              partition_path,
              contract_name,
              valid_count,
              invalid_count,
              schema_version,
              valid_path,
              quarantine_path,
              metadata_path,
              started_at,
              completed_at,
              duration_ms,
              status
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run.run_id,
                run.source,
                run.entity,
                run.file_name,
                run.source_path,
                run.source_content_sha256,
                run.partition_path,
                run.contract_name,
                run.valid_count,
                run.invalid_count,
                run.schema_version,
                run.valid_path,
                run.quarantine_path,
                run.metadata_path,
                run.started_at,
                run.completed_at,
                run.duration_ms,
                run.status,
            ],
        )


def upsert_lineage_edges(edges: list[LineageEdge], db_path: Path = DUCKDB_PATH) -> None:
    if not edges:
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            create table if not exists lineage_edges (
              run_id varchar,
              source_node varchar,
              target_node varchar,
              edge_type varchar,
              entity varchar,
              created_at timestamptz,
              primary key (run_id, source_node, target_node, edge_type)
            )
            """
        )
        con.executemany(
            """
            insert or replace into lineage_edges
            values (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    edge.run_id,
                    edge.source_node,
                    edge.target_node,
                    edge.edge_type,
                    edge.entity,
                    edge.created_at,
                )
                for edge in edges
            ],
        )
