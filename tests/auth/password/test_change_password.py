"""POST /auth/password: change the credential, verify the current one first."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.live


def test_change_password_then_login_with_new(client, supabase_env, register):
    email, _, token = register()
    resp = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "Sup3rSecure!", "new_password": "N3wSecure!pass"},
    )
    assert resp.status_code == 204, resp.text

    old = client.post(
        "/auth/login", json={"email": email, "password": "Sup3rSecure!"}
    )
    assert old.status_code == 401, "old password still works"

    new = client.post(
        "/auth/login", json={"email": email, "password": "N3wSecure!pass"}
    )
    assert new.status_code == 200, new.text


def test_change_password_rejects_wrong_current(client, supabase_env, register):
    email, _, token = register()
    resp = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "WRONGpass123!", "new_password": "N3wSecure!pass"},
    )
    assert resp.status_code == 400, resp.text
    assert "incorrect" in resp.json()["detail"].lower()

    # nothing changed: the original password still works
    login = client.post(
        "/auth/login", json={"email": email, "password": "Sup3rSecure!"}
    )
    assert login.status_code == 200, "password changed despite a failed check"


def test_change_password_rejects_unchanged(client, supabase_env, register):
    _, _, token = register()
    resp = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "Sup3rSecure!", "new_password": "Sup3rSecure!"},
    )
    assert resp.status_code == 400, resp.text


def test_change_password_rejects_short_new(client, supabase_env, register):
    _, _, token = register()
    resp = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "Sup3rSecure!", "new_password": "short"},
    )
    assert resp.status_code == 422, resp.text


def test_change_password_revokes_sessions(client, supabase_env, register):
    """A password change kills refresh tokens (scope=global)."""
    email, _, token = register()
    from app import supabase as sb

    session = sb.login(email, "Sup3rSecure!")
    resp = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "Sup3rSecure!", "new_password": "N3wSecure!pass"},
    )
    assert resp.status_code == 204, resp.text
    assert sb.refresh_grant(session["refresh_token"]).status_code >= 400


def test_change_password_for_deleted_user_is_403(client, supabase_env, register):
    _, _, token = register()
    assert client.delete("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    resp = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "Sup3rSecure!", "new_password": "N3wSecure!pass"},
    )
    assert resp.status_code == 403, resp.text
