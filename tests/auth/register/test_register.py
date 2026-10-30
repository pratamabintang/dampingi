"""Registration: the Profile is created with a name and a Role (ADR-0002, ADR-0005)."""

from uuid import uuid4

import base64
import jwt as pyjwt
import pytest

pytestmark = pytest.mark.live


def test_register_creates_user_session_and_profile(
    client, supabase_env, register
):
    email, user, access_token = register(name="Ada Lovelace")
    assert user["email"] == email
    assert user["name"] == "Ada Lovelace"
    assert access_token


def test_register_duplicate_gmail_conflicts(client, supabase_env, tracked_users):
    email = f"pytest.{uuid4().hex[:12]}@gmail.com"
    payload = {
        "name": "Dup",
        "email": email,
        "password": "Sup3rSecure!",
        "role": "mother",
    }
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
            "role": "mother",
        },
    )
    assert resp.status_code == 422
    assert "gmail" in resp.text.lower()


def test_register_rejects_missing_name(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={"email": unique_email(), "password": "Sup3rSecure!", "role": "mother"},
    )
    assert resp.status_code == 422


def test_register_rejects_blank_name(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={
            "name": "",
            "email": unique_email(),
            "password": "Sup3rSecure!",
            "role": "mother",
        },
    )
    assert resp.status_code == 422


def test_register_rejects_short_password(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={
            "name": "Shorty",
            "email": unique_email(),
            "password": "short",
            "role": "mother",
        },
    )
    assert resp.status_code == 422


def test_register_rejects_missing_fields(client, supabase_env):
    resp = client.post("/auth/register", json={})
    assert resp.status_code == 422
