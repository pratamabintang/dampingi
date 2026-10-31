"""Tests for mood deletion."""

import pytest


@pytest.mark.live
def test_delete_mood(client, register):
    """DELETE /moods/{created_at} deletes a mood entry."""
    email, user, token = register()
    
    # Create
    client.post(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "created_at": "2026-10-30",
            "mood": "good",
        },
    )
    
    # Delete
    resp = client.delete(
        "/moods/2026-10-30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
    
    # Verify gone
    resp = client.get(
        "/moods/2026-10-30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.live
def test_delete_mood_idempotent(client, register):
    """DELETE /moods/{created_at} is idempotent (no-op when missing)."""
    email, user, token = register()
    
    # Delete non-existent entry
    resp = client.delete(
        "/moods/2026-10-30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204


def test_delete_mood_no_auth(client):
    """DELETE /moods requires auth."""
    resp = client.delete("/moods/2026-10-30")
    assert resp.status_code == 401
