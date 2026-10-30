"""Auth endpoints: registration, login, and the authenticated /me.

Identity is delegated to Supabase Auth (ADR-0001); the user's profile
(name) lives in a Postgres table populated by an auth trigger (ADR-0002),
which is why /me does one local JWT verification plus one profile read.
"""

from enum import Enum
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from pydantic import BaseModel, EmailStr, Field, field_validator

import supabase
from config import settings

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
    session = body.get("session") or {}
    profile = supabase.fetch_profile(user.get("id", ""))
    return RegisterResponse(
        user=UserOut(
            id=user.get("id", ""),
            email=user.get("email", payload.email),
            name=(profile or {}).get("name"),
        ),
        access_token=session.get("access_token"),
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
        # Supabase returns 400 (or 422) for both unknown emails and wrong
        # passwords; we don't distinguish to avoid account enumeration
        raise HTTPException(
            status_code=401, detail="Invalid email or password"
        ) from exc

    user = body.get("user") or {}
    profile = supabase.fetch_profile(user.get("id", ""))
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
    return UserOut(
        id=user_id,
        email=claims.get("email", ""),
        name=(profile or {}).get("name"),
    )
