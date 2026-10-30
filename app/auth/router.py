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
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    UpdateMeRequest,
    UserOut,
)
from app.config import get_settings

router = APIRouter(tags=["auth"])

_ALGORITHMS = ("ES256", "RS256", "PS256", "EdDSA")

# Built on first use, not at import (ADR-0006): constructing these needs
# the project URL, and importing the app must not require credentials.
# PyJWKClient caches keys after the first discovery fetch
# (docs/supabase-setup.md explains the trade-off).
_jwk_client: Optional[PyJWKClient] = None


def _jwk() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(supabase.jwks_url(), cache_keys=True)
    return _jwk_client


def _issuer() -> str:
    return f"{get_settings().supabase_url.rstrip('/')}/auth/v1"

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
        signing_key = _jwk().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(_ALGORITHMS),
            audience="authenticated",
            issuer=_issuer(),
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


def _handle_auth_errors(func, *args):
    """Run a service call, mapping domain/transport errors to HTTP.

    One place owns the mapping ADR-0004 assigns to this layer, so no
    endpoint can forget a branch (each of five handlers used to repeat
    this pair).
    """
    try:
        return func(*args)
    except service.AccountDeleted as exc:
        raise _deleted() from exc
    except supabase.SupabaseError as exc:
        if _is_banned(exc):
            raise _deleted() from exc
        raise HTTPException(status_code=503, detail=exc.message) from exc


def _is_banned(exc: supabase.SupabaseError) -> bool:
    """Supabase refuses banned (soft-deleted) users with user_banned.

    Shared by login and logout so one condition speaks one language.
    """
    return exc.error_code == "user_banned"


@router.post(
    "/auth/register",
    status_code=201,
    response_model=RegisterResponse,
    summary="Register with name, Gmail address, password, and role",
)
def register(payload: RegisterRequest) -> RegisterResponse:
    try:
        account = service.register_user(
            payload.name, payload.email, payload.password, payload.role.value
        )
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
    summary="The registered name, email, and role of the caller",
)
def me(session: service.Session = Depends(verified_session)) -> UserOut:
    profile = _handle_auth_errors(service.active_profile, session.user_id)
    return service.profile_out(session, profile)


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
    _handle_auth_errors(service.logout, session)
    return Response(status_code=204)


@router.patch(
    "/me",
    response_model=UserOut,
    summary="Change the caller's name (role is immutable)",
    description=(
        "The only editable Profile field is the name. Sending any other "
        "field — including `role` — is rejected with 422: a silently "
        "ignored role would read as success to the caller (ADR-0005)."
    ),
)
def update_me(
    payload: UpdateMeRequest, session: service.Session = Depends(verified_session)
) -> UserOut:
    return _handle_auth_errors(service.update_name, session, payload.name)


@router.post(
    "/auth/password",
    status_code=204,
    summary="Change the caller's password (current password required)",
    description=(
        "Verifies `current_password` by attempting a login, then sets "
        "`new_password` and revokes every session — the caller must log in "
        "again. Other devices' live access tokens keep working until they "
        "expire (Supabase has no access-token revocation)."
    ),
)
def change_password(
    payload: ChangePasswordRequest,
    session: service.Session = Depends(verified_session),
):
    try:
        service.change_password(
            session, payload.current_password, payload.new_password
        )
    except service.AccountDeleted as exc:
        raise _deleted() from exc
    except service.CurrentPasswordIncorrect as exc:
        # 400, not 401: 401 means the bearer token failed verified_session
        raise HTTPException(status_code=400, detail="Current password is incorrect") from exc
    except service.PasswordUnchanged as exc:
        raise HTTPException(
            status_code=400, detail="New password must differ from the current one"
        ) from exc
    except supabase.SupabaseError as exc:
        # Reached when the verification login itself is refused upstream:
        # a banned-but-not-tombstoned caller gets user_banned here (the
        # tombstone check already ran, but an admin ban may exist without
        # one). Everything else is an upstream failure.
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
