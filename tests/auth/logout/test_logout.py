"""Logout revokes every server-side session (scope=global refresh tokens).
It does NOT invalidate the current access token: Supabase has no
access-token revocation, so a live JWT keeps verifying until it expires.
That limitation is the contract, not a bug (parent repo: .scratch/logout/spec.md)."""

from uuid import uuid4

import base64
import jwt as pyjwt
import pytest

pytestmark = pytest.mark.live


def test_logout_returns_204(client, supabase_env, register):
    _, _, token = register(name="Logging Out")
    resp = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204, resp.text


def test_logout_for_deleted_user_is_403(client, supabase_env, register):
    """A deleted user's live token can reach the endpoint; the tombstone
    check must stop it (same invariant as GET /me)."""
    _, _, token = register()
    assert client.delete("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    resp = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, resp.text
    assert "deleted" in resp.json()["detail"].lower()


def test_logout_is_idempotent(client, supabase_env, register):
    """Logging out twice with the same token still succeeds (204)."""
    _, _, token = register()
    headers = {"Authorization": f"Bearer {token}"}
    first = client.post("/auth/logout", headers=headers)
    assert first.status_code == 204, first.text
    second = client.post("/auth/logout", headers=headers)
    assert second.status_code == 204, second.text


def test_logout_kills_every_session(client, supabase_env, register):
    """scope=global: logging out with ONE session's token must also kill
    a second, independent session's refresh token. (The app never returns
    refresh tokens, so direct transport calls are the only way to see it.)
    """
    email, _, _ = register()
    from app import supabase as sb

    first = sb.login(email, "Sup3rSecure!")   # session A
    second = sb.login(email, "Sup3rSecure!")  # session B

    resp = client.post(
        "/auth/logout", headers={"Authorization": f"Bearer {first['access_token']}"}
    )
    assert resp.status_code == 204, resp.text

    for label, token in (("A (logged out)", first), ("B (other device)", second)):
        refresh = sb.refresh_grant(token["refresh_token"])
        assert refresh.status_code >= 400, f"session {label} survived logout"


def test_login_still_works_after_logout(client, supabase_env, register):
    """Logout is not delete: the user survives and can log back in."""
    email, _, token = register()
    assert client.post(
        "/auth/logout", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 204

    resp = client.post(
        "/auth/login", json={"email": email, "password": "Sup3rSecure!"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


def test_logout_for_banned_but_not_tombstoned_user_is_403(
    client, supabase_env, register
):
    """Admin-banned in the dashboard (no tombstone): Supabase rejects the
    logout with user_banned; the router maps it to the deleted-403 so one
    condition speaks one language across the API (as login does)."""
    _, user, token = register()
    from app import supabase as sb

    sb.ban_user(user["id"])  # no tombstone: deleted_at stays NULL

    resp = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, resp.text
    assert "deleted" in resp.json()["detail"].lower()
