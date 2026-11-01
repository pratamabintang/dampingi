"""Tests for mood creation.

Offline tests run through the real router, service, and transport with
Supabase's socket canned (ADR-0006); live tests remain the authority on what
the real project does.

The canned table behaves like PostgREST where it matters: it enforces the
composite key `(user_id, created_at)`, applies the `user_id` filter, and
honours `select`. A fake that appended blindly would answer 201 to a second
Mood for the same day, so a duplicate test written against it could never
fail.
"""

import json

import pytest

from tests.conftest import captured, supabase_json

AUTH = {"Authorization": "Bearer offline-token"}

# Captured from the real project, with the timezone column.
PROFILE = {**captured("profiles-select-2026-11-01"), "role": "mother"}


@pytest.mark.live
def test_create_mood(client, register):
    """POST /moods creates a mood entry."""
    email, user, token = register()
    
    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "created_at": "2026-10-30",
            "mood": 4,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_id"] == user["id"]
    assert body["mood"] == 4
    assert body["created_at"] == "2026-10-30"
    assert body["description"] is None


@pytest.mark.live
def test_create_mood_with_description(client, register):
    """POST /moods with description."""
    email, user, token = register()
    
    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "created_at": "2026-10-30",
            "mood": 5,
            "description": "Feeling great today!",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["mood"] == 5
    assert body["description"] == "Feeling great today!"


def test_create_mood_future_rejected(mock_client):
    """POST /moods rejects future dates."""
    client, session = mock_client
    
    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        json={
            "created_at": "2099-12-31",
            "mood": 3,
        },
    )
    assert resp.status_code == 400
    assert "future" in resp.json()["detail"].lower()


def test_create_mood_invalid_date_format(mock_client):
    """POST /moods rejects invalid date format."""
    client, session = mock_client
    
    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        json={
            "created_at": "2026-13-45",  # Invalid month/day
            "mood": 3,
        },
    )
    assert resp.status_code == 400
    assert "date" in resp.json()["detail"].lower()


def test_create_mood_no_auth(client):
    """POST /moods requires auth."""
    resp = client.post(
        "/moods",
        json={
            "created_at": "2026-10-30",
            "mood": 4,
        },
    )
    assert resp.status_code == 401


def test_a_second_mood_for_the_same_day_is_a_conflict(mock_client, moods_table):
    """A duplicate day is a 409 naming the date, not a 503.

    The request collection has documented 409 for this case since before the
    endpoint could produce it; the generic transport error was reported
    instead, so a client was told the backend was down when it had simply
    already recorded the day. Journals answers 409 already (ticket 07 brings
    Moods into line) and this asserts the same external behaviour.

    The fake enforces the composite key the way the table does, so this test
    fails if the service stops recognising the violation: the 409 would ride
    the generic branch and surface as 503.
    """
    client, session = mock_client
    moods_table(PROFILE)

    first = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        json={"created_at": "2026-10-30", "mood": 4},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        json={"created_at": "2026-10-30", "mood": 2},
    )

    assert second.status_code == 409, second.text
    # The message names the day that collided, so the client can act on it.
    assert "2026-10-30" in second.json()["detail"]


def test_a_foreign_key_violation_is_not_reported_as_a_duplicate(
    mock_client, upstream
):
    """PostgREST answers 409 for an FK violation too (verified for Journals).

    A Profile hard-deleted between the guard and the insert must not be
    reported as "you already recorded today" — that would be a false
    statement, and a 409 would tell the client not to retry a write that
    never landed.
    """
    import httpx

    client, session = mock_client

    def profile_get(request):
        asked = request.url.params["select"].split(",")
        return supabase_json(
            200, [{k: PROFILE[k] for k in asked if k in PROFILE}]
        )

    def orphan(_request):
        return httpx.Response(
            409,
            json={
                "code": "23503",
                "details": "Key (user_id)=(...) is not present in table users.",
                "message": "insert or update violates foreign key constraint",
            },
        )

    upstream(
        {
            ("GET", "/rest/v1/profiles"): profile_get,
            ("POST", "/rest/v1/moods"): orphan,
        }
    )

    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        json={"created_at": "2026-10-30", "mood": 4},
    )

    assert resp.status_code != 409, resp.text
    assert resp.status_code == 503, resp.text


def test_an_unreachable_upstream_is_503(mock_client, upstream):
    """A transport failure stays a 503 — the mapping ticket 07 must not change.

    Without this, "409 for duplicates" could be satisfied by treating every
    transport error as a conflict, which would report an outage as a
    duplicate day.
    """
    import httpx

    client, session = mock_client

    def profile_get(request):
        asked = request.url.params["select"].split(",")
        return supabase_json(
            200, [{k: PROFILE[k] for k in asked if k in PROFILE}]
        )

    def unreachable(_request):
        raise httpx.ConnectError("connection refused")

    upstream(
        {
            ("GET", "/rest/v1/profiles"): profile_get,
            ("POST", "/rest/v1/moods"): unreachable,
        }
    )

    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        json={"created_at": "2026-10-30", "mood": 4},
    )

    assert resp.status_code == 503, resp.text
    # The transport reported the outage in its own words, so this is the
    # canned path and not a duplicate that happens to answer 503.
    assert "unreachable" in resp.text.lower()


def test_a_deleted_profile_may_not_record_a_mood(mock_client, moods_table):
    """A tombstoned Profile is a 404, unchanged by ticket 07 (ADR-0003)."""
    client, session = mock_client
    moods_table(
        {**PROFILE, "deleted_at": "2026-10-30T00:00:00+00:00"}
    )

    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        json={"created_at": "2026-10-30", "mood": 4},
    )

    assert resp.status_code == 404, resp.text


def test_a_missing_profile_is_404(mock_client, upstream):
    """No Profile row at all is a 404, not a 500 or a 201."""
    client, session = mock_client
    upstream({("GET", "/rest/v1/profiles"): supabase_json(200, [])})

    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        json={"created_at": "2026-10-30", "mood": 4},
    )

    assert resp.status_code == 404, resp.text


def test_a_duplicate_is_refused_even_for_a_backfilled_past_day(
    mock_client, moods_table
):
    """The one-per-day rule is about the day, not about "today".

    A backfilled day is a day like any other, so the second write for it is
    the same conflict — and the message names that day, not the server's.
    """
    client, session = mock_client
    moods_table(PROFILE)
    headers = {"Authorization": f"Bearer {session.token}"}
    day = {"created_at": "2026-10-25", "mood": 3}

    first = client.post("/moods", headers=headers, json=day)
    assert first.status_code == 201, first.text

    second = client.post("/moods", headers=headers, json={**day, "mood": 5})

    assert second.status_code == 409, second.text
    assert "2026-10-25" in second.json()["detail"]


@pytest.mark.live
def test_create_mood_invalid_range(client, register):
    """POST /moods rejects mood values outside 1-5."""
    email, user, token = register()
    
    # Too high
    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "created_at": "2026-10-30",
            "mood": 6,
        },
    )
    assert resp.status_code == 422

    # Too low
    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "created_at": "2026-10-30",
            "mood": 0,
        },
    )
    assert resp.status_code == 422


@pytest.mark.live
def test_a_second_mood_for_the_same_day_is_a_conflict(client, register):
    """The real table's composite key surfaces as 409, naming the day.

    The authority for ticket 07: the offline test asserts this against a fake
    that enforces the key, this one against the key itself.
    """
    _, _, token = register()
    headers = {"Authorization": f"Bearer {token}"}
    day = {"created_at": "2026-10-30", "mood": 4}

    first = client.post("/moods", headers=headers, json=day)
    assert first.status_code == 201, first.text

    second = client.post("/moods", headers=headers, json={**day, "mood": 2})

    assert second.status_code == 409, second.text
    assert "2026-10-30" in second.json()["detail"]


@pytest.mark.live
def test_a_duplicate_backfilled_past_day_is_a_conflict(client, register):
    """A past day holds one Mood too, and the message names that day."""
    _, _, token = register()
    headers = {"Authorization": f"Bearer {token}"}
    day = {"created_at": "2026-10-25", "mood": 3}

    first = client.post("/moods", headers=headers, json=day)
    assert first.status_code == 201, first.text

    second = client.post("/moods", headers=headers, json={**day, "mood": 5})

    assert second.status_code == 409, second.text
    assert "2026-10-25" in second.json()["detail"]


@pytest.mark.live
def test_create_mood_description_too_long(client, register):
    """POST /moods rejects description > 500 chars."""
    email, user, token = register()
    
    resp = client.post(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "created_at": "2026-10-30",
            "mood": 3,
            "description": "a" * 501,
        },
    )
    assert resp.status_code == 422
