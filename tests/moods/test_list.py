"""Tests for mood retrieval."""

import pytest

from tests.conftest import captured, mood_row

PROFILE = {**captured("profiles-select-2026-11-01"), "role": "mother"}


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
            "description": "Found clarity",
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
    assert body["description"] == "Found clarity"


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
                "description": f"Entry {i}",
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
    assert body["moods"][0]["description"] == "Entry 2"


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
                "mood": 3,
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
    # The window is closed on both ends, and the real API is what decides
    # that: `created_at` goes out twice (`gte.` and `lte.`).
    assert [m["created_at"] for m in body["moods"]] == [
        "2026-10-29",
        "2026-10-28",
        "2026-10-27",
    ]


@pytest.mark.live
def test_list_moods_with_an_end_date_only(client, register):
    """GET /moods?end_date=... filters on the real project, not in Python.

    `end_date` used to be applied after the fetch, so it was only ever
    correct by accident on a small table. This is the live authority that
    the bound is pushed down to PostgREST.
    """
    email, user, token = register()

    for i in range(5):
        client.post(
            "/moods",
            headers={"Authorization": f"Bearer {token}"},
            json={"created_at": f"2026-10-{26 + i}", "mood": 3},
        )

    resp = client.get(
        "/moods",
        headers={"Authorization": f"Bearer {token}"},
        params={"end_date": "2026-10-28"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [m["created_at"] for m in body["moods"]] == [
        "2026-10-28",
        "2026-10-27",
        "2026-10-26",
    ]
    assert body["count"] == 3


def test_a_start_date_filter_excludes_older_moods(mock_client, moods_table):
    """The date range is filtered by the upstream, and the fake honours it.

    `list_moods` sends `created_at=gte.<start_date>`, so the transport's
    filtering is real and not done client-side. The canned table applies that
    operator as PostgREST does; a fake that only understood `eq.` would answer
    every ranged list with an empty array, which would agree with a transport
    that had stopped filtering at all.
    """
    client, session = mock_client
    moods_table(
        PROFILE,
        moods=[
            {
                "user_id": session.user_id,
                "created_at": "2026-10-26",
                "mood": 2,
                "description": None,
            },
            {
                "user_id": session.user_id,
                "created_at": "2026-10-29",
                "mood": 4,
                "description": None,
            },
            {
                "user_id": session.user_id,
                "created_at": "2026-10-30",
                "mood": 5,
                "description": None,
            },
        ],
    )

    resp = client.get(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        params={"start_date": "2026-10-29"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 2026-10-26 is older than the start date and must be excluded.
    assert [m["created_at"] for m in body["moods"]] == ["2026-10-30", "2026-10-29"]
    assert body["count"] == 2


# PostgREST caps a response at PGRST_DB_MAX_ROWS (max_rows = 1000 in the
# parent repo's supabase/config.toml) and says nothing about having done so.
MAX_ROWS = 1000


def _daily_moods(user_id, total):
    """`total` moods on consecutive days, newest first.

    Built newest-day-first so the oldest rows sort last, which is where a
    truncating fetch loses them.
    """
    from datetime import date, timedelta

    newest = date(2026, 10, 31)
    return [
        mood_row(user_id, (newest - timedelta(days=offset)).isoformat(), mood=3)
        for offset in range(total)
    ]


def test_an_end_date_filter_reaches_past_the_projects_row_cap(
    mock_client, moods_table
):
    """An end_date-only range must not be answered from the newest page only.

    The bug this pins: `end_date` used to be applied in Python *after* the
    fetch. Once an Author has more than the row cap's worth of moods, the
    fetch returns only the newest 1000 and the filter runs over those — so a
    range pointing at older days answered [] or a partial list, silently and
    wrongly. The range has to be pushed down to the query.
    """
    client, session = mock_client
    total = MAX_ROWS + 5
    rows = _daily_moods(session.user_id, total)
    moods_table(PROFILE, moods=rows)

    # The three oldest days: 1002, 1003 and 1004 days back from the newest.
    oldest_three = sorted(r["created_at"] for r in rows)[:3]

    resp = client.get(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        params={"end_date": oldest_three[-1]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    days = [m["created_at"] for m in body["moods"]]
    assert days, "an end_date past the row cap returned nothing"
    assert set(days) == set(oldest_three)
    assert body["count"] == len(oldest_three)


def test_a_range_with_both_ends_returns_only_that_window(mock_client, moods_table):
    """start_date and end_date together are one closed interval.

    PostgREST takes `created_at` twice (`gte.` and `lte.`) and applies both; a
    transport that sent only one of them would widen the window, and a fake
    that honoured only the first would agree with it. The window deliberately
    spans more rows than the cap, so it also proves the two conditions stay
    applied across every page rather than only the first.
    """
    client, session = mock_client
    total = MAX_ROWS + 5
    rows = _daily_moods(session.user_id, total)
    moods_table(PROFILE, moods=rows)

    days = sorted(r["created_at"] for r in rows)
    # Everything except the two newest days.
    resp = client.get(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
        params={"start_date": days[0], "end_date": days[-3]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    got = [m["created_at"] for m in body["moods"]]
    assert got == sorted(days[:-2], reverse=True)
    assert body["count"] == total - 2


def test_the_mood_list_is_not_truncated_by_the_projects_row_cap(
    mock_client, moods_table
):
    """An Author with more moods than one PostgREST response can hold.

    Same failure as the Journals list: a single un-paged GET returns the
    newest 1000 and nothing says the rest exists, so `count` under-reports a
    total it is presenting as the total. The whole history must arrive.
    """
    client, session = mock_client
    total = MAX_ROWS + 5
    moods_table(PROFILE, moods=_daily_moods(session.user_id, total))

    resp = client.get(
        "/moods",
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == total, "the row cap truncated the Author's moods"
    assert len(body["moods"]) == total
    days = [m["created_at"] for m in body["moods"]]
    assert days == sorted(days, reverse=True)
    assert len(set(days)) == total, "paging repeated a page"
