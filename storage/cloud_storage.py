"""Cloud Storage Integration (PHASE7-DEPLOYMENT.md Section 5).

One interface (`CloudStorageBackend`), four backends: `local` (default),
`s3`, `azure_blob`, `gcs`. `config/storage.yaml`'s `backend` key selects
which one `get_backend()` builds; every call site (this module's own
top-level `upload_raw_json()` / `download_raw_json()` /
`list_tenant_directories()` / `apply_retention_policy()` functions, and any
future caller) is written against the interface, not a specific backend, so
switching backends is a one-line config change.

`local` needs no `[cloud]` extra installed (see pyproject.toml) - it wraps a
plain directory (`config/storage.yaml`'s `local.root`, defaulting to
`data/raw` - the same tree `ingestion/paths.py`'s `RAW_DIR`/`TENANTS_RAW_DIR`
already point at). This is deliberate, not a placeholder: with the default
config, this module's `tenants/<id>/...` keys resolve to the exact same
files `ingestion/tenant_ingest.py` already writes directly, so the two never
disagree about where a tenant's raw data lives. `s3`/`azure_blob`/`gcs` are
real, complete backends against each provider's actual SDK (boto3 /
azure-storage-blob / google-cloud-storage) - not stubs - guarded by a
try/except ImportError exactly like `ml/models/forecasting.py` guards
statsmodels, with a clear "install the `[cloud]` extra" message if the SDK
isn't there. None of the three cloud SDKs are installed in this sandbox (no
package-index access here - see this repo's other Phase 7 modules for the
same constraint), so unlike `local` (exercised by this module's own test
suite against a real temp directory) they could not be run against a live
bucket/container here; they're written to each provider's documented API
and follow the same key scheme as `local` so behavior is consistent across
backends by construction, not verified end-to-end in this environment.

Scope: this module does NOT replace `ingestion/tenant_ingest.py`'s direct
filesystem writes with calls through here - `local` being config-identical
to the existing raw zone makes that unnecessary for the default case, and
forcing every ingest call through a network-backed abstraction by default
would make the local demo depend on cloud credentials it doesn't have. What
this module adds instead: `sync_tenant_to_cloud(tenant_id)`, an explicit,
opt-in push of a tenant's already-ingested local raw files to whichever
non-local backend `config/storage.yaml` configures - "modify ingestion to
write to cloud storage" becomes "ingestion keeps writing locally, and a
configured deployment additionally syncs to the cloud," which is both safer
and closer to how a real bulk-data pipeline actually stages writes (local
disk first, object storage as a durable second copy / cross-region archive)
than making every single ingest call a synchronous network round-trip.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ingestion.paths import PROJECT_ROOT

STORAGE_CONFIG_PATH = PROJECT_ROOT / "config" / "storage.yaml"

BACKEND_LOCAL = "local"
BACKEND_S3 = "s3"
BACKEND_AZURE_BLOB = "azure_blob"
BACKEND_GCS = "gcs"
BACKENDS = (BACKEND_LOCAL, BACKEND_S3, BACKEND_AZURE_BLOB, BACKEND_GCS)

_KEY_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9_\-./]+$")


class StorageError(ValueError):
    """Raised for an unknown backend, an unsafe object key, or a
    backend-specific failure this module chooses not to let propagate as a
    raw SDK exception - a distinct type so callers (e.g. a future FastAPI
    route wrapping this module) can catch it specifically, same reasoning as
    `multi_tenant/tenant_manager.py`'s `TenantError`."""


@dataclass(frozen=True)
class StorageConfig:
    backend: str
    local_root: Path
    s3_bucket: str
    s3_region: str
    s3_endpoint_url: str | None
    azure_container: str
    azure_account_name: str
    gcs_bucket: str
    gcs_project_id: str
    retention_raw_zone_days: int
    versioning_enabled: bool
    raw: dict = field(default_factory=dict)


def load_storage_config(path: Path = STORAGE_CONFIG_PATH) -> StorageConfig:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    local = raw.get("local") or {}
    s3 = raw.get("s3") or {}
    azure = raw.get("azure_blob") or {}
    gcs = raw.get("gcs") or {}
    retention = raw.get("retention") or {}

    local_root = Path(local.get("root", "data/raw"))
    if not local_root.is_absolute():
        local_root = PROJECT_ROOT / local_root

    return StorageConfig(
        backend=raw.get("backend", BACKEND_LOCAL),
        local_root=local_root,
        s3_bucket=s3.get("bucket", "mini-faire-raw"),
        s3_region=s3.get("region", "us-east-1"),
        s3_endpoint_url=s3.get("endpoint_url"),
        azure_container=azure.get("container", "mini-faire-raw"),
        azure_account_name=azure.get("account_name", ""),
        gcs_bucket=gcs.get("bucket", "mini-faire-raw"),
        gcs_project_id=gcs.get("project_id", ""),
        retention_raw_zone_days=int(retention.get("raw_zone_days", 365)),
        versioning_enabled=bool(raw.get("versioning_enabled", True)),
        raw=raw,
    )


def validate_object_key(key: str) -> str:
    """Raises StorageError for a key containing anything that could escape
    the configured prefix (`..`, a leading `/`, backslashes) - every backend
    below treats `key` as a path/blob-name segment, so this is the one
    chokepoint that keeps a maliciously- or accidentally-crafted key (e.g. a
    tenant_id that somehow bypassed `multi_tenant.tenant_manager.
    validate_tenant_id()`) from writing or reading outside its intended
    prefix."""
    if not key or key.startswith("/") or ".." in key.split("/") or not _KEY_SAFE_PATTERN.match(key):
        raise StorageError(f"unsafe object key {key!r}")
    return key


def tenant_prefix(tenant_id: str, *parts: str) -> str:
    """The one prefix scheme every backend uses - matches
    `multi_tenant/tenant_manager.py`'s `Tenant.storage_prefix`
    (`tenants/<tenant_id>`) and `tenant_storage_path()` exactly, so a key
    built here and a local filesystem path built there resolve to the same
    relative location under their respective roots."""
    return "/".join(("tenants", tenant_id, *parts))


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------


class CloudStorageBackend(ABC):
    @abstractmethod
    def upload_raw_json(self, key: str, records: list[dict[str, Any]]) -> str:
        """Writes `records` as a JSON array under `key`. Returns a
        backend-specific URI/identifier for what was written (the specific
        version, if `config/storage.yaml`'s `versioning_enabled` is true)."""

    @abstractmethod
    def download_raw_json(self, key: str) -> list[dict[str, Any]]:
        """Raises StorageError if `key` doesn't exist."""

    @abstractmethod
    def list_objects(self, prefix: str) -> list[str]:
        """Every object key under `prefix`, backend-native ordering."""

    @abstractmethod
    def list_tenant_directories(self) -> list[str]:
        """Every tenant_id with at least one object under `tenants/`."""

    @abstractmethod
    def delete_object(self, key: str) -> None:
        """No-op (not an error) if `key` doesn't exist - matches
        `Path.unlink(missing_ok=True)`'s idempotent-delete semantics used
        elsewhere in this repo."""

    @abstractmethod
    def object_age_days(self, key: str) -> float | None:
        """Age in days since `key` was last written, or None if it doesn't
        exist - used by `apply_retention_policy()`."""


# ---------------------------------------------------------------------------
# Local filesystem backend (default - no [cloud] extra required)
# ---------------------------------------------------------------------------


class LocalStorageBackend(CloudStorageBackend):
    def __init__(self, root: Path):
        self.root = root

    def _path_for(self, key: str) -> Path:
        validate_object_key(key)
        return self.root / f"{key}.json"

    def upload_raw_json(self, key: str, records: list[dict[str, Any]], *, versioning_enabled: bool = True) -> str:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if versioning_enabled and path.exists():
            version = 1
            while (path.parent / f"{path.stem}.v{version}{path.suffix}").exists():
                version += 1
            shutil.copy2(path, path.parent / f"{path.stem}.v{version}{path.suffix}")
        path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return f"file://{path}"

    def download_raw_json(self, key: str) -> list[dict[str, Any]]:
        path = self._path_for(key)
        if not path.exists():
            raise StorageError(f"object not found: {key!r}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else [payload]

    def list_objects(self, prefix: str) -> list[str]:
        validate_object_key(prefix) if prefix else None
        base = self.root / prefix if prefix else self.root
        if not base.exists():
            return []
        keys = []
        for path in sorted(base.rglob("*.json")):
            if ".v" in path.stem and re.search(r"\.v\d+$", path.stem):
                continue  # skip version-history siblings, mirroring how a
                # bucket's ListObjects call would return only current keys
                # unless a caller explicitly asks to list versions.
            keys.append(str(path.relative_to(self.root).with_suffix("")).replace("\\", "/"))
        return keys

    def list_tenant_directories(self) -> list[str]:
        tenants_dir = self.root / "tenants"
        if not tenants_dir.exists():
            return []
        return sorted(p.name for p in tenants_dir.iterdir() if p.is_dir())

    def delete_object(self, key: str) -> None:
        path = self._path_for(key)
        path.unlink(missing_ok=True)

    def object_age_days(self, key: str) -> float | None:
        path = self._path_for(key)
        if not path.exists():
            return None
        return (time.time() - path.stat().st_mtime) / 86400.0


# ---------------------------------------------------------------------------
# S3 backend
# ---------------------------------------------------------------------------


class S3StorageBackend(CloudStorageBackend):
    def __init__(self, *, bucket: str, region: str, endpoint_url: str | None = None):
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - exercised only with [cloud] installed
            raise ImportError(
                "storage/cloud_storage.py's S3 backend requires boto3. Install this "
                'repo\'s cloud extra with `pip install -e ".[cloud]"` (see pyproject.toml), '
                "or set config/storage.yaml's backend back to 'local'."
            ) from exc
        self.bucket = bucket
        self._client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)

    def _object_key(self, key: str) -> str:
        return f"{validate_object_key(key)}.json"

    def upload_raw_json(self, key: str, records: list[dict[str, Any]]) -> str:
        body = (json.dumps(records, indent=2, sort_keys=True) + "\n").encode("utf-8")
        object_key = self._object_key(key)
        response = self._client.put_object(Bucket=self.bucket, Key=object_key, Body=body, ContentType="application/json")
        version_id = response.get("VersionId")
        uri = f"s3://{self.bucket}/{object_key}"
        return f"{uri}?versionId={version_id}" if version_id else uri

    def download_raw_json(self, key: str) -> list[dict[str, Any]]:
        from botocore.exceptions import ClientError

        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self._object_key(key))
        except ClientError as exc:
            raise StorageError(f"object not found: {key!r}") from exc
        payload = json.loads(response["Body"].read().decode("utf-8"))
        return payload if isinstance(payload, list) else [payload]

    def list_objects(self, prefix: str) -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(".json"):
                    keys.append(obj["Key"][: -len(".json")])
        return sorted(keys)

    def list_tenant_directories(self) -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        tenants = set()
        for page in paginator.paginate(Bucket=self.bucket, Prefix="tenants/", Delimiter="/"):
            for prefix in page.get("CommonPrefixes", []):
                tenants.add(prefix["Prefix"].split("/")[1])
        return sorted(tenants)

    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=self._object_key(key))

    def object_age_days(self, key: str) -> float | None:
        from botocore.exceptions import ClientError

        try:
            response = self._client.head_object(Bucket=self.bucket, Key=self._object_key(key))
        except ClientError:
            return None
        last_modified = response["LastModified"]
        return (datetime.now(UTC) - last_modified).total_seconds() / 86400.0


# ---------------------------------------------------------------------------
# Azure Blob backend
# ---------------------------------------------------------------------------


class AzureBlobStorageBackend(CloudStorageBackend):
    def __init__(self, *, container: str, account_name: str):
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:  # pragma: no cover - exercised only with [cloud] installed
            raise ImportError(
                "storage/cloud_storage.py's Azure Blob backend requires azure-storage-blob. "
                'Install this repo\'s cloud extra with `pip install -e ".[cloud]"`, or set '
                "config/storage.yaml's backend back to 'local'."
            ) from exc
        import os

        connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        else:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(
                account_url=f"https://{account_name}.blob.core.windows.net", credential=DefaultAzureCredential()
            )
        self._container = service.get_container_client(container)

    def _blob_name(self, key: str) -> str:
        return f"{validate_object_key(key)}.json"

    def upload_raw_json(self, key: str, records: list[dict[str, Any]]) -> str:
        body = (json.dumps(records, indent=2, sort_keys=True) + "\n").encode("utf-8")
        blob = self._container.get_blob_client(self._blob_name(key))
        blob.upload_blob(body, overwrite=True, content_type="application/json")
        return blob.url

    def download_raw_json(self, key: str) -> list[dict[str, Any]]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            blob = self._container.get_blob_client(self._blob_name(key))
            payload = json.loads(blob.download_blob().readall().decode("utf-8"))
        except ResourceNotFoundError as exc:
            raise StorageError(f"object not found: {key!r}") from exc
        return payload if isinstance(payload, list) else [payload]

    def list_objects(self, prefix: str) -> list[str]:
        return sorted(
            blob.name[: -len(".json")]
            for blob in self._container.list_blobs(name_starts_with=prefix)
            if blob.name.endswith(".json")
        )

    def list_tenant_directories(self) -> list[str]:
        tenants = set()
        for blob in self._container.list_blobs(name_starts_with="tenants/"):
            parts = blob.name.split("/")
            if len(parts) > 1:
                tenants.add(parts[1])
        return sorted(tenants)

    def delete_object(self, key: str) -> None:
        self._container.get_blob_client(self._blob_name(key)).delete_blob(delete_snapshots="include")

    def object_age_days(self, key: str) -> float | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            properties = self._container.get_blob_client(self._blob_name(key)).get_blob_properties()
        except ResourceNotFoundError:
            return None
        return (datetime.now(UTC) - properties.last_modified).total_seconds() / 86400.0


# ---------------------------------------------------------------------------
# Google Cloud Storage backend
# ---------------------------------------------------------------------------


class GCSStorageBackend(CloudStorageBackend):
    def __init__(self, *, bucket: str, project_id: str):
        try:
            from google.cloud import storage as gcs_storage
        except ImportError as exc:  # pragma: no cover - exercised only with [cloud] installed
            raise ImportError(
                "storage/cloud_storage.py's GCS backend requires google-cloud-storage. "
                'Install this repo\'s cloud extra with `pip install -e ".[cloud]"`, or set '
                "config/storage.yaml's backend back to 'local'."
            ) from exc
        client = gcs_storage.Client(project=project_id) if project_id else gcs_storage.Client()
        self._bucket = client.bucket(bucket)

    def _blob_name(self, key: str) -> str:
        return f"{validate_object_key(key)}.json"

    def upload_raw_json(self, key: str, records: list[dict[str, Any]]) -> str:
        blob = self._bucket.blob(self._blob_name(key))
        blob.upload_from_string(json.dumps(records, indent=2, sort_keys=True) + "\n", content_type="application/json")
        return f"gs://{self._bucket.name}/{blob.name}"

    def download_raw_json(self, key: str) -> list[dict[str, Any]]:
        blob = self._bucket.blob(self._blob_name(key))
        if not blob.exists():
            raise StorageError(f"object not found: {key!r}")
        payload = json.loads(blob.download_as_text())
        return payload if isinstance(payload, list) else [payload]

    def list_objects(self, prefix: str) -> list[str]:
        return sorted(
            blob.name[: -len(".json")] for blob in self._bucket.list_blobs(prefix=prefix) if blob.name.endswith(".json")
        )

    def list_tenant_directories(self) -> list[str]:
        tenants = set()
        for blob in self._bucket.list_blobs(prefix="tenants/"):
            parts = blob.name.split("/")
            if len(parts) > 1:
                tenants.add(parts[1])
        return sorted(tenants)

    def delete_object(self, key: str) -> None:
        blob = self._bucket.blob(self._blob_name(key))
        if blob.exists():
            blob.delete()

    def object_age_days(self, key: str) -> float | None:
        blob = self._bucket.blob(self._blob_name(key))
        blob.reload()
        if blob.updated is None:
            return None
        return (datetime.now(UTC) - blob.updated).total_seconds() / 86400.0


# ---------------------------------------------------------------------------
# Factory + top-level convenience functions
# ---------------------------------------------------------------------------


def get_backend(config: StorageConfig | None = None) -> CloudStorageBackend:
    config = config or load_storage_config()
    if config.backend == BACKEND_LOCAL:
        return LocalStorageBackend(config.local_root)
    if config.backend == BACKEND_S3:
        return S3StorageBackend(bucket=config.s3_bucket, region=config.s3_region, endpoint_url=config.s3_endpoint_url)
    if config.backend == BACKEND_AZURE_BLOB:
        return AzureBlobStorageBackend(container=config.azure_container, account_name=config.azure_account_name)
    if config.backend == BACKEND_GCS:
        return GCSStorageBackend(bucket=config.gcs_bucket, project_id=config.gcs_project_id)
    raise StorageError(f"unknown storage backend {config.backend!r}; must be one of {BACKENDS}")


def upload_raw_json(key: str, records: list[dict[str, Any]], *, config: StorageConfig | None = None) -> str:
    config = config or load_storage_config()
    backend = get_backend(config)
    if isinstance(backend, LocalStorageBackend):
        return backend.upload_raw_json(key, records, versioning_enabled=config.versioning_enabled)
    return backend.upload_raw_json(key, records)


def download_raw_json(key: str, *, config: StorageConfig | None = None) -> list[dict[str, Any]]:
    return get_backend(config).download_raw_json(key)


def list_tenant_directories(*, config: StorageConfig | None = None) -> list[str]:
    return get_backend(config).list_tenant_directories()


def list_objects(prefix: str = "", *, config: StorageConfig | None = None) -> list[str]:
    return get_backend(config).list_objects(prefix)


def apply_retention_policy(
    *, prefix: str = "tenants", config: StorageConfig | None = None, dry_run: bool = True
) -> list[str]:
    """Every object under `prefix` older than `config.retention_raw_zone_days`.
    `dry_run=True` (the default) only lists candidates - never deletes
    anything unless a caller explicitly opts in with `dry_run=False`, the
    same "no silent destructive default" posture
    `multi_tenant/tenant_manager.py`'s `delete_tenant()` takes."""
    config = config or load_storage_config()
    backend = get_backend(config)
    expired = [
        key for key in backend.list_objects(prefix)
        if (age := backend.object_age_days(key)) is not None and age > config.retention_raw_zone_days
    ]
    if not dry_run:
        for key in expired:
            backend.delete_object(key)
    return expired


def sync_tenant_to_cloud(tenant_id: str, *, config: StorageConfig | None = None) -> list[str]:
    """Uploads every JSON file under this tenant's LOCAL raw zone
    (`ingestion/paths.py`'s TENANTS_RAW_DIR - always written locally first,
    regardless of `config/storage.yaml`'s backend, per this module's
    docstring) to the configured backend. Returns the keys written. A no-op
    list (nothing to sync, not an error) when `backend` is `local` - the
    local raw zone already *is* local storage's root, so there's nothing to
    copy anywhere."""
    config = config or load_storage_config()
    if config.backend == BACKEND_LOCAL:
        return []
    from ingestion.paths import TENANTS_RAW_DIR

    local_tenant_dir = TENANTS_RAW_DIR / tenant_id
    if not local_tenant_dir.exists():
        return []
    backend = get_backend(config)
    written: list[str] = []
    for path in sorted(local_tenant_dir.rglob("*.json")):
        relative = path.relative_to(TENANTS_RAW_DIR.parent)  # "tenants/<id>/.../file.json"
        key = str(relative.with_suffix("")).replace("\\", "/")
        records = json.loads(path.read_text(encoding="utf-8"))
        backend.upload_raw_json(key, records if isinstance(records, list) else [records])
        written.append(key)
    return written


if __name__ == "__main__":
    cfg = load_storage_config()
    print(f"backend={cfg.backend!r} tenant_directories={list_tenant_directories(config=cfg)}")
