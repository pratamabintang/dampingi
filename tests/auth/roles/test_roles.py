"""Roles (ADR-0005): exactly one per Profile, chosen at registration and
immutable. Authorization data lives in profiles.role, never the JWT."""

from uuid import uuid4

import base64
import pytest

pytestmark = pytest.mark.live


def _claims(token: str) -> dict:
    """Decode a JWT's claims WITHOUT verifying the signature.

    Test-only helper: proves that a claim can say one thing while
    profiles says another (ADR-0005). Never authorize from this.
    """
    import json

    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def test_register_accepts_each_role(client, supabase_env, unique_email, tracked_users):
    for role in ("mother", "companion", "professional"):
        email = unique_email()
        resp = client.post(
            "/auth/register",
            json={
                "name": f"A {role}",
                "email": email,
                "password": "Sup3rSecure!",
                "role": role,
            },
        )
        assert resp.status_code == 201, f"{role}: {resp.text}"
        body = resp.json()
        tracked_users.append(body["user"]["id"])
        assert body["user"]["role"] == role


def test_role_appears_in_login_and_me(client, supabase_env, register):
    email, user, token = register(role="professional")
    assert user["role"] == "professional"

    me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "professional"

    login = client.post(
        "/auth/login", json={"email": email, "password": "Sup3rSecure!"}
    )
    assert login.status_code == 200, login.text
    assert login.json()["user"]["role"] == "professional"


def test_register_rejects_unknown_role(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={
            "name": "Nope",
            "email": unique_email(),
            "password": "Sup3rSecure!",
            "role": "admin",
        },
    )
    assert resp.status_code == 422, resp.text


def test_register_rejects_wrongly_cased_role(client, supabase_env, unique_email):
    """One spelling only: `Mother` is not `mother` (Q18)."""
    resp = client.post(
        "/auth/register",
        json={
            "name": "Nope",
            "email": unique_email(),
            "password": "Sup3rSecure!",
            "role": "Mother",
        },
    )
    assert resp.status_code == 422, resp.text


def test_register_rejects_missing_role(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={
            "name": "No Role",
            "email": unique_email(),
            "password": "Sup3rSecure!",
        },
    )
    assert resp.status_code == 422, resp.text


def test_patch_me_rejects_role(client, supabase_env, register):
    """Role is immutable: sending it must fail loudly, not be ignored."""
    _, _, token = register(role="mother")
    resp = client.patch(
        "/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "New Name", "role": "professional"},
    )
    assert resp.status_code == 422, resp.text


def test_role_cannot_be_escalated_through_user_metadata(
    client, supabase_env, register
):
    """The ADR-0005 regression test: `role` is user-writable in the token's
    user_metadata with the PUBLISHABLE key (live-verified), so a gated
    endpoint that read the claim could be escalated. /me must keep
    reporting profiles.role.
    """
    email, user, token = register(role="mother")
    from app import supabase as sb

    # Rewrite our own metadata using only the public key — exactly what an
    # attacker with a browser-visible key can do.
    sb.update_own_metadata(token, {"name": user["name"], "role": "professional"})

    # The metadata WAS changed (the endpoint returns the updated user object,
    # not a fresh token). To see the new claim in a token, we must log in
    # fresh — any token minted AFTER the metadata change will carry it.
    fresh_login = client.post(
        "/auth/login", json={"email": email, "password": "Sup3rSecure!"}
    )
    assert fresh_login.status_code == 200, fresh_login.text
    fresh_token = fresh_login.json()["access_token"]
    claims = _claims(fresh_token)
    assert claims["user_metadata"]["role"] == "professional"

    # ...but the profile is still the source of truth.
    me = client.get("/me", headers={"Authorization": f"Bearer {fresh_token}"})
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "mother", "role was read from the JWT, not profiles"
