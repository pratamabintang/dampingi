"""Thin transport helpers for the Supabase REST API.

We deliberately call the stable `/auth/v1/*` and `/rest/v1/*` endpoints
directly instead of using the `supabase-py` SDK: fewer dependencies, and
the REST contract is platform-guaranteed.
"""

from typing import Any, Optional

import httpx

from app.config import supabase_auth_http, supabase_service_http, settings

TIMEOUT = 10.0


class SupabaseError(Exception):
    """Raised when Supabase does not answer 2xx."""

    def __init__(self, status: int, message: str, error_code: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.error_code = error_code


def _error_from_response(resp: httpx.Response) -> SupabaseError:
    error_code = None
    message = resp.text
    try:
        body = resp.json()
        error_code = body.get("error_code") or body.get("code")
        message = body.get("msg") or body.get("message") or resp.text
    except ValueError:
        pass
    return SupabaseError(resp.status_code, message, error_code)


def _request(func, *args, **kwargs) -> httpx.Response:
    """Call httpx, mapping transport failures to SupabaseError(503)."""
    try:
        return func(*args, **kwargs)
    except httpx.HTTPError as exc:
        raise SupabaseError(503, f"Supabase unreachable: {exc}") from exc


def signup(name: str, email: str, password: str) -> dict:
    """Create a Supabase auth user with `name` in signup metadata.

    Relies on a database trigger (docs/supabase-setup.md) copying the
    name from raw_user_meta_data into a `profiles` row.

    Returns the Supabase response body: {user, session} (session is None
    when email confirmation is still required).
    """
    resp = _request(
        httpx.post,
        f"{settings.supabase_url}/auth/v1/signup",
        headers=supabase_auth_http(),
        json={"email": email, "password": password, "data": {"name": name}},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    return resp.json()


def login(email: str, password: str) -> dict:
    """Password grant sign-in; returns the token response body."""
    resp = _request(
        httpx.post,
        f"{settings.supabase_url}/auth/v1/token",
        params={"grant_type": "password"},
        headers=supabase_auth_http(),
        json={"email": email, "password": password},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    return resp.json()


def fetch_profile(user_id: str) -> Optional[dict]:
    """Read a profiles row via PostgREST (`columns=user_id,name`).

    Returns None when no row exists for the user (e.g. the setup trigger
    was not installed yet), rather than raising.
    """
    resp = _request(
        httpx.get,
        f"{settings.supabase_url}/rest/v1/profiles",
        params={"user_id": f"eq.{user_id}", "select": "user_id,name"},
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    return rows[0] if rows else None


def admin_list_users(page: int = 1, per_page: int = 100) -> tuple:
    """One page of the admin user list.

    Returns (users, next_page, total): next_page is None on the last
    page; total is the project's total user count (x-total-count).
    """
    resp = _request(
        httpx.get,
        f"{settings.supabase_url}/auth/v1/admin/users",
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
        httpx.delete,
        f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
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
    return f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
