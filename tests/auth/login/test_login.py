"""Login: password grant, and the token it returns."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.live


def test_login_returns_matching_user_and_token(client, supabase_env, register):
    email, user, _ = register()
    resp = client.post(
        "/auth/login", json={"email": email, "password": "Sup3rSecure!"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["id"] == user["id"]
    assert body["user"]["email"] == email
    assert body["access_token"]


def test_login_token_grants_me(client, supabase_env, register):
    email, user, _ = register()
    login = client.post(
        "/auth/login", json={"email": email, "password": "Sup3rSecure!"}
    )
    token = login.json()["access_token"]
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == user["id"]


def test_login_wrong_password_rejected(client, supabase_env, register):
    email, _, _ = register()
    resp = client.post(
        "/auth/login", json={"email": email, "password": "WRONGpass123!"}
    )
    assert resp.status_code == 401, resp.text


def test_login_unknown_email_rejected(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/login", json={"email": unique_email(), "password": "Sup3rSecure!"}
    )
    assert resp.status_code == 401
