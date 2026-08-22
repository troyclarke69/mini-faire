"""ML Infrastructure & Model Registry (PHASE6-ML.md Section 1).

Every trained model in this repo - forecasting, clustering, recommendations,
the anomaly classifier - goes through this module rather than each
`ml/models/*.py` file inventing its own bookkeeping. A registry row is pure
metadata (params, eval metrics, feature schema, timestamps, active/inactive
status); the fitted estimator itself is pickled to `data/ml_models/<model_id>.pkl`
(see `ingestion/paths.py`'s `ML_MODELS_DIR`) and the row's `artifact_path`
points at it. This mirrors how a real model registry (MLflow, SageMaker Model
Registry, etc.) separates "what was trained and how did it score" from "the
binary itself" - and it lets `orchestration/ml_inference_flow.py` load a
model without refitting it, matching PHASE6-ML.md Section 7's "load active
models" / "run inference" split from "build features" / "train models".

Versioning: `model_name` is the stable identity across retrains (e.g.
"forecast_gmv_daily", "cluster_retailer_segments"); `version` is an
auto-incrementing integer per model_name, starting at 1. `model_id` (the
primary key) is `f"{model_name}_v{version}"`.

Exactly one version per model_name is ever `status = 'active'` at a time -
`register_model(activate=True)`, `activate_model()`, and `rollback_model()`
all demote every other version of the same model_name to `inactive` in the
same transaction as they promote one. This is a deliberate simplification:
a production registry might support multiple simultaneously-active variants
for A/B testing, but this demo's inference flow only ever wants "the one
model to use right now" per model_name.

Nothing here imports scikit-learn, numpy, or any ML library - this module
only stores metadata and (de)serializes whatever object `ml/models/*.py`
hands it via `pickle`. That keeps the registry usable even in a process that
never imports the heavier optional ML dependencies (e.g. `api/ml_api.py`
just reads registry rows for the `/ml/models` endpoint).
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH, ML_MODELS_DIR

STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"  # registered, never promoted to active
STATUS_SUPERSEDED = "superseded"  # was active, demoted because a newer version was promoted
STATUS_ROLLED_BACK = "rolled_back"  # was active, demoted by an explicit rollback_model() call


@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    model_name: str
    model_type: str  # forecasting | clustering | recommendation | anomaly_classifier
    version: int
    status: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    feature_schema: list[str]
    artifact_path: str | None
    trained_at: str
    created_at: str


def _row_to_record(row: tuple) -> ModelRecord:
    (model_id, model_name, model_type, version, status, params, metrics,
     feature_schema, artifact_path, trained_at, created_at) = row
    return ModelRecord(
        model_id=model_id,
        model_name=model_name,
        model_type=model_type,
        version=version,
        status=status,
        params=json.loads(params) if params else {},
        metrics=json.loads(metrics) if metrics else {},
        feature_schema=json.loads(feature_schema) if feature_schema else [],
        artifact_path=artifact_path,
        trained_at=str(trained_at),
        created_at=str(created_at),
    )


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists ml")
    con.execute(
        """
        create table if not exists ml.model_registry (
          model_id varchar primary key,
          model_name varchar,
          model_type varchar,
          version integer,
          status varchar,
          params varchar,
          metrics varchar,
          feature_schema varchar,
          artifact_path varchar,
          trained_at timestamptz,
          created_at timestamptz
        )
        """
    )


def save_artifact(model_id: str, estimator: Any, *, models_dir: Path = ML_MODELS_DIR) -> str:
    """Pickles `estimator` (any picklable object - a fitted sklearn estimator,
    a plain dict of coefficients, a fallback statistics.NamedTuple, whatever
    the caller trained) to `<models_dir>/<model_id>.pkl` and returns the path
    as a string for storage in the registry row. `estimator` may be `None`
    (e.g. a model type with no single fitted object, like the recommendations
    module's precomputed similarity table) - in that case no file is written
    and this returns None."""
    if estimator is None:
        return None
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"{model_id}.pkl"
    with path.open("wb") as handle:
        pickle.dump(estimator, handle)
    return str(path)


def load_artifact(artifact_path: str | None) -> Any:
    """Inverse of save_artifact(). Returns None if artifact_path is None or
    the file is missing (e.g. data/ml_models/ was cleaned but the registry
    row survived) - callers must treat None as "no fitted object available,
    fall back or skip" rather than letting a bare FileNotFoundError surface
    from an inference pass that's supposed to isolate failures per model
    type (see orchestration/ml_inference_flow.py)."""
    if not artifact_path:
        return None
    path = Path(artifact_path)
    if not path.exists():
        return None
    with path.open("rb") as handle:
        return pickle.load(handle)


def _next_version(con, model_name: str) -> int:
    row = con.execute(
        "select coalesce(max(version), 0) from ml.model_registry where model_name = ?", [model_name]
    ).fetchone()
    return int(row[0]) + 1 if row else 1


def _deactivate_other_versions(con, model_name: str, keep_model_id: str, *, new_status: str = STATUS_SUPERSEDED) -> None:
    """Demotes whichever OTHER version of model_name currently has status
    STATUS_ACTIVE. Defaults to STATUS_SUPERSEDED (not STATUS_INACTIVE) - the
    version being demoted here was genuinely live a moment ago, which
    rollback_model() below needs to be able to tell apart from a version
    that was registered but never promoted (which stays STATUS_INACTIVE
    from registration and is never touched by this function, since the
    `status = ?` filter only matches the currently-active row)."""
    con.execute(
        "update ml.model_registry set status = ? where model_name = ? and model_id != ? and status = ?",
        [new_status, model_name, keep_model_id, STATUS_ACTIVE],
    )


def register_model(
    model_name: str,
    model_type: str,
    *,
    params: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    feature_schema: list[str] | None = None,
    estimator: Any = None,
    activate: bool = True,
    db_path: Path = DUCKDB_PATH,
) -> ModelRecord:
    """Registers a newly-trained model version. If `activate=True` (the
    default - callers that want promotion gating, e.g.
    orchestration/ml_training_flow.py comparing eval metrics against the
    current active version, should pass `activate=False` and call
    `activate_model()` themselves once the comparison passes), this version
    becomes the active one and every other version of the same model_name is
    demoted to inactive."""
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        version = _next_version(con, model_name)
        model_id = f"{model_name}_v{version}"
        artifact_path = save_artifact(model_id, estimator)
        now = utc_now()
        status = STATUS_ACTIVE if activate else STATUS_INACTIVE

        con.execute(
            """
            insert or replace into ml.model_registry
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                model_id, model_name, model_type, version, status,
                json.dumps(params or {}, default=str, sort_keys=True),
                json.dumps(metrics or {}, default=str, sort_keys=True),
                json.dumps(feature_schema or [], default=str),
                artifact_path, now, now,
            ],
        )
        if activate:
            _deactivate_other_versions(con, model_name, model_id)

    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=model_id,
                source_node=f"ml_training://{model_name}",
                target_node="ml.model_registry",
                edge_type="ml_model_registered",
                entity=model_type,
                created_at=now,
            )
        ]
    )
    return ModelRecord(
        model_id=model_id, model_name=model_name, model_type=model_type, version=version,
        status=status, params=params or {}, metrics=metrics or {}, feature_schema=feature_schema or [],
        artifact_path=artifact_path, trained_at=now, created_at=now,
    )


def activate_model(model_name: str, version: int, db_path: Path = DUCKDB_PATH) -> ModelRecord | None:
    """Promotes a specific version to active, demoting every other version
    of the same model_name. Returns None if that (model_name, version)
    doesn't exist."""
    model_id = f"{model_name}_v{version}"
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.execute("update ml.model_registry set status = ? where model_id = ?", [STATUS_ACTIVE, model_id])
        _deactivate_other_versions(con, model_name, model_id)
        row = con.execute(
            "select model_id, model_name, model_type, version, status, params, metrics, "
            "feature_schema, artifact_path, trained_at, created_at "
            "from ml.model_registry where model_id = ?",
            [model_id],
        ).fetchone()
    return _row_to_record(row) if row else None


def rollback_model(model_name: str, db_path: Path = DUCKDB_PATH) -> ModelRecord | None:
    """Activates the previous version relative to whichever version is
    currently active (by version number, excluding the current active one),
    and marks the version being rolled back away from as `rolled_back`
    rather than plain `inactive` - so the registry's history distinguishes
    "never promoted" from "was active, then demoted due to a failure"
    (PHASE6-ML.md Section 7's "rollback on failure").

    Used by orchestration/ml_training_flow.py when a newly-activated
    version fails a post-activation sanity check, and available for manual
    use if a model starts misbehaving in production. Returns None if there
    is no earlier version that was ever actually active to roll back to
    (first-ever version, model_name unknown, or every earlier version was
    registered but never promoted) - callers should treat that as "nothing
    to roll back to, current active version stands." Deliberately searches
    only STATUS_SUPERSEDED/STATUS_ROLLED_BACK versions (ones that were
    genuinely live before), not just "the next version number down" - a
    version that was registered and never promoted (STATUS_INACTIVE, e.g.
    because it scored worse than the active model at the time) was never a
    known-good state, so rolling back to it would be no safer than the
    version that just failed."""
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        current = con.execute(
            "select model_id, version from ml.model_registry where model_name = ? and status = ? "
            "order by version desc limit 1",
            [model_name, STATUS_ACTIVE],
        ).fetchone()
        if current is None:
            return None
        current_model_id, current_version = current

        previous = con.execute(
            "select model_id, version from ml.model_registry where model_name = ? and version < ? "
            "and status in (?, ?) order by version desc limit 1",
            [model_name, current_version, STATUS_SUPERSEDED, STATUS_ROLLED_BACK],
        ).fetchone()
        if previous is None:
            return None
        previous_model_id, previous_version = previous

        con.execute("update ml.model_registry set status = ? where model_id = ?", [STATUS_ROLLED_BACK, current_model_id])
        con.execute("update ml.model_registry set status = ? where model_id = ?", [STATUS_ACTIVE, previous_model_id])

        row = con.execute(
            "select model_id, model_name, model_type, version, status, params, metrics, "
            "feature_schema, artifact_path, trained_at, created_at "
            "from ml.model_registry where model_id = ?",
            [previous_model_id],
        ).fetchone()
    return _row_to_record(row) if row else None


def get_active_model(model_name: str, db_path: Path = DUCKDB_PATH) -> ModelRecord | None:
    with connect_with_retry(db_path, read_only=True) as con:
        # No _ensure_tables(con) here (unlike register_model/activate_model/
        # rollback_model, which hold a write connection): "create schema/table
        # if not exists" is still a CREATE statement, and DuckDB refuses to
        # execute ANY statement of type CREATE against a read-only-attached
        # database, even one that would be a no-op because the object already
        # exists. `ml.model_registry` is only ever written by the three
        # write-connection functions above, so by the time anything calls this
        # read path the table either already exists (the normal case) or no
        # model has ever been registered yet - which is exactly the
        # "no active model" case this function needs to report as None
        # rather than crash on, e.g. on a freshly built warehouse before
        # orchestration/ml_training_flow.py has ever run.
        try:
            row = con.execute(
                "select model_id, model_name, model_type, version, status, params, metrics, "
                "feature_schema, artifact_path, trained_at, created_at "
                "from ml.model_registry where model_name = ? and status = ? order by version desc limit 1",
                [model_name, STATUS_ACTIVE],
            ).fetchone()
        except Exception:
            return None
    return _row_to_record(row) if row else None


def list_versions(model_name: str, db_path: Path = DUCKDB_PATH) -> list[ModelRecord]:
    with connect_with_retry(db_path, read_only=True) as con:
        # See get_active_model()'s comment on why _ensure_tables() is not
        # called on this read-only connection.
        try:
            rows = con.execute(
                "select model_id, model_name, model_type, version, status, params, metrics, "
                "feature_schema, artifact_path, trained_at, created_at "
                "from ml.model_registry where model_name = ? order by version desc",
                [model_name],
            ).fetchall()
        except Exception:
            return []
    return [_row_to_record(row) for row in rows]


def list_all_active(db_path: Path = DUCKDB_PATH) -> list[ModelRecord]:
    """Every currently-active model across every model_name - used by
    api/ml_api.py's /ml/models endpoint and orchestration/ml_inference_flow.py
    to enumerate what to run inference with in one call."""
    with connect_with_retry(db_path, read_only=True) as con:
        # See get_active_model()'s comment on why _ensure_tables() is not
        # called on this read-only connection.
        try:
            rows = con.execute(
                "select model_id, model_name, model_type, version, status, params, metrics, "
                "feature_schema, artifact_path, trained_at, created_at "
                "from ml.model_registry where status = ? order by model_name",
                [STATUS_ACTIVE],
            ).fetchall()
        except Exception:
            return []
    return [_row_to_record(row) for row in rows]
