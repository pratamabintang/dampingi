"""Tests for mood update."""

import pytest


@pytest.mark.live
def test_update_mood(client, register):
    """PATCH /moods/{created_at} updates the mood value."""
    email, user, token = register()
    
    # Create
    client.post(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "created_at": "2026-10-30",
            "mood": 4,
        },
    )
    
    # Update mood
    resp = client.patch(
        "/moods/2026-10-30",
        headers={"Authorization": f"Bearer {token}"},
        json={"mood": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mood"] == 5
    assert body["created_at"] == "2026-10-30"  # unchanged
    assert body["description"] is None

    # Update description
    resp = client.patch(
        "/moods/2026-10-30",
        headers={"Authorization": f"Bearer {token}"},
        json={"description": "Better now"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mood"] == 5
    assert body["description"] == "Better now"


@pytest.mark.live
def test_update_mood_not_found(client, register):
    """PATCH /moods/{created_at} returns 404 when entry missing."""
    email, user, token = register()
    
    resp = client.patch(
        "/moods/2026-10-30",
        headers={"Authorization": f"Bearer {token}"},
        json={"mood": 5},
    )
    assert resp.status_code == 404


@pytest.mark.live
def test_update_mood_invalid_date_format(client, register):
    """PATCH /moods rejects invalid date format."""
    email, user, token = register()
    
    resp = client.patch(
        "/moods/2026-13-45",
        headers={"Authorization": f"Bearer {token}"},
        json={"mood": 4},
    )
    assert resp.status_code == 400
    assert "date" in resp.json()["detail"].lower()


def test_update_mood_rejects_extra_fields(mock_client):
    """PATCH /moods rejects fields other than mood and description."""
    client, session = mock_client
    
    resp = client.patch(
        "/moods/2026-10-30",
        headers={"Authorization": f"Bearer {session.token}"},
        json={"mood": 4, "created_at": "2026-10-31"},
    )
    assert resp.status_code == 422


def test_update_mood_no_auth(client):
    """PATCH /moods requires auth."""
    resp = client.patch(
        "/moods/2026-10-30",
        json={"mood": 4},
    )
    assert resp.status_code == 401
