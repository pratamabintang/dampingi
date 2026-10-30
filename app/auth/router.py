"""API surface of the auth domain: routes, status codes, token auth.

Identity is delegated to Supabase Auth (ADR-0001); the user's profile
(name) lives in a Postgres table populated by an auth trigger (ADR-0002),
which is why /me does one local JWT verification plus one profile read.
"""

from enum import Enum
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app import supabase
from app.auth import service
from app.auth.schemas import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    UserOut,
)
from app.config import settings

router = APIRouter(tags=["auth"])

_ALGORITHMS = ("ES256", "RS256", "PS256", "EdDSA")
_issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"

# Cached JWKS fetch: PyJWKClient caches keys after the first request to
# the discovery endpoint (docs/supabase-setup.md explains the trade-off).
_jwk_client = PyJWKClient(supabase.jwks_url(), cache_keys=True)

bearer = HTTPBearer(auto_error=False)


def verified_session(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> service.Session:
    """Dependency: verify a Supabase-issued access token locally.

    Raises 401 when the token is missing, malformed, expired, signed by
    an unknown key, or issued by another project.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = credentials.credentials
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(_ALGORITHMS),
            audience="authenticated",
            issuer=_issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWKClientError as exc:
        raise HTTPException(
            status_code=401,
            detail=(
                "Token rejected: no trusted signing key matched. If your "
                "Supabase project still uses the legacy JWT secret, "
                "migrate to signing keys (docs/supabase-setup.md)."
            ),
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    return service.Session(user_id=claims["sub"], email=claims.get("email", ""), token=token)


def _deleted() -> HTTPException:
    return HTTPException(status_code=403, detail="This account has been deleted")


def _is_banned(exc: supabase.SupabaseError) -> bool:
    """Supabase refuses banned (soft-deleted) users with user_banned.

    Shared by login and logout so one condition speaks one language.
    """
    return exc.error_code == "user_banned"


@router.post(
    "/auth/register",
    status_code=201,
    response_model=RegisterResponse,
    summary="Register with name, Gmail address, and password",
)
def register(payload: RegisterRequest) -> RegisterResponse:
    try:
        account = service.register_user(payload.name, payload.email, payload.password)
    except supabase.SupabaseError as exc:
        if exc.status == 503:
            raise HTTPException(status_code=503, detail=exc.message) from exc
        if exc.error_code in ("user_already_exists", "email_exists") or (
            "already registered" in exc.message.lower()
            or "already been registered" in exc.message.lower()
        ):
            raise HTTPException(
                status_code=409, detail="This Gmail address is already registered"
            ) from exc
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return RegisterResponse(user=service.user_out(account), access_token=account.token or None)


@router.post(
    "/auth/login",
    response_model=LoginResponse,
    summary="Log in with Gmail address and password",
)
def login(payload: LoginRequest) -> LoginResponse:
    try:
        account = service.login_user(payload.email, payload.password)
    except supabase.SupabaseError as exc:
        if exc.status == 503:
            raise HTTPException(status_code=503, detail=exc.message) from exc
        # A soft-deleted user: Supabase bans before verifying the
        # password, with a distinguishable error_code (ADR-0003).
        if _is_banned(exc):
            raise _deleted() from exc
        # Supabase returns 400 (or 422) for both unknown emails and wrong
        # passwords; we don't distinguish to avoid account enumeration
        raise HTTPException(status_code=401, detail="Invalid email or password") from exc
    return LoginResponse(access_token=account.token, user=service.user_out(account))


@router.get(
    "/me",
    response_model=UserOut,
    summary="The registered name + email of the caller",
)
def me(session: service.Session = Depends(verified_session)) -> UserOut:
    try:
        profile = service.active_profile(session.user_id)
    except service.AccountDeleted as exc:
        raise _deleted() from exc
    except supabase.SupabaseError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    return UserOut(id=session.user_id, email=session.email, name=profile.get("name"))


@router.post(
    "/auth/logout",
    status_code=204,
    summary="Log out: revoke every session for the caller",
    description=(
        "Revokes all refresh tokens (scope=global), so no new access tokens "
        "can be minted from any of the caller's sessions. The current access "
        "token is NOT invalidated: Supabase has no access-token revocation, "
        "so it keeps verifying until it expires. Idempotent — calling it "
        "again is a no-op that still returns 204."
    ),
)
def logout(session: service.Session = Depends(verified_session)):
    try:
        service.logout(session)
    except service.AccountDeleted as exc:
        raise _deleted() from exc
    except supabase.SupabaseError as exc:
        if _is_banned(exc):
            raise _deleted() from exc
        raise HTTPException(status_code=503, detail=exc.message) from exc
    return Response(status_code=204)


@router.delete(
    "/me",
    status_code=204,
    summary="Soft-delete the caller's account (data is retained)",
)
def delete_me(session: service.Session = Depends(verified_session)):
    """Soft delete (ADR-0003): ban the auth identity, tombstone the
    Profile, revoke sessions. No row is destroyed; see service.py.
    """
    try:
        service.soft_delete_user(session)
    except service.AccountDeleted as exc:
        raise _deleted() from exc
    except supabase.SupabaseError as exc:
        # Partial delete is safe by construction (see service.py);
        # the whole call is idempotent — retry completes it.
        raise HTTPException(status_code=503, detail=exc.message) from exc
    return Response(status_code=204)
