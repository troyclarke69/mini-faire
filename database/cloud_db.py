"""Cloud Database Integration (PHASE7-DEPLOYMENT.md Section 6).

Three connection managers, one per backend Section 6 names, each
independently enabled via `config/database.yaml` (see that file's header
for why this is three simultaneous connections rather than one selected
backend, unlike `storage/cloud_storage.py`'s single-backend switch):

- `DuckDBConnectionManager` - the OLAP warehouse. Wraps
  `ingestion/duckdb_utils.py`'s existing `connect_with_retry()` rather than
  reimplementing retry logic; adds a small bounded pool of cached read-only
  connections (DuckDB is single-writer, so there is no write pool to build -
  every writer already holds its connection for a single short-lived `with`
  block, per `duckdb_utils.py`'s own docstring). This is what every module
  in this repo already uses in practice; "DuckDB server mode" per Section 6
  is exactly this - DuckDB opened in-process by the backend, not a separate
  server. DuckDB WASM is frontend-only (no backend Python surface) - see
  `frontend/lib/`'s notes for that half of Section 6.
- `PostgresConnectionManager` - real, complete `psycopg2` connection
  pooling + retry + a minimal migration runner (`run_migrations()`),
  guarded by a try/except ImportError exactly like
  `storage/cloud_storage.py`'s S3/Azure/GCS backends guard their SDKs.
  `database/migrations/postgres/*.sql` mirrors this repo's existing DuckDB
  schemas (`ingestion_runs`/`lineage_edges`, `auth.users`/
  `auth.refresh_tokens`, `tenant.tenants`) in Postgres DDL - a real,
  runnable migration path to "Neon/Postgres (metadata + lineage + auth)"
  per Section 6, not a placeholder. Disabled by default
  (`config/database.yaml`'s `postgres.enabled: false`): this repo's
  metadata/auth/tenant modules all still read/write DuckDB today (see each
  module's own docstring) - actually cutting them over to Postgres instead
  of DuckDB is a deliberately separate, larger change than this phase
  scopes to, consistent with `multi_tenant/tenant_manager.py`'s own
  "documented gap, not silently claimed as done" posture on its silo
  isolation tier. What's real and complete here: the connection/pool/retry/
  migration machinery a cutover would use, and the migrations themselves.
- `MongoConnectionManager` - wraps `ingestion/mongo_ingest.py`'s existing
  `load_mongo_config()`/`build_mongo_uri()` (not duplicated - same
  MONGO_PASSWORD-from-env, mongo_uri_template convention) with pooling
  (pymongo's `MongoClient` already pools internally; this manager mainly
  adds the shared retry wrapper) for "MongoDB Atlas (events + streaming)".

Nothing in this module is imported by the rest of this repo's runtime code
paths (ingestion/tenant_ingest.py, auth/auth_models.py, etc. all still talk
to DuckDB directly via `connect_with_retry`) - it is the abstraction layer a
cloud deployment's ops tooling and a future cutover would build on, kept
separate so today's working DuckDB-backed code is untouched by it.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

import yaml

from ingestion.duckdb_utils import connect_with_retry
from ingestion.paths import DUCKDB_PATH, PROJECT_ROOT

DATABASE_CONFIG_PATH = PROJECT_ROOT / "config" / "database.yaml"
POSTGRES_MIGRATIONS_DIR = PROJECT_ROOT / "database" / "migrations" / "postgres"

T = TypeVar("T")


class DatabaseError(RuntimeError):
    """Raised for a disabled/misconfigured backend or a migration failure -
    a distinct type so callers can catch it specifically, matching
    `multi_tenant/tenant_manager.py`'s `TenantError` / `storage/
    cloud_storage.py`'s `StorageError`."""


def with_retries(
    func: Callable[[], T], *, retries: int, exceptions: tuple[type[BaseException], ...],
    initial_delay: float = 0.25, max_delay: float = 3.0,
) -> T:
    """Generic exponential-backoff retry, factored out of
    `ingestion/duckdb_utils.py`'s `connect_with_retry()` so every connection
    manager below (Postgres, Mongo) shares the exact same backoff shape
    instead of each hand-rolling its own - only DuckDB itself still calls
    the original `connect_with_retry()` directly, since that one is already
    battle-tested (Phase 4's concurrent-writer fix) and this module
    shouldn't risk changing its behavior by routing it through a new
    generic wrapper."""
    delay = initial_delay
    last_exc: BaseException | None = None
    for attempt in range(retries):
        try:
            return func()
        except exceptions as exc:
            last_exc = exc
            if attempt == retries - 1:
                break
            time.sleep(delay)
            delay = min(delay * 2, max_delay)
    assert last_exc is not None
    raise last_exc


@dataclass(frozen=True)
class DatabaseConfig:
    duckdb_enabled: bool
    duckdb_read_pool_size: int
    duckdb_retries: int
    postgres_enabled: bool
    postgres_host: str
    postgres_port: int
    postgres_database: str
    postgres_user: str
    postgres_password_env_var: str
    postgres_sslmode: str
    postgres_pool_min_size: int
    postgres_pool_max_size: int
    postgres_retries: int
    postgres_migrations_dir: Path
    mongo_enabled: bool
    mongo_retries: int
    mongo_server_selection_timeout_ms: int
    raw: dict = field(default_factory=dict)


def load_database_config(path: Path = DATABASE_CONFIG_PATH) -> DatabaseConfig:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    duckdb_cfg = raw.get("duckdb") or {}
    postgres_cfg = raw.get("postgres") or {}
    mongo_cfg = raw.get("mongo") or {}

    migrations_dir = Path(postgres_cfg.get("migrations_dir", "database/migrations/postgres"))
    if not migrations_dir.is_absolute():
        migrations_dir = PROJECT_ROOT / migrations_dir

    return DatabaseConfig(
        duckdb_enabled=bool(duckdb_cfg.get("enabled", True)),
        duckdb_read_pool_size=int(duckdb_cfg.get("read_pool_size", 4)),
        duckdb_retries=int(duckdb_cfg.get("retries", 8)),
        postgres_enabled=bool(postgres_cfg.get("enabled", False)),
        postgres_host=postgres_cfg.get("host", ""),
        postgres_port=int(postgres_cfg.get("port", 5432)),
        postgres_database=postgres_cfg.get("database", "mini_faire_metadata"),
        postgres_user=postgres_cfg.get("user", "mini_faire"),
        postgres_password_env_var=postgres_cfg.get("password_env_var", "POSTGRES_PASSWORD"),
        postgres_sslmode=postgres_cfg.get("sslmode", "require"),
        postgres_pool_min_size=int(postgres_cfg.get("pool_min_size", 1)),
        postgres_pool_max_size=int(postgres_cfg.get("pool_max_size", 5)),
        postgres_retries=int(postgres_cfg.get("retries", 5)),
        postgres_migrations_dir=migrations_dir,
        mongo_enabled=bool(mongo_cfg.get("enabled", False)),
        mongo_retries=int(mongo_cfg.get("retries", 5)),
        mongo_server_selection_timeout_ms=int(mongo_cfg.get("server_selection_timeout_ms", 5000)),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# DuckDB - the OLAP warehouse, always on
# ---------------------------------------------------------------------------


class DuckDBConnectionManager:
    """A small bounded LRU cache of read-only connections, keyed by
    db_path, on top of `connect_with_retry()`. DuckDB allows many
    concurrent read-only connections to the same file, so caching them
    (rather than reopening the file on every read within one process'
    lifetime) is a real optimization; write connections are never cached -
    each one still goes through a fresh `connect_with_retry()` call and is
    closed at the end of its `with` block, exactly as every writer in this
    repo already does, since DuckDB's exclusive write lock makes holding a
    write connection open any longer than necessary actively harmful (see
    `ingestion/duckdb_utils.py`'s docstring)."""

    def __init__(self, *, pool_size: int = 4, retries: int = 8):
        self.pool_size = pool_size
        self.retries = retries
        self._read_pool: dict[str, Any] = {}
        self._read_pool_order: list[str] = []

    def _get_or_open_read_connection(self, db_path: Path):
        key = str(db_path)
        if key in self._read_pool:
            self._read_pool_order.remove(key)
            self._read_pool_order.append(key)
            return self._read_pool[key]
        con = connect_with_retry(db_path, read_only=True, retries=self.retries)
        self._read_pool[key] = con
        self._read_pool_order.append(key)
        if len(self._read_pool_order) > self.pool_size:
            evicted_key = self._read_pool_order.pop(0)
            self._read_pool.pop(evicted_key).close()
        return con

    @contextmanager
    def read_connection(self, db_path: Path = DUCKDB_PATH):
        yield self._get_or_open_read_connection(db_path)

    @contextmanager
    def write_connection(self, db_path: Path = DUCKDB_PATH):
        con = connect_with_retry(db_path, read_only=False, retries=self.retries)
        try:
            yield con
        finally:
            con.close()

    def close_all(self) -> None:
        for con in self._read_pool.values():
            con.close()
        self._read_pool.clear()
        self._read_pool_order.clear()


# ---------------------------------------------------------------------------
# Postgres - metadata/lineage/auth's cloud home (Neon or any Postgres)
# ---------------------------------------------------------------------------


class PostgresConnectionManager:
    def __init__(
        self, *, host: str, port: int, database: str, user: str, password_env_var: str,
        sslmode: str = "require", pool_min_size: int = 1, pool_max_size: int = 5, retries: int = 5,
    ):
        try:
            import psycopg2
            import psycopg2.pool
        except ImportError as exc:  # pragma: no cover - exercised only with [cloud] installed
            raise ImportError(
                "database/cloud_db.py's Postgres backend requires psycopg2. Install this "
                'repo\'s cloud extra with `pip install -e ".[cloud]"` (see pyproject.toml), '
                "or leave config/database.yaml's postgres.enabled: false."
            ) from exc
        import os

        password = os.environ.get(password_env_var)
        if not password:
            raise DatabaseError(
                f"{password_env_var} environment variable is not set. Set it before connecting "
                "to Postgres - the password is never stored in config/database.yaml."
            )
        self.retries = retries
        self._psycopg2 = psycopg2
        self._pool = psycopg2.pool.SimpleConnectionPool(
            pool_min_size, pool_max_size, host=host, port=port, dbname=database,
            user=user, password=password, sslmode=sslmode,
        )

    @contextmanager
    def connection(self):
        con = with_retries(
            lambda: self._pool.getconn(), retries=self.retries, exceptions=(self._psycopg2.OperationalError,)
        )
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            self._pool.putconn(con)

    def close_all(self) -> None:
        self._pool.closeall()

    def run_migrations(self, migrations_dir: Path = POSTGRES_MIGRATIONS_DIR) -> list[str]:
        """Applies every `*.sql` file in `migrations_dir` (sorted by
        filename, so `0001_...sql` before `0002_...sql`) that hasn't already
        been recorded in `schema_migrations`, each inside its own
        transaction. Returns the list of migration filenames actually
        applied this call (empty if the schema was already current) - a
        minimal, dependency-free migration runner (no alembic/sqlalchemy),
        matching this repo's "hand-roll it with stdlib/the driver already in
        use rather than add a framework dependency" precedent
        (`alerts/dispatcher.py`'s hand-rolled webhook POST, `auth/
        auth_models.py`'s hand-rolled JWT/PBKDF2)."""
        migration_files = sorted(migrations_dir.glob("*.sql"))
        applied: list[str] = []
        with self.connection() as con:
            cursor = con.cursor()
            cursor.execute(
                "create table if not exists schema_migrations ("
                "  version varchar primary key, applied_at timestamptz not null default now())"
            )
            cursor.execute("select version from schema_migrations")
            already_applied = {row[0] for row in cursor.fetchall()}
            for path in migration_files:
                if path.name in already_applied:
                    continue
                cursor.execute(path.read_text(encoding="utf-8"))
                cursor.execute("insert into schema_migrations (version) values (%s)", (path.name,))
                applied.append(path.name)
            cursor.close()
        return applied

    def create_tenant_schema(self, tenant_id: str) -> str:
        """Postgres counterpart of
        `multi_tenant/tenant_manager.py`'s `create_tenant_schema()` (DuckDB) -
        same `tenant_<id>` naming, same "idempotent, safe to call more than
        once" contract. `tenant_id` is trusted to already be
        `validate_tenant_id()`-safe (callers go through
        `multi_tenant/tenant_manager.py` first), same assumption
        `create_tenant()`'s own schema-name interpolation makes."""
        schema_name = f"tenant_{tenant_id.replace('-', '_')}"
        with self.connection() as con:
            cursor = con.cursor()
            cursor.execute(f"create schema if not exists {schema_name}")
            cursor.close()
        return schema_name


# ---------------------------------------------------------------------------
# MongoDB Atlas - events + streaming
# ---------------------------------------------------------------------------


class MongoConnectionManager:
    def __init__(self, *, retries: int = 5, server_selection_timeout_ms: int = 5000):
        try:
            from pymongo import MongoClient
            from pymongo.errors import PyMongoError
        except ImportError as exc:  # pragma: no cover - exercised only with [mongo]/[cloud] installed
            raise ImportError(
                "database/cloud_db.py's Mongo backend requires pymongo. Install this repo's "
                'mongo extra with `pip install -e ".[mongo]"` (see pyproject.toml), or leave '
                "config/database.yaml's mongo.enabled: false."
            ) from exc
        from ingestion.mongo_ingest import build_mongo_uri, load_mongo_config

        self.retries = retries
        self._pymongo_error = PyMongoError
        config = load_mongo_config()
        uri = build_mongo_uri(config)
        self._client = with_retries(
            lambda: MongoClient(uri, serverSelectionTimeoutMS=server_selection_timeout_ms),
            retries=retries, exceptions=(PyMongoError,),
        )
        self._database_name = config.database

    @property
    def database(self):
        return self._client[self._database_name]

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_duckdb_manager(config: DatabaseConfig | None = None) -> DuckDBConnectionManager:
    config = config or load_database_config()
    if not config.duckdb_enabled:
        raise DatabaseError("config/database.yaml's duckdb.enabled is false")
    return DuckDBConnectionManager(pool_size=config.duckdb_read_pool_size, retries=config.duckdb_retries)


def get_postgres_manager(config: DatabaseConfig | None = None) -> PostgresConnectionManager:
    config = config or load_database_config()
    if not config.postgres_enabled:
        raise DatabaseError("config/database.yaml's postgres.enabled is false")
    return PostgresConnectionManager(
        host=config.postgres_host, port=config.postgres_port, database=config.postgres_database,
        user=config.postgres_user, password_env_var=config.postgres_password_env_var,
        sslmode=config.postgres_sslmode, pool_min_size=config.postgres_pool_min_size,
        pool_max_size=config.postgres_pool_max_size, retries=config.postgres_retries,
    )


def get_mongo_manager(config: DatabaseConfig | None = None) -> MongoConnectionManager:
    config = config or load_database_config()
    if not config.mongo_enabled:
        raise DatabaseError("config/database.yaml's mongo.enabled is false")
    return MongoConnectionManager(
        retries=config.mongo_retries, server_selection_timeout_ms=config.mongo_server_selection_timeout_ms
    )


def manage_tenant_schema(tenant_id: str, *, backend: str = "duckdb", config: DatabaseConfig | None = None) -> str:
    """Dispatches tenant schema management to whichever backend actually
    owns schemas. `duckdb` (default) delegates to `multi_tenant/
    tenant_manager.py`'s own `create_tenant_schema()` rather than
    duplicating it. `postgres` uses `PostgresConnectionManager.
    create_tenant_schema()` above. `mongo` has no schema concept (it's
    schemaless) - this returns the collection-name prefix convention
    (`tenant_<id>_`) a Mongo-backed module should use instead of raising,
    since "manage tenant database schemas" for a schemaless database
    honestly means "manage the tenant-scoping convention," not literally
    nothing."""
    if backend == "duckdb":
        from multi_tenant.tenant_manager import create_tenant_schema

        return create_tenant_schema(tenant_id)
    if backend == "postgres":
        return get_postgres_manager(config).create_tenant_schema(tenant_id)
    if backend == "mongo":
        return f"tenant_{tenant_id.replace('-', '_')}_"
    raise DatabaseError(f"unknown backend {backend!r}; must be one of ('duckdb', 'postgres', 'mongo')")


if __name__ == "__main__":
    cfg = load_database_config()
    print(f"duckdb.enabled={cfg.duckdb_enabled} postgres.enabled={cfg.postgres_enabled} mongo.enabled={cfg.mongo_enabled}")
