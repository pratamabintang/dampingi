"""GET /me: the caller's own Profile, and every way a token is refused."""

from uuid import uuid4

import base64
import jwt as pyjwt
import pytest

pytestmark = pytest.mark.live


def test_me_returns_name_and_email(client, supabase_env, register):
    email, user, access_token = register(name="Grace Hopper")
    resp = client.get("/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "id": user["id"],
        "email": email,
        "name": "Grace Hopper",
        "role": "mother",
    }


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
