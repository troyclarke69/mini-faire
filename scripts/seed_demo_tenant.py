"""One-time (idempotent) setup for a shared 'demo_tenant' workspace + login
(PHASE7-DEPLOYMENT.md Section 3/4's onboarding flow, demo-friendly variant).

This repo's normal signup flow (`auth/auth_api.py`'s `POST /auth/signup`)
always creates a brand-new tenant - there's no seeded "just log in and look
around" account, which is a real gap for a demo/eval audience who shouldn't
have to create a workspace before seeing anything. This script fills that
gap once: creates a `demo_tenant` tenant (if it doesn't already exist) and
one `tenant_admin` user in it with a fixed, published password. That's safe
only because this is a local demo app working on synthetic data - never a
pattern for a real deployment (a real deployment would never publish a
password in a repo or a script; see `config/auth.yaml`'s own docstring on
the JWT secret for the same "demo runs with zero configuration, a real
deployment must set real secrets" posture).

Safe to run more than once - both the tenant and the user creation are
skipped (not re-raised as errors) if they already exist, so re-running this
after `scripts/seed_tenant_orders.py demo_tenant`, or after any other setup
step, never fails or duplicates anything.

Usage (from the project root, same venv as scripts/run_demo.py):
    python scripts\\seed_demo_tenant.py

Then seed it with order data the normal way:
    python scripts\\seed_tenant_orders.py demo_tenant
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/ isn't a package (no __init__.py - see scripts/run_demo.py's own
# "from preflight import ..." bare import, which only works because running
# `python scripts\<name>.py` puts scripts/ itself on sys.path[0]). The
# project root - where auth/, ingestion/, multi_tenant/, etc. actually live -
# is NOT added by that same mechanism, so it normally has to come from this
# project's own editable install (`pip install -e ".[dev]"`, per
# scripts/preflight.py's error messages and pyproject.toml's
# [tool.setuptools.packages.find]). That install can go stale the moment a
# new top-level package (like auth/ or multi_tenant/, both added this phase)
# is created after the last `pip install -e .` - the fix is just to re-run
# that command, but this script doesn't require it: inserting the project
# root here makes `python scripts\seed_demo_tenant.py` work regardless of
# whether the editable install is current, same as scripts/seed_tenant_orders.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth.auth_models import ROLE_TENANT_ADMIN, AuthError, create_user  # noqa: E402
from multi_tenant.tenant_manager import ISOLATION_POOLED, create_tenant, get_tenant  # noqa: E402

# Published on purpose (see module docstring) - this is the account
# frontend/app/login/page.tsx pre-fills and labels as the demo login.
DEMO_TENANT_ID = "demo_tenant"
DEMO_TENANT_NAME = "Demo Workspace"
DEMO_EMAIL = "demo@rmap.local"
DEMO_PASSWORD = "demo_tenant"  # 11 chars - clears config/auth.yaml's password_policy.min_length: 10
DEMO_NAME = "Demo User"


def seed_demo_tenant() -> None:
    if get_tenant(DEMO_TENANT_ID) is None:
        create_tenant(DEMO_TENANT_ID, DEMO_TENANT_NAME, isolation_policy=ISOLATION_POOLED)
        print(f"Created tenant {DEMO_TENANT_ID!r}.")
    else:
        print(f"Tenant {DEMO_TENANT_ID!r} already exists - leaving it as is.")

    try:
        create_user(DEMO_EMAIL, DEMO_PASSWORD, DEMO_NAME, role=ROLE_TENANT_ADMIN, tenant_id=DEMO_TENANT_ID)
        print(f"Created demo user {DEMO_EMAIL!r}.")
    except AuthError as exc:
        if "already exists" in str(exc):
            print(f"User {DEMO_EMAIL!r} already exists - leaving it as is.")
        else:
            raise

    print(
        f"\nDemo login -> email: {DEMO_EMAIL}  password: {DEMO_PASSWORD}\n"
        f"Next: python scripts\\seed_tenant_orders.py {DEMO_TENANT_ID}  (to give it order data)"
    )


if __name__ == "__main__":
    seed_demo_tenant()
