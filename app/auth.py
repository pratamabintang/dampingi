"""Auth endpoints: registration, login, and the authenticated /me.

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
from pydantic import BaseModel, EmailStr, Field, field_validator

from app import supabase
from app.config import settings

router = APIRouter(tags=["auth"])

_ALGORITHMS = ("ES256", "RS256", "PS256", "EdDSA")
_issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"

# Cached JWKS fetch: PyJWKClient caches keys after the first request to
# the discovery endpoint (docs/supabase-setup.md explains the trade-off).
_jwk_client = PyJWKClient(supabase.jwks_url(), cache_keys=True)

bearer = HTTPBearer(auto_error=False)


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    # Supabase's default minimum password length is 8
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def must_be_gmail(cls, value: EmailStr) -> EmailStr:
        if not value.lower().endswith("@gmail.com"):
            raise ValueError("registration is restricted to Gmail addresses")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    id: str
    email: str
    name: Optional[str] = None


class RegisterResponse(BaseModel):
    user: UserOut
    # None only when email confirmation is enabled on the Supabase
    # project (we set it up disabled; see docs/supabase-setup.md)
    access_token: Optional[str] = None


class LoginResponse(BaseModel):
    access_token: str
    user: UserOut


@router.post(
    "/auth/register",
    status_code=201,
    response_model=RegisterResponse,
    summary="Register with name, Gmail address, and password",
)
def register(payload: RegisterRequest) -> RegisterResponse:
    try:
        body = supabase.signup(payload.name, payload.email, payload.password)
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
        raise HTTPException(
            status_code=400, detail=exc.message
        ) from exc

    user = body.get("user") or {}
    # Signup responses come in two shapes: flat (access_token at top
    # level — current API) or {user, session}. Handle both; the token
    # is None only when email confirmation is enabled on the project.
    session = body.get("session") or {}
    access_token = body.get("access_token") or session.get("access_token")
    try:
        profile = supabase.fetch_profile(user.get("id", ""))
    except supabase.SupabaseError as exc:
        # The auth user was created; the profile store is misconfigured
        # (e.g. the profiles table / trigger was never created — see
        # docs/supabase-setup.md). 503 rather than a bare 500 so the
        # failure is identifiable, though retrying now yields 409.
        raise HTTPException(status_code=503, detail=exc.message) from exc
    return RegisterResponse(
        user=UserOut(
            id=user.get("id", ""),
            email=user.get("email", payload.email),
            name=(profile or {}).get("name"),
        ),
        access_token=access_token,
    )


@router.post(
    "/auth/login",
    response_model=LoginResponse,
    summary="Log in with Gmail address and password",
)
def login(payload: LoginRequest) -> LoginResponse:
    try:
        body = supabase.login(payload.email, payload.password)
    except supabase.SupabaseError as exc:
        if exc.status == 503:
            raise HTTPException(status_code=503, detail=exc.message) from exc
        # A soft-deleted user: Supabase bans before verifying the
        # password, with a distinguishable error_code (ADR-0003).
        if exc.error_code == "user_banned":
            raise HTTPException(
                status_code=403, detail="This account has been deleted"
            ) from exc
        # Supabase returns 400 (or 422) for both unknown emails and wrong
        # passwords; we don't distinguish to avoid account enumeration
        raise HTTPException(
            status_code=401, detail="Invalid email or password"
        ) from exc

    user = body.get("user") or {}
    try:
        profile = supabase.fetch_profile(user.get("id", ""))
    except supabase.SupabaseError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    token = body.get("access_token")
    assert token is not None  # Supabase always returns one on password grant
    return LoginResponse(
        access_token=token,
        user=UserOut(
            id=user.get("id", ""),
            email=user.get("email", payload.email),
            name=(profile or {}).get("name"),
        ),
    )


def verified_claims(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> dict:
    """Dependency: verify a Supabase-issued access token locally.

    Raises 401 when the token is missing, malformed, expired, signed by
    an unknown key, or issued by another project. The returned dict
    carries the verified claims plus the raw token under "_token" (used
    by delete-me to revoke the session it arrived on).
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
    claims["_token"] = token
    return claims


@router.get(
    "/me",
    response_model=UserOut,
    summary="The registered name + email of the caller",
)
def me(claims: dict = Depends(verified_claims)) -> UserOut:
    user_id = claims["sub"]
    try:
        profile = supabase.fetch_profile(user_id)
    except supabase.SupabaseError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    if profile and profile.get("deleted_at"):
        # Deleted users can't act (ADR-0003): the tombstone check, not
        # token expiry, is what stops a still-valid JWT from working.
        raise HTTPException(status_code=403, detail="This account has been deleted")
    return UserOut(
        id=user_id,
        email=claims.get("email", ""),
        name=(profile or {}).get("name"),
    )


@router.delete(
    "/me",
    status_code=204,
    summary="Soft-delete the caller's account (data is retained)",
)
def delete_me(claims: dict = Depends(verified_claims)):
    """Soft delete (ADR-0003): ban the auth identity, tombstone the
    Profile, revoke sessions. No row is destroyed.

    Write order is deliberate — revoke, ban, then tombstone. The logout
    must come first: Supabase rejects the logout of a *banned* user's
    token ("User is banned"), so it can only run pre-ban. Ban-first
    ordering (logout after ban) fails on every call. The ban then blocks
    login AND refresh grants; the tombstone last records the deletion in
    our data layer. A crash between writes fails closed on login (the
    dangerous boundary) from the ban onward; the whole call is
    idempotent, so a retry after 503 completes it.
    """
    user_id = claims["sub"]

    # Already deleted? Idempotent re-delete: nothing left to do.
    try:
        profile = supabase.fetch_profile(user_id)
    except supabase.SupabaseError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    if profile and profile.get("deleted_at"):
        raise HTTPException(status_code=403, detail="This account has been deleted")

    try:
        # 1. Revoke refresh tokens (scope=global), with the caller's own
        #    still-valid token — impossible after the ban, hence first.
        #    Also keeps sessions dead through any future restore.
        supabase.revoke_sessions(claims["_token"])
        # 2. Ban: Supabase itself refuses login + refresh for this user.
        supabase.ban_user(user_id)
        # 3. Tombstone: our data layer's record of deletion.
        supabase.tombstone_profile(user_id)
    except supabase.SupabaseError as exc:
        # Partial delete is safe by construction: whatever step failed,
        # login is blocked from step 2 onward; retry completes the rest.
        raise HTTPException(status_code=503, detail=exc.message) from exc
    return Response(status_code=204)
