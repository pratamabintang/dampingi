"""Soft delete (ADR-0003): the auth identity is banned and the Profile is
tombstoned, but no row is destroyed."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.live


def test_delete_me_soft_deletes_and_returns_204(client, supabase_env, register):
    """DELETE /me: 204, and the user is banned + tombstoned, not gone."""
    email, user, token = register(name="To Delete")
    resp = client.delete("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204, resp.text

    # Both halves of the soft delete happened: banned auth identity...
    from app import admin as admin_mod

    users = [u for u in admin_mod.all_user_ids() if u.get("id") == user["id"]]
    assert users, "auth user row destroyed — soft delete must retain it"
    assert users[0].get("banned_until"), "auth identity not banned"

    # ...and the tombstone on profiles.
    from app.auth import supabase as sb

    profile = sb.fetch_profile(user["id"])
    assert profile and profile.get("deleted_at"), "profile not tombstoned"


def test_delete_me_is_idempotent(client, supabase_env, register):
    """Re-deleting an already-deleted user: 403 'deleted', no error."""
    _, _, token = register()
    first = client.delete("/me", headers={"Authorization": f"Bearer {token}"})
    assert first.status_code == 204, first.text
    second = client.delete("/me", headers={"Authorization": f"Bearer {token}"})
    assert second.status_code == 403, second.text
    assert "deleted" in second.json()["detail"].lower()


def test_deleted_user_login_is_403_not_401(client, supabase_env, register):
    """A soft-deleted user must not be told 'invalid credentials'."""
    email, _, token = register()
    assert client.delete("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    resp = client.post(
        "/auth/login", json={"email": email, "password": "Sup3rSecure!"}
    )
    assert resp.status_code == 403, resp.text
    assert "deleted" in resp.json()["detail"].lower()


def test_deleted_user_re_register_conflicts(client, supabase_env, register):
    """History retention: the Gmail stays taken even after deletion."""
    email, _, token = register()
    assert client.delete("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    resp = client.post(
        "/auth/register",
        json={
            "name": "Again",
            "email": email,
            "password": "Sup3rSecure!",
            "role": "mother",
        },
    )
    assert resp.status_code == 409, resp.text


def test_deleted_user_me_rejected_with_old_token(client, supabase_env, register):
    """The tombstone check (not token expiry) stops a live JWT."""
    _, _, token = register()
    assert client.delete("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, resp.text
    assert "deleted" in resp.json()["detail"].lower()


def test_deleted_user_refresh_is_dead(client, supabase_env, register):
    """scope=global logout revoked the refresh token: no new tokens."""
    email, _, _ = register()
    # grab a refresh token via direct transport login (app /auth/login
    # doesn't return one)
    from app import supabase as sb

    body = sb.login(email, "Sup3rSecure!")
    token = body["access_token"]
    assert client.delete("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    # Through the transport, not the TestClient: a TestClient request to an
    # absolute Supabase URL is routed to this app, not the network, and
    # 404s — which would satisfy ">= 400" without ever contacting Supabase.
    resp = sb.refresh_grant(body["refresh_token"])
    assert resp.status_code >= 400, "refresh grant should fail after soft delete"
