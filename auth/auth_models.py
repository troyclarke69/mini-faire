"""User Accounts & Authentication - data layer (PHASE7-DEPLOYMENT.md Section 3).

Deliberately split from `auth/auth_api.py` / `auth/auth_middleware.py`: this
module holds every piece of authentication logic that does NOT need FastAPI
- password hashing, JWT encode/decode, and the `auth.users`/
`auth.refresh_tokens` DuckDB tables - so it can be imported and unit-tested
in any environment, including one without `fastapi` installed (this repo's
own dev sandbox is exactly that environment; see api/ml_api.py's docstring
for the established precedent of API-layer files being FastAPI-dependent
while the logic they wrap stays plain Python). `auth_api.py` and
`auth_middleware.py` are the only two files in this package that import
`fastapi`.

Password hashing: PBKDF2-HMAC-SHA256 via `hashlib.pbkdf2_hmac` (Python
stdlib, no bcrypt/argon2 dependency needed) at 260,000 iterations - OWASP's
2023 minimum recommendation for PBKDF2-SHA256. Encoded as
`pbkdf2_sha256$<iterations>$<base64 salt>$<base64 digest>`, a self-describing
format (iteration count travels with the hash) so a future bump to the
iteration count doesn't invalidate already-hashed passwords.

JWT: a from-scratch HS256 (HMAC-SHA256) implementation using only
`hashlib`/`hmac`/`base64`/`json` - no PyJWT/python-jose dependency. This
mirrors `alerts/dispatcher.py`'s documented reasoning for hand-rolling its
HTTP POST with `urllib.request` instead of adding `requests`: this repo
follows a minimal-dependency philosophy, and HS256 JWT is a small enough
algorithm (base64url-encode header + payload, HMAC-sign, base64url-encode
the signature) that reimplementing it correctly is cheaper than it looks and
avoids a dependency most of this repo's users won't otherwise need. Signature
verification uses `hmac.compare_digest` for constant-time comparison.
Nothing here implements RS256/other asymmetric algorithms - HS256 with a
server-side secret is sufficient for a single-backend deployment; a
horizontally-federated deployment that needs public-key verification at the
gateway is a real extension, not something this phase claims.

Tables live in DuckDB's `auth` schema (`auth.users`, `auth.refresh_tokens`),
same "create schema/table if not exists, own it" convention every other
module in this repo follows (`ml/registry.py`, `multi_tenant/tenant_manager.py`,
`alerts/dispatcher.py`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import utc_now
from ingestion.paths import DUCKDB_PATH, PROJECT_ROOT

AUTH_CONFIG_PATH = PROJECT_ROOT / "config" / "auth.yaml"

ROLE_ADMIN = "admin"  # platform-wide - can act across every tenant
ROLE_TENANT_ADMIN = "tenant_admin"  # full control within one tenant
ROLE_ANALYST = "analyst"  # read/write within one tenant (no user/tenant management)
ROLE_VIEWER = "viewer"  # read-only within one tenant
ROLES = (ROLE_ADMIN, ROLE_TENANT_ADMIN, ROLE_ANALYST, ROLE_VIEWER)
# Coarse role ranking used by auth_middleware.py's require_role() for
# "at least this role" checks - index position, not a numeric score, so
# there's no arithmetic to keep in sync if a role is inserted later.
_ROLE_RANK = {role: index for index, role in enumerate(reversed(ROLES))}  # viewer=0 .. admin=3

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"

_INSECURE_DEV_JWT_SECRET = "mini-faire-insecure-dev-secret-do-not-use-in-production"
PBKDF2_ITERATIONS = 260_000


class AuthError(Exception):
    """Raised for invalid credentials, malformed/expired/tampered tokens, or
    disabled accounts - a distinct type so auth_middleware.py's FastAPI
    dependencies can catch it specifically and translate it into a 401,
    rather than an arbitrary exception surfacing as a 500."""


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    jwt_secret: str
    jwt_secret_is_insecure_default: bool
    jwt_algorithm: str
    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int
    password_min_length: int
    rate_limit_enabled: bool
    rate_limit_requests_per_minute: int
    rate_limit_burst: int
    default_role: str


def load_auth_config(path: Path = AUTH_CONFIG_PATH) -> AuthConfig:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    jwt_cfg = raw.get("jwt") or {}
    password_cfg = raw.get("password_policy") or {}
    rate_cfg = raw.get("rate_limit") or {}

    secret_env_var = jwt_cfg.get("secret_env_var", "JWT_SECRET_KEY")
    secret = os.environ.get(secret_env_var)
    insecure_default = secret is None
    if insecure_default:
        secret = _INSECURE_DEV_JWT_SECRET
        print(
            f"  auth: {secret_env_var} not set - using an insecure built-in dev secret. "
            f"Set {secret_env_var} before deploying anywhere real."
        )

    return AuthConfig(
        enabled=bool(raw.get("enabled", True)),
        jwt_secret=secret,
        jwt_secret_is_insecure_default=insecure_default,
        jwt_algorithm=jwt_cfg.get("algorithm", "HS256"),
        access_token_ttl_seconds=int(jwt_cfg.get("access_token_ttl_seconds", 900)),
        refresh_token_ttl_seconds=int(jwt_cfg.get("refresh_token_ttl_seconds", 1_209_600)),
        password_min_length=int(password_cfg.get("min_length", 10)),
        rate_limit_enabled=bool(rate_cfg.get("enabled", True)),
        rate_limit_requests_per_minute=int(rate_cfg.get("requests_per_minute", 120)),
        rate_limit_burst=int(rate_cfg.get("burst", 30)),
        default_role=raw.get("default_role", ROLE_VIEWER),
    )


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256, stdlib only)
# ---------------------------------------------------------------------------


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode('ascii')}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_str, salt_b64, digest_b64 = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        iterations = int(iterations_str)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


# ---------------------------------------------------------------------------
# JWT (HS256, stdlib only)
# ---------------------------------------------------------------------------


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def encode_jwt(claims: dict[str, Any], secret: str, *, algorithm: str = "HS256", ttl_seconds: int | None = None) -> str:
    if algorithm != "HS256":
        raise AuthError(f"unsupported JWT algorithm {algorithm!r} - only HS256 is implemented")
    header = {"alg": algorithm, "typ": "JWT"}
    body = dict(claims)
    now = int(time.time())
    body.setdefault("iat", now)
    if ttl_seconds is not None:
        body["exp"] = now + ttl_seconds
    header_segment = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_segment = _b64url_encode(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_segment = _b64url_encode(signature)
    return f"{header_segment}.{payload_segment}.{signature_segment}"


def decode_jwt(token: str, secret: str, *, verify_exp: bool = True) -> dict[str, Any]:
    """Verifies the signature (and expiry, unless `verify_exp=False`) and
    returns the claims dict. Raises AuthError on any failure - malformed
    token, wrong number of segments, bad signature, or (if checked)
    expiry - never returns a partially-trusted result."""
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("malformed token: expected 3 dot-separated segments")
    header_segment, payload_segment, signature_segment = parts
    try:
        header = json.loads(_b64url_decode(header_segment))
    except (ValueError, UnicodeDecodeError):
        raise AuthError("malformed token: invalid header")
    algorithm = header.get("alg")
    if algorithm != "HS256":
        raise AuthError(f"unsupported JWT algorithm {algorithm!r} - only HS256 is implemented")

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected_signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual_signature = _b64url_decode(signature_segment)
    except (ValueError, UnicodeDecodeError):
        raise AuthError("malformed token: invalid signature encoding")
    if not hmac.compare_digest(expected_signature, actual_signature):
        raise AuthError("invalid token signature")

    try:
        claims = json.loads(_b64url_decode(payload_segment))
    except (ValueError, UnicodeDecodeError):
        raise AuthError("malformed token: invalid payload")

    if verify_exp and "exp" in claims and time.time() > claims["exp"]:
        raise AuthError("token expired")
    return claims


# ---------------------------------------------------------------------------
# User record + auth.users table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    password_hash: str
    name: str
    role: str
    tenant_id: str
    status: str
    created_at: str
    updated_at: str
    last_login_at: str | None


def _row_to_user(row: tuple) -> User:
    (user_id, email, password_hash, name, role, tenant_id, status,
     created_at, updated_at, last_login_at) = row
    return User(
        user_id=user_id, email=email, password_hash=password_hash, name=name, role=role,
        tenant_id=tenant_id, status=status, created_at=str(created_at), updated_at=str(updated_at),
        last_login_at=str(last_login_at) if last_login_at else None,
    )


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists auth")
    con.execute(
        """
        create table if not exists auth.users (
          user_id varchar primary key,
          email varchar,
          password_hash varchar,
          name varchar,
          role varchar,
          tenant_id varchar,
          status varchar,
          created_at timestamptz,
          updated_at timestamptz,
          last_login_at timestamptz
        )
        """
    )
    con.execute(
        """
        create table if not exists auth.refresh_tokens (
          token_id varchar primary key,
          user_id varchar,
          issued_at timestamptz,
          expires_at timestamptz,
          revoked boolean
        )
        """
    )


def create_user(
    email: str, password: str, name: str, *, role: str, tenant_id: str,
    config: AuthConfig | None = None, db_path: Path = DUCKDB_PATH,
) -> User:
    config = config or load_auth_config()
    if role not in ROLES:
        raise AuthError(f"role must be one of {ROLES}, got {role!r}")
    if len(password) < config.password_min_length:
        raise AuthError(f"password must be at least {config.password_min_length} characters")

    now = utc_now()
    user = User(
        user_id=str(uuid.uuid4()), email=email.strip().lower(), password_hash=hash_password(password),
        name=name, role=role, tenant_id=tenant_id, status=STATUS_ACTIVE,
        created_at=now, updated_at=now, last_login_at=None,
    )
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        existing = con.execute("select user_id from auth.users where email = ?", [user.email]).fetchone()
        if existing is not None:
            raise AuthError(f"an account with email {user.email!r} already exists")
        con.execute(
            "insert into auth.users values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                user.user_id, user.email, user.password_hash, user.name, user.role, user.tenant_id,
                user.status, user.created_at, user.updated_at, user.last_login_at,
            ],
        )
    return user


def get_user_by_email(email: str, db_path: Path = DUCKDB_PATH) -> User | None:
    with connect_with_retry(db_path, read_only=True) as con:
        # No _ensure_tables() here - see multi_tenant/tenant_manager.py's
        # get_tenant() for why CREATE is never issued on a read-only
        # connection (DuckDB refuses it outright, even as a no-op).
        try:
            row = con.execute(
                "select user_id, email, password_hash, name, role, tenant_id, status, "
                "created_at, updated_at, last_login_at from auth.users where email = ?",
                [email.strip().lower()],
            ).fetchone()
        except Exception:
            return None
    return _row_to_user(row) if row else None


def get_user_by_id(user_id: str, db_path: Path = DUCKDB_PATH) -> User | None:
    with connect_with_retry(db_path, read_only=True) as con:
        try:
            row = con.execute(
                "select user_id, email, password_hash, name, role, tenant_id, status, "
                "created_at, updated_at, last_login_at from auth.users where user_id = ?",
                [user_id],
            ).fetchone()
        except Exception:
            return None
    return _row_to_user(row) if row else None


def list_users(tenant_id: str, db_path: Path = DUCKDB_PATH) -> list[User]:
    with connect_with_retry(db_path, read_only=True) as con:
        try:
            rows = con.execute(
                "select user_id, email, password_hash, name, role, tenant_id, status, "
                "created_at, updated_at, last_login_at from auth.users where tenant_id = ? order by created_at",
                [tenant_id],
            ).fetchall()
        except Exception:
            return []
    return [_row_to_user(row) for row in rows]


def record_login(user_id: str, db_path: Path = DUCKDB_PATH) -> None:
    now = utc_now()
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.execute("update auth.users set last_login_at = ?, updated_at = ? where user_id = ?", [now, now, user_id])


def authenticate(email: str, password: str, db_path: Path = DUCKDB_PATH) -> User:
    """Verifies email+password and returns the User on success. Raises
    AuthError for a wrong password, unknown email, or a disabled account -
    deliberately the SAME error/message for "wrong password" and "unknown
    email" (never reveal which one it was) to avoid leaking which emails
    have accounts."""
    user = get_user_by_email(email, db_path)
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("invalid email or password")
    if user.status != STATUS_ACTIVE:
        raise AuthError("this account has been disabled")
    record_login(user.user_id, db_path)
    return user


# ---------------------------------------------------------------------------
# Refresh tokens (auth.refresh_tokens) - opaque IDs embedded in the JWT's
# `jti` claim, so a refresh token can be individually revoked (logout)
# without invalidating every other session, unlike a stateless access token.
# ---------------------------------------------------------------------------


def issue_refresh_token(user: User, *, config: AuthConfig | None = None, db_path: Path = DUCKDB_PATH) -> str:
    config = config or load_auth_config()
    token_id = str(uuid.uuid4())
    now = time.time()
    expires_at = now + config.refresh_token_ttl_seconds
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.execute(
            "insert into auth.refresh_tokens values (?, ?, ?, ?, ?)",
            [token_id, user.user_id, utc_now(), _epoch_to_iso(expires_at), False],
        )
    return encode_jwt(
        {"sub": user.user_id, "jti": token_id, "type": "refresh"},
        config.jwt_secret, algorithm=config.jwt_algorithm, ttl_seconds=config.refresh_token_ttl_seconds,
    )


def revoke_refresh_token(token_id: str, db_path: Path = DUCKDB_PATH) -> None:
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.execute("update auth.refresh_tokens set revoked = true where token_id = ?", [token_id])


def is_refresh_token_valid(token_id: str, db_path: Path = DUCKDB_PATH) -> bool:
    with connect_with_retry(db_path, read_only=True) as con:
        try:
            row = con.execute(
                "select revoked, expires_at from auth.refresh_tokens where token_id = ?", [token_id]
            ).fetchone()
        except Exception:
            return False
    if row is None:
        return False
    revoked, expires_at = row
    if revoked:
        return False
    return _iso_to_epoch(str(expires_at)) > time.time()


def issue_access_token(user: User, *, config: AuthConfig | None = None) -> str:
    config = config or load_auth_config()
    return encode_jwt(
        {"sub": user.user_id, "email": user.email, "role": user.role, "tenant_id": user.tenant_id, "type": "access"},
        config.jwt_secret, algorithm=config.jwt_algorithm, ttl_seconds=config.access_token_ttl_seconds,
    )


def role_at_least(role: str, minimum: str) -> bool:
    """True if `role` outranks or equals `minimum` in ROLES' admin-down
    ordering (admin > tenant_admin > analyst > viewer) - used by
    auth_middleware.py's require_role() for "at least analyst" style checks
    rather than an exact-match role list."""
    return _ROLE_RANK.get(role, -1) >= _ROLE_RANK.get(minimum, len(ROLES))


def _epoch_to_iso(epoch_seconds: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat()


def _iso_to_epoch(iso_value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).timestamp()
