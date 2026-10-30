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
    assert "gmail" in resp.text.lower()


def test_register_rejects_missing_name(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={"email": unique_email(), "password": "Sup3rSecure!"},
    )
    assert resp.status_code == 422


def test_register_rejects_blank_name(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={"name": "", "email": unique_email(), "password": "Sup3rSecure!"},
    )
    assert resp.status_code == 422


def test_register_rejects_short_password(client, supabase_env, unique_email):
    resp = client.post(
        "/auth/register",
        json={"name": "Shorty", "email": unique_email(), "password": "short"},
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


# --- Soft delete (ADR-0003) -------------------------------------------------


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
    from app import supabase as sb

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
        json={"name": "Again", "email": email, "password": "Sup3rSecure!"},
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
    resp = client.post(
        f"{sb.settings.supabase_url}/auth/v1/token",
        params={"grant_type": "refresh_token"},
        json={"refresh_token": body["refresh_token"]},
        headers={
            "apikey": sb.settings.supabase_publishable_key,
            "Authorization": f"Bearer {sb.settings.supabase_publishable_key}",
        },
    )
    assert resp.status_code >= 400, "refresh grant should fail after soft delete"


# --- Logout -----------------------------------------------------------------
# Logout revokes every server-side session (scope=global refresh tokens).
# It does NOT invalidate the current access token: Supabase has no
# access-token revocation, so a live JWT keeps verifying until it expires.
# That limitation is the contract, not a bug — see the logout spec in the
# parent repo (.scratch/logout/spec.md).


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
