"""Tests for mood deletion."""

import pytest

from tests.conftest import captured

PROFILE = {**captured("profiles-select-2026-11-01"), "role": "mother"}


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


def test_a_malformed_date_is_a_400_not_an_outage(mock_client, moods_table):
    """A typo in the day is the caller's mistake, not a backend failure.

    Delete was the one verb that never validated its date: the malformed
    string went straight to PostgREST, which rejected the SQL literal, and the
    generic transport branch reported that as 503 — telling the client to
    retry a request that can never succeed. GET, PATCH and POST all answer
    400 for the same input.

    The fake holds no rows, so if the date were accepted this would be a 204
    (delete is idempotent) rather than the 400 asserted here.
    """
    client, session = mock_client
    moods_table({**PROFILE, "role": "mother"})

    resp = client.delete(
        "/moods/2026-13-45",
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert resp.status_code == 400, resp.text
    assert "date" in resp.json()["detail"].lower()
