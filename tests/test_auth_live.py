"""Live scenarios for the Supabase-backed auth endpoints.

Every test talks to a real Supabase project:
- register / login / me happy paths and every failure branch;
- users are deleted afterwards via the admin API (see conftest).

They all implicitly depend on `supabase_env`, so the module skips
(doesn't fail) when no credentials are configured.
"""

from uuid import uuid4

import base64
import jwt as pyjwt


def test_register_creates_user_session_and_profile(
    client, supabase_env, register
):
    email, user, access_token = register(name="Ada Lovelace")
    assert user["email"] == email
    assert user["name"] == "Ada Lovelace"
    assert access_token


def test_me_returns_name_and_email(client, supabase_env, register):
    email, user, access_token = register(name="Grace Hopper")
    resp = client.get("/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": user["id"], "email": email, "name": "Grace Hopper"}


def test_register_duplicate_gmail_conflicts(client, supabase_env, tracked_users):
    email = f"pytest.{uuid4().hex[:12]}@gmail.com"
    payload = {"name": "Dup", "email": email, "password": "Sup3rSecure!"}
    first = client.post("/auth/register", json=payload)
    assert first.status_code == 201, first.text
    tracked_users.append(first.json()["user"]["id"])
    second = client.post("/auth/register", json=payload)
    assert second.status_code == 409, second.text


def test_register_rejects_non_gmail(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={
            "name": "Nope",
            "email": unique_email().replace("@gmail.com", "@outlook.com"),
            "password": "Sup3rSecure!",
        },
    )
    assert resp.status_code == 422
    assert "@gmail.com" in resp.text


def test_register_rejects_missing_name(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={"email": unique_email(), "password": "Sup3rSecure!"},
    )
    assert resp.status_code == 422


def test_register_rejects_blank_name(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={"name": "", "email": unique_email, "password": "Sup3rSecure!"},
    )
    assert resp.status_code == 422


def test_register_rejects_short_password(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={"name": "Shorty", "email": unique_email, "password": "short"},
    )
    assert resp.status_code == 422


def test_register_rejects_missing_fields(client, supabase_env):
    resp = client.post("/auth/register", json={})
    assert resp.status_code == 422


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


def test_me_without_token_rejected(client, supabase_env):
    resp = client.get("/me")
    assert resp.status_code == 401


def test_me_with_nonsense_token_rejected(client, supabase_env):
    resp = client.get("/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


def test_me_with_tampered_token_rejected(client, supabase_env, register):
    """Swap the payload of a valid token: signature stops verifying."""
    _, _, access_token = register()
    header_b64, _, signature_b64 = access_token.split(".")
    forged_payload = base64.urlsafe_b64encode(
        b'{"sub":"00000000-0000-0000-0000-000000000000","role":"authenticated","exp":99999999999}'
    ).rstrip(b"=")
    forged = f"{header_b64}.{forged_payload.decode()}.{signature_b64}"
    resp = client.get("/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401, resp.text


def test_me_with_self_signed_token_rejected(client, supabase_env):
    """A token signed by an unknown key (not the project's JWKS)."""
    token = pyjwt.encode(
        {"sub": str(uuid4()), "role": "authenticated", "exp": 99999999999},
        key="attacker-controlled-secret",
        algorithm="HS256",
    )
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, resp.text


def test_me_name_covers_user_not_profile(client, supabase_env, register):
    """The name must come from the profiles table (ADR-0002): verify a
    second user can't see the first user's name in their /me."""
    email, _, _ = register(name="First User")
    _, _, other_token = register(name="Second User")
    resp = client.get("/me", headers={"Authorization": f"Bearer {other_token}"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Second User"
