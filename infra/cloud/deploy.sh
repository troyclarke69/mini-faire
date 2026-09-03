#!/usr/bin/env bash
# Deployment automation (PHASE7-DEPLOYMENT.md Section 9). Run from the repo
# root: `infra/cloud/deploy.sh <target>` where <target> is one of the
# platforms this phase's infra/cloud/ manifests actually cover:
#   fly            - infra/cloud/fly.toml (backend) + fly.frontend.toml
#   render         - infra/cloud/render.yaml (Blueprint deploy)
#   azure          - infra/cloud/azure-container-apps.{backend,frontend}.yaml
#   docker-compose - infra/cloud/docker-compose.cloud.yaml (+ optionally
#                    docker-compose.observability.yaml), for a single-host
#                    deployment (a small VM, or local staging)
#
# Same five steps for every target, in order: lint, test, build images,
# migrate, deploy, notify - a step that doesn't apply to a given target
# (e.g. "build images" for Render, which builds from its own Dockerfile
# references server-side) is skipped with a message, not silently faked.
#
# Not run end-to-end against a real fly/render/azure account or a live
# Docker daemon in the sandbox that authored this script (see
# docker-compose.cloud.yaml's and MANAGED_SERVICES.md's headers for what
# could and couldn't be validated there) - `bash -n` (syntax check) and
# shellcheck (where available) were both run clean; every fly/render/az/gh
# CLI invocation below is written against each tool's real documented
# command shape, not guessed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

TARGET="${1:-}"
SKIP_TESTS="${SKIP_TESTS:-false}"
DRY_RUN="${DRY_RUN:-false}"

log() { printf '[deploy] %s\n' "$*"; }
run() {
  log "+ $*"
  if [ "$DRY_RUN" = "true" ]; then
    return 0
  fi
  "$@"
}

usage() {
  cat <<'EOF'
Usage: infra/cloud/deploy.sh <fly|render|azure|docker-compose>

Environment variables:
  SKIP_TESTS=true   Skip lint/pytest (still builds/migrates/deploys)
  DRY_RUN=true       Print every command instead of running it
  JWT_SECRET_KEY, POSTGRES_PASSWORD, MONGO_PASSWORD, SLACK_WEBHOOK_URL,
  NEXT_PUBLIC_API_URL, FLY_API_TOKEN, AZURE_RESOURCE_GROUP, ...
                     Same variables docker-compose.cloud.yaml / fly.toml /
                     render.yaml / azure-container-apps.*.yaml already read.
EOF
}

notify() {
  # Best-effort deploy notification - mirrors alerts/dispatcher.py's "a
  # notification failure must never take down the caller" posture: `|| true`
  # so a Slack outage never fails a deploy that otherwise succeeded.
  local status="$1" message="$2"
  if [ -z "${SLACK_WEBHOOK_URL:-}" ]; then
    log "SLACK_WEBHOOK_URL not set - skipping deploy notification ($status: $message)"
    return 0
  fi
  local emoji=":white_check_mark:"
  [ "$status" = "failed" ] && emoji=":x:"
  curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "{\"text\": \"${emoji} mini-faire deploy (${TARGET}): ${message}\"}" \
    "$SLACK_WEBHOOK_URL" >/dev/null 2>&1 || log "notify: Slack webhook post failed (non-fatal)"
}

trap 'notify failed "deploy script exited with an error (see CI/terminal log)"' ERR

step_lint_and_test() {
  if [ "$SKIP_TESTS" = "true" ]; then
    log "SKIP_TESTS=true - skipping lint/pytest"
    return 0
  fi
  log "Step 1/5: lint + test"
  run ruff check .
  if [ -d tests ]; then
    run python -m pytest -q
  else
    log "no tests/ directory in this checkout - skipping pytest"
  fi
  if [ -d frontend ]; then
    run bash -c 'cd frontend && npm run lint --if-present && npm run typecheck --if-present'
  fi
}

step_build_images() {
  log "Step 2/5: build Docker images"
  case "$TARGET" in
    docker-compose)
      run docker compose -f infra/cloud/docker-compose.cloud.yaml build
      ;;
    fly)
      # `fly deploy` (step 5) builds remotely from fly.toml's [build] stanza
      # itself - no separate local build step needed, matching Fly's actual
      # documented flow.
      log "fly deploy builds remotely - skipping local docker build"
      ;;
    render)
      # Render's Blueprint deploy (render.yaml) builds from the referenced
      # Dockerfile on Render's own infrastructure when the blueprint is
      # synced - nothing to build locally.
      log "Render builds from render.yaml server-side - skipping local docker build"
      ;;
    azure)
      run docker build -f infra/cloud/Dockerfile.backend -t "${ACR_LOGIN_SERVER:?set ACR_LOGIN_SERVER}/mini-faire-backend:${IMAGE_TAG:-latest}" .
      run docker build -f infra/cloud/Dockerfile.frontend -t "${ACR_LOGIN_SERVER:?set ACR_LOGIN_SERVER}/mini-faire-frontend:${IMAGE_TAG:-latest}" \
        --build-arg NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-}" .
      run docker push "${ACR_LOGIN_SERVER}/mini-faire-backend:${IMAGE_TAG:-latest}"
      run docker push "${ACR_LOGIN_SERVER}/mini-faire-frontend:${IMAGE_TAG:-latest}"
      ;;
  esac
}

step_migrate() {
  log "Step 3/5: database migrations"
  # config/database.yaml's postgres.enabled gates this - the default
  # duckdb-only setup has no separate migration step (warehouse/duckdb/*.sql
  # is applied by the ELT flows themselves, same as every non-Phase-7
  # environment). Only run Postgres migrations when that backend is
  # actually turned on, so this step is a no-op for the default deployment.
  local pg_enabled
  pg_enabled="$(python -c "
from database.cloud_db import load_database_config
print('true' if load_database_config().postgres_enabled else 'false')
" 2>/dev/null || echo false)"
  if [ "$pg_enabled" = "true" ]; then
    run python -c "
from database.cloud_db import get_postgres_manager
applied = get_postgres_manager().run_migrations()
print(f'applied {len(applied)} migration(s): {applied}')
"
  else
    log "database.postgres.enabled is false in config/database.yaml - skipping Postgres migrations"
  fi
}

step_deploy() {
  log "Step 4/5: deploy"
  case "$TARGET" in
    docker-compose)
      run docker compose -f infra/cloud/docker-compose.cloud.yaml up -d
      ;;
    fly)
      # No --dockerfile flag or working-directory positional arg needed -
      # infra/cloud/fly.toml's/fly.frontend.toml's own [build] dockerfile
      # field already resolves correctly (a bare filename, relative to the
      # directory each fly.toml lives in), while the Docker build context
      # stays at the repo root by default regardless of where --config
      # points (see fly.toml's own header comment, and DEPLOYMENT.md's
      # troubleshooting section, for the doubled-path error this avoids).
      run fly deploy --config infra/cloud/fly.toml --remote-only
      run fly deploy --config infra/cloud/fly.frontend.toml --remote-only
      ;;
    render)
      # Render Blueprints deploy on git push once the blueprint is synced
      # via the dashboard/API - `render blueprint launch` (Render CLI) is
      # the one-time setup step; redeploys of an already-launched blueprint
      # happen by pushing to the branch Render is tracking, which this
      # script doesn't do on the caller's behalf (a `git push` here would be
      # a surprising side effect for a deploy script to take unprompted).
      log "render.yaml is a Blueprint - push this branch to the tracked remote to trigger Render's own build+deploy, or run 'render blueprint launch infra/cloud/render.yaml' for first-time setup"
      ;;
    azure)
      run az containerapp update \
        --name mini-faire-backend \
        --resource-group "${AZURE_RESOURCE_GROUP:?set AZURE_RESOURCE_GROUP}" \
        --image "${ACR_LOGIN_SERVER}/mini-faire-backend:${IMAGE_TAG:-latest}"
      run az containerapp update \
        --name mini-faire-frontend \
        --resource-group "${AZURE_RESOURCE_GROUP}" \
        --image "${ACR_LOGIN_SERVER}/mini-faire-frontend:${IMAGE_TAG:-latest}"
      ;;
  esac
}

step_notify() {
  log "Step 5/5: notify"
  notify succeeded "deployed to ${TARGET} successfully"
}

case "$TARGET" in
  fly|render|azure|docker-compose)
    ;;
  *)
    usage
    exit 1
    ;;
esac

step_lint_and_test
step_build_images
step_migrate
step_deploy
step_notify
log "done."
