"""User Accounts & Authentication - FastAPI layer (PHASE7-DEPLOYMENT.md
Section 3).

Thin FastAPI wrapper around `auth/auth_models.py`'s pure-Python logic (see
that module's docstring for why the split exists). Mounted into
`api/metrics_api.py` alongside `realtime_api.py`/`monitoring_api.py`/
`ml_api.py`'s routers.

Signup creates a brand-new tenant (via `multi_tenant/tenant_manager.py`'s
`generate_tenant_id()`) and makes the signing-up user that tenant's first
`tenant_admin` - the natural "someone signs up, gets their own workspace"
SaaS onboarding flow PHASE7-DEPLOYMENT.md Section 4's "tenant onboarding
wizard" fronts. Joining an *existing* tenant (an invited teammate) is a
separate, explicit path (`tenant_id` provided + `invite_token`) rather than
folding both into one endpoint that has to guess intent from which fields
are present - kept simple here (an invite token is just checked against the
inviting tenant's metadata, not a full invitation-email system, which is out
of scope for this phase).
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.auth_middleware import AuthenticatedUser, get_current_user
from auth.auth_models import (
    AuthError,
    authenticate,
    create_user,
    decode_jwt,
    get_user_by_id,
    is_refresh_token_valid,
    issue_access_token,
    issue_refresh_token,
    load_auth_config,
    revoke_refresh_token,
)
from multi_tenant.tenant_manager import (
    ISOLATION_POOLED,
    TenantError,
    create_tenant,
    generate_tenant_id,
    get_tenant,
    update_tenant_metadata,
    validate_tenant_id,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str
    organization_name: str = Field(..., description="Name for the new tenant/workspace this user is creating.")


class JoinRequest(BaseModel):
    email: str
    password: str
    name: str
    tenant_id: str
    invite_token: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    tenant_id: str


def _token_response(user) -> TokenResponse:
    config = load_auth_config()
    return TokenResponse(
        access_token=issue_access_token(user, config=config),
        refresh_token=issue_refresh_token(user, config=config),
        user_id=user.user_id, email=user.email, role=user.role, tenant_id=user.tenant_id,
    )


@router.post("/signup", response_model=TokenResponse)
def signup(body: SignupRequest) -> TokenResponse:
    config = load_auth_config()
    tenant_id = generate_tenant_id(body.organization_name)
    tenant = create_tenant(tenant_id, body.organization_name, isolation_policy=ISOLATION_POOLED)
    # An invite token any future teammate needs to join this tenant via
    # /auth/join - stored on the tenant record rather than a separate
    # invites table, since this phase's scope is "one shared invite secret
    # per tenant", not per-invitee single-use tokens.
    invite_token = secrets.token_urlsafe(24)
    update_tenant_metadata(tenant_id, {"invite_token": invite_token})
    try:
        user = create_user(body.email, body.password, body.name, role="tenant_admin", tenant_id=tenant.tenant_id, config=config)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _token_response(user)


@router.post("/join", response_model=TokenResponse)
def join(body: JoinRequest) -> TokenResponse:
    config = load_auth_config()
    try:
        validate_tenant_id(body.tenant_id)
    except TenantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tenant = get_tenant(body.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    if tenant.metadata.get("invite_token") != body.invite_token:
        raise HTTPException(status_code=403, detail="invalid invite token")
    try:
        user = create_user(body.email, body.password, body.name, role=config.default_role, tenant_id=tenant.tenant_id, config=config)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest) -> TokenResponse:
    try:
        user = authenticate(body.email, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return _token_response(user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest) -> TokenResponse:
    config = load_auth_config()
    try:
        claims = decode_jwt(body.refresh_token, config.jwt_secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="not a refresh token")
    if not is_refresh_token_valid(claims["jti"]):
        raise HTTPException(status_code=401, detail="refresh token revoked or expired")
    user = get_user_by_id(claims["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="user no longer exists")
    # Rotate: the old refresh token is revoked and a new one issued, so a
    # leaked refresh token has a limited further lifetime once its holder
    # uses it even once - standard refresh-token-rotation practice.
    revoke_refresh_token(claims["jti"])
    return _token_response(user)


@router.post("/logout")
def logout(body: LogoutRequest) -> dict[str, str]:
    config = load_auth_config()
    try:
        claims = decode_jwt(body.refresh_token, config.jwt_secret, verify_exp=False)
    except AuthError:
        # Already malformed/unverifiable - nothing to revoke, and logout
        # should never fail just because the client's token was already bad.
        return {"status": "ok"}
    if claims.get("type") == "refresh" and "jti" in claims:
        revoke_refresh_token(claims["jti"])
    return {"status": "ok"}


@router.get("/me")
def me(current_user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    return {
        "user_id": current_user.user_id, "email": current_user.email,
        "role": current_user.role, "tenant_id": current_user.tenant_id,
    }
