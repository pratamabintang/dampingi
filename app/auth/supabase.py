"""Supabase transport functions for the auth domain."""

from typing import Optional

import httpx

from app.config import supabase_auth_http, supabase_service_http
from app.supabase import (
    TIMEOUT,
    BAN_DURATION,
    _client,
    _base_url,
    _publishable_key,
    _error_from_response,
    _request,
    _utc_now_iso,
    SupabaseError,
)


def signup(name: str, email: str, password: str, role: str) -> dict:
    """Create a Supabase auth user with `name` and `role` in metadata.

    Relies on a database trigger (docs/supabase-setup.md) copying both
    from raw_user_meta_data into a `profiles` row. The role lands in
    user_metadata too — user-writable, so never authorize from it
    (ADR-0005); the trigger's profiles row is the source of truth.

    Returns the Supabase response body: {user, session} (session is None
    when email confirmation is still required).
    """
    resp = _request(
        _client().post,
        f"{_base_url()}/auth/v1/signup",
        headers=supabase_auth_http(),
        json={
            "email": email,
            "password": password,
            "data": {"name": name, "role": role},
        },
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    return resp.json()


def login(email: str, password: str) -> dict:
    """Password grant sign-in; returns the token response body."""
    resp = _request(
        _client().post,
        f"{_base_url()}/auth/v1/token",
        params={"grant_type": "password"},
        headers=supabase_auth_http(),
        json={"email": email, "password": password},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    return resp.json()


def update_profile_name(user_id: str, name: str) -> dict:
    """Update profiles.name via PostgREST (server-side, secret key).

    Returns the updated row. Raises SupabaseError when no row matched:
    a PATCH that wrote nothing must not report success.
    """
    resp = _request(
        _client().patch,
        f"{_base_url()}/rest/v1/profiles",
        params={"user_id": f"eq.{user_id}", "select": "user_id,name,deleted_at,role"},
        headers={**supabase_service_http(), "Prefer": "return=representation"},
        json={"name": name},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    if not rows:
        raise SupabaseError(404, f"No profile row for user {user_id}")
    return rows[0]


def update_password(user_token: str, new_password: str) -> None:
    """Set a new password for the token's user (PUT /auth/v1/user).

    Supabase performs NO check of the old password here — the caller
    must verify it first (service.change_password does, by attempting a
    login). Does not revoke existing sessions; the service does that.
    """
    resp = _request(
        _client().put,
        f"{_base_url()}/auth/v1/user",
        headers={
            "apikey": _publishable_key(),
            "Authorization": f"Bearer {user_token}",
            "Content-Type": "application/json",
        },
        json={"password": new_password},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)


def fetch_profile(user_id: str) -> Optional[dict]:
    """Read a profiles row via PostgREST (`select=user_id,name,deleted_at,role,timezone`).

    Returns None when no row exists for the user (e.g. the setup trigger
    was not installed yet), rather than raising.

    `timezone` is the IANA zone the Profile registered with (defaults to
    'UTC'); PostgREST returns only the columns named here, so a caller
    that needs the zone must get it from this select.
    """
    resp = _request(
        _client().get,
        f"{_base_url()}/rest/v1/profiles",
        params={
            "user_id": f"eq.{user_id}",
            "select": "user_id,name,deleted_at,role,timezone",
        },
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    return rows[0] if rows else None


def tombstone_profile(user_id: str) -> bool:
    """Set profiles.deleted_at (the soft-delete tombstone; ADR-0003).

    Idempotent: PATCHing again is a no-op (the column is not in the
    body — the DB default for `now()` is not used; we send it). Returns
    True when the row was updated.
    """
    resp = _request(
        _client().patch,
        f"{_base_url()}/rest/v1/profiles",
        params={"user_id": f"eq.{user_id}"},
        headers=supabase_service_http(),
        json={"deleted_at": _utc_now_iso()},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    return resp.status_code < 300


def ban_user(user_id: str) -> dict:
    """Ban the auth user for 100 years (soft delete, auth half; ADR-0003).

    Blocks login AND refresh-token grants (Supabase refuses banned users
    before verifying credentials); live access tokens still validate
    until expiry — revoke_sessions() is the companion call. Idempotent:
    re-banning an already-banned user just re-sets banned_until.
    """
    resp = _request(
        _client().put,
        f"{_base_url()}/auth/v1/admin/users/{user_id}",
        headers=supabase_service_http(),
        json={"ban_duration": BAN_DURATION},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    return resp.json()


def revoke_sessions(user_token: str, scope: str = "global") -> None:
    """Revoke refresh tokens for the token's user.

    Uses the user's own access token (auth, not admin) — this is the
    standard logout endpoint. Note the current access token stays valid
    until it expires; Supabase has no access-token revocation.

    scope="global" (default) kills every session the user holds;
    scope="local" kills only the session the token belongs to. Use
    "local" when another session must survive (change_password revokes
    its throwaway verification session this way).

    Raises SupabaseError(403, error_code="session_not_found") when the
    session was already revoked (a repeat logout).
    """
    resp = _request(
        _client().post,
        f"{_base_url()}/auth/v1/logout",
        params={"scope": scope},
        headers={
            "apikey": _publishable_key(),
            "Authorization": f"Bearer {user_token}",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)


def update_own_metadata(user_token: str, metadata: dict) -> dict:
    """Overwrite the caller's user_metadata (PUT /auth/v1/user).

    Exposed because the app must never *rely* on it: metadata is
    user-writable with the publishable key. Tests use it to prove that a
    forged `role` claim does not change what the API reports (ADR-0005).
    """
    resp = _request(
        _client().put,
        f"{_base_url()}/auth/v1/user",
        headers={
            "apikey": _publishable_key(),
            "Authorization": f"Bearer {user_token}",
            "Content-Type": "application/json",
        },
        json={"data": metadata},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    return resp.json()


def refresh_grant(refresh_token: str) -> httpx.Response:
    """Attempt a refresh-token grant; returns the raw response.

    Tests use it to observe session revocation (the API never exposes
    refresh tokens). Status >= 400 means the session is dead.
    """
    return _request(
        _client().post,
        f"{_base_url()}/auth/v1/token",
        params={"grant_type": "refresh_token"},
        json={"refresh_token": refresh_token},
        headers=supabase_auth_http(),
        timeout=TIMEOUT,
    )


def admin_list_users(page: int = 1, per_page: int = 100) -> tuple:
    """One page of the admin user list.

    Returns (users, next_page, total): next_page is None on the last
    page; total is the project's total user count (x-total-count).
    """
    resp = _request(
        _client().get,
        f"{_base_url()}/auth/v1/admin/users",
        params={"page": page, "per_page": per_page},
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    body = resp.json()
    total = resp.headers.get("x-total-count")
    return body.get("users", []), body.get("next_page"), total


def delete_user(user_id: str) -> bool:
    """Delete an auth user via the admin API (test cleanup).

    The profiles row is removed automatically by the FK cascade.
    """
    resp = _request(
        _client().delete,
        f"{_base_url()}/auth/v1/admin/users/{user_id}",
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code < 300:
        return True
    # 404 = user already gone; acceptable for cleanup
    if resp.status_code == 404:
        return False
    raise _error_from_response(resp)


def jwks_url() -> str:
    """Public key discovery endpoint (asymmetric signing keys)."""
    return f"{_base_url()}/auth/v1/.well-known/jwks.json"
