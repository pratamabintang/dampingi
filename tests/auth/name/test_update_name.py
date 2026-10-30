"""PATCH /me: the name is the only editable Profile field."""

from uuid import uuid4

import base64
import jwt as pyjwt
import pytest

pytestmark = pytest.mark.live


def test_patch_me_updates_name(client, supabase_env, register):
    _, user, token = register(name="Old Name", role="companion")
    resp = client.patch(
        "/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "New Name"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "New Name"
    assert body["role"] == "companion"

    # persisted, not just echoed
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["name"] == "New Name"


def test_patch_me_rejects_blank_name(client, supabase_env, register):
    _, _, token = register()
    resp = client.patch(
        "/me", headers={"Authorization": f"Bearer {token}"}, json={"name": ""}
    )
    assert resp.status_code == 422, resp.text


def test_patch_me_rejects_oversized_name(client, supabase_env, register):
    _, _, token = register()
    resp = client.patch(
        "/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "x" * 101},
    )
    assert resp.status_code == 422, resp.text


def test_patch_me_rejects_empty_body(client, supabase_env, register):
    _, _, token = register()
    resp = client.patch(
        "/me", headers={"Authorization": f"Bearer {token}"}, json={}
    )
    assert resp.status_code == 422, resp.text


def test_patch_me_for_deleted_user_is_403(client, supabase_env, register):
    _, _, token = register()
    assert client.delete("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    resp = client.patch(
        "/me", headers={"Authorization": f"Bearer {token}"}, json={"name": "Ghost"}
    )
    assert resp.status_code == 403, resp.text
