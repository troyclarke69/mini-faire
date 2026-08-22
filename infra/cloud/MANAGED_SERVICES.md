# Managed services setup notes

Quick pointers for the three managed services PHASE7-DEPLOYMENT.md Section 1
names alongside the compute deployment manifests (Fly.io/Render/Azure
Container Apps) - `infra/cloud/terraform/modules/` is what actually
provisions each one; this file is the "what env var does this become"
cross-reference.

## Neon / Postgres

Provisioned by `infra/cloud/terraform/modules/postgres/` (AWS RDS by
default - see that module's header for why, and for swapping in Neon's own
Terraform provider instead). Once you have a host/port/database/user/
password from either path:

- Set `config/database.yaml`'s `postgres.enabled: true` and fill in
  `host`/`port`/`database`/`user`.
- Set the `POSTGRES_PASSWORD` environment variable (never in the YAML file).
- Run `database/cloud_db.py`'s `PostgresConnectionManager.run_migrations()`
  (or `python -m database.cloud_db` after wiring a small script - see that
  module's docstring) to apply `database/migrations/postgres/*.sql`.

## MongoDB Atlas

Provisioned by `infra/cloud/terraform/modules/mongodb_atlas/`. Reuses
`config/mongo.yaml`'s existing shape - once the module's `connection_srv_uri`
output is available, set `config/mongo.yaml`'s `mongo_host` to match and set
the `MONGO_PASSWORD` environment variable to the module's
`database_password` output. No new config file - `ingestion/mongo_ingest.py`
and `database/cloud_db.py`'s `MongoConnectionManager` both already read
`config/mongo.yaml`.

## S3-compatible object storage

Provisioned by `infra/cloud/terraform/modules/object_storage/`. Set
`config/storage.yaml`'s `backend: s3` and fill in `s3.bucket`/`s3.region` to
match the module's `bucket_name` output - `storage/cloud_storage.py`'s
`S3StorageBackend` reads standard AWS credentials via boto3's normal
credential chain (env vars / `~/.aws/credentials` / an instance/task role),
not a value in this config file.
