"""Tests for mood retrieval."""

import pytest


@pytest.mark.live
def test_get_mood(client, register):
    """GET /moods/{created_at} retrieves a mood entry."""
    email, user, token = register()
    
    # Create first
    client.post(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "created_at": "2026-10-30",
            "mood": 4,
            "desc": "Found clarity",
        },
    )
    
    # Retrieve
    resp = client.get(
        "/moods/2026-10-30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == user["id"]
    assert body["mood"] == 4
    assert body["created_at"] == "2026-10-30"
    assert body["desc"] == "Found clarity"


@pytest.mark.live
def test_get_mood_not_found(client, register):
    """GET /moods/{created_at} returns 404 when entry missing."""
    email, user, token = register()
    
    resp = client.get(
        "/moods/2026-10-30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.live
def test_get_mood_invalid_date_format(client, register):
    """GET /moods/{created_at} rejects invalid date format."""
    email, user, token = register()
    
    resp = client.get(
        "/moods/2026-13-45",  # Invalid date
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "date" in resp.json()["detail"].lower()


def test_get_mood_no_auth(client):
    """GET /moods/{created_at} requires auth."""
    resp = client.get("/moods/2026-10-30")
    assert resp.status_code == 401


@pytest.mark.live
def test_list_moods(client, register):
    """GET /moods lists mood entries."""
    email, user, token = register()
    
    # Create a few entries
    for i in range(3):
        client.post(
            "/moods",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "created_at": f"2026-10-{27 + i}",
                "mood": [4, 3, 5][i],
                "desc": f"Entry {i}",
            },
        )
    
    # List all
    resp = client.get(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    assert len(body["moods"]) == 3
    # Newest first
    assert body["moods"][0]["created_at"] == "2026-10-29"
    assert body["moods"][0]["mood"] == 5
    assert body["moods"][0]["desc"] == "Entry 2"


@pytest.mark.live
def test_list_moods_with_range(client, register):
    """GET /moods with date range filters entries."""
    email, user, token = register()
    
    # Create entries on different days
    for i in range(5):
        client.post(
            "/moods",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "created_at": f"2026-10-{26 + i}",
                "mood": "good",
            },
        )
    
    # Filter by range
    resp = client.get(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "start_date": "2026-10-27",
            "end_date": "2026-10-29",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
