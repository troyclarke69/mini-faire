"""User Accounts & Authentication - FastAPI middleware & dependencies
(PHASE7-DEPLOYMENT.md Section 3).

Three concerns, each independently usable:

- `get_current_user` / `require_role` / `require_tenant`: FastAPI
  dependency functions that decode+validate the caller's access token
  (`Authorization: Bearer <jwt>`) and enforce role/tenant checks. Route
  handlers opt in per-endpoint via `Depends(...)` (see `auth/auth_api.py`'s
  `/me` for the simplest case) - nothing here forces every route in the app
  to require auth, since `api/metrics_api.py`'s existing Phase 3-6 endpoints
  stay open by design (this is a local demo; see README's "ML layer" and
  "Monitoring" sections - they were never gated behind auth, and Phase 7
  doesn't retroactively lock them down, only the new tenant/auth-aware
  surface is protected).
- `TokenBucket` / `RateLimiter`: a plain in-memory token-bucket rate
  limiter with NO FastAPI/Starlette dependency in its core logic, so it's
  unit-testable standalone (same "keep the pure logic importable without
  the web framework" split `auth/auth_models.py`'s docstring explains).
- `RateLimitMiddleware`: the thin Starlette `BaseHTTPMiddleware` wrapper
  around `RateLimiter`, added via `app.add_middleware(RateLimitMiddleware)`
  in `api/metrics_api.py`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from auth.auth_models import AuthError, ROLE_ADMIN, decode_jwt, load_auth_config, role_at_least
from multi_tenant.tenant_manager import validate_tenant_access


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str
    role: str
    tenant_id: str


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    return token


def get_current_user(request: Request) -> AuthenticatedUser:
    """The base dependency every other auth check builds on: validates the
    bearer access token and resolves it to an AuthenticatedUser. Does NOT
    hit the database on every call - the JWT's claims (sub/email/role/
    tenant_id, set at issuance by auth/auth_models.py's issue_access_token())
    are trusted for the token's lifetime, same tradeoff every stateless-JWT
    design makes: a role change or account disable takes effect on that
    user's NEXT token refresh, not instantly. `access_token_ttl_seconds`
    (config/auth.yaml, 15 minutes by default) bounds how stale that can get."""
    config = load_auth_config()
    token = _extract_bearer_token(request.headers.get("authorization"))
    try:
        claims = decode_jwt(token, config.jwt_secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if claims.get("type") != "access":
        raise HTTPException(status_code=401, detail="not an access token")
    return AuthenticatedUser(
        user_id=claims.get("sub", ""), email=claims.get("email", ""),
        role=claims.get("role", ""), tenant_id=claims.get("tenant_id", ""),
    )


def require_role(minimum_role: str) -> Callable[..., AuthenticatedUser]:
    """`Depends(require_role("analyst"))` - passes for analyst/tenant_admin/
    admin, rejects viewer, per auth_models.py's ROLES admin-down ranking."""

    def _dependency(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if not role_at_least(current_user.role, minimum_role):
            raise HTTPException(status_code=403, detail=f"requires role '{minimum_role}' or higher")
        return current_user

    return _dependency


def require_tenant(tenant_id_param: str = "tenant_id") -> Callable[..., AuthenticatedUser]:
    """`Depends(require_tenant())` - rejects a request whose path/query
    `tenant_id_param` doesn't match the caller's own tenant_id, UNLESS the
    caller is a platform `admin` (the one role that's allowed to cross
    tenant boundaries - e.g. a support operator looking at a specific
    tenant's data). A route with no `tenant_id` in its path/query params
    passes through unchanged - this dependency only enforces a match when
    there's something to match against."""

    def _dependency(request: Request, current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        requested_tenant_id = request.path_params.get(tenant_id_param) or request.query_params.get(tenant_id_param)
        if requested_tenant_id is not None and current_user.role != ROLE_ADMIN:
            if not validate_tenant_access(requested_tenant_id, current_user.tenant_id):
                raise HTTPException(status_code=403, detail="not authorized for this tenant")
        return current_user

    return _dependency


# ---------------------------------------------------------------------------
# Rate limiting - pure logic (TokenBucket/RateLimiter), no FastAPI dependency
# ---------------------------------------------------------------------------


class TokenBucket:
    """Classic token bucket: `capacity` tokens available at once (bursts up
    to that many requests), refilling continuously at `refill_per_second`.
    No FastAPI/Starlette import here - unit-testable standalone."""

    def __init__(self, capacity: int, refill_per_second: float):
        self.capacity = float(capacity)
        self.refill_per_second = refill_per_second
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()

    def allow(self, *, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        elapsed = max(0.0, now - self.last_refill)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """One TokenBucket per client key (JWT `sub` if the request carries a
    decodable access token, else client IP - see RateLimitMiddleware
    below). Plain dict, not thread/process-safe beyond Python's GIL - a
    single-process, single-worker limiter, sufficient for this demo's
    single uvicorn worker. `infra/cloud/api_gateway.yaml` documents the
    shared, multi-instance-safe limiter a horizontally-scaled real
    deployment would need instead (e.g. a Redis-backed bucket at the
    gateway, ahead of every backend instance)."""

    def __init__(self, *, requests_per_minute: int, burst: int):
        self.requests_per_minute = requests_per_minute
        self.burst = burst
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, client_key: str) -> bool:
        bucket = self._buckets.get(client_key)
        if bucket is None:
            bucket = TokenBucket(self.burst, self.requests_per_minute / 60.0)
            self._buckets[client_key] = bucket
        return bucket.allow()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """`app.add_middleware(RateLimitMiddleware)` in api/metrics_api.py.
    Reads config/auth.yaml's rate_limit block once at startup; a request
    over the limit gets a 429 with no downstream handler ever invoked."""

    def __init__(self, app):
        super().__init__(app)
        config = load_auth_config()
        self._enabled = config.rate_limit_enabled
        self._limiter = RateLimiter(
            requests_per_minute=config.rate_limit_requests_per_minute, burst=config.rate_limit_burst
        )

    async def dispatch(self, request: Request, call_next):
        if not self._enabled:
            return await call_next(request)
        client_key = self._client_key(request)
        if not self._limiter.allow(client_key):
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        return await call_next(request)

    @staticmethod
    def _client_key(request: Request) -> str:
        authorization = request.headers.get("authorization")
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
            try:
                # Best-effort: decode without verifying expiry just to key
                # the bucket by user rather than IP for authenticated
                # traffic - an invalid/expired token still gets a real key
                # via the IP fallback below, and get_current_user() (which
                # DOES verify) still rejects it downstream regardless.
                config = load_auth_config()
                claims = decode_jwt(token, config.jwt_secret, verify_exp=False)
                if "sub" in claims:
                    return f"user:{claims['sub']}"
            except AuthError:
                pass
        client = request.client
        return f"ip:{client.host}" if client else "ip:unknown"
