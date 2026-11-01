"""Writing a Journal (ticket 02): create, read back, and who may do it.

Offline tests run through the real router, service, and transport with
Supabase's socket canned (ADR-0006); live tests are the authority on what
the real project does.

The canned upstream behaves like PostgREST where it matters: it returns
only the columns named in `select`, and it applies the `user_id` filter.
Both are what make these tests able to fail — a fake that ignored the
filter would pass even if the transport stopped scoping by Author, which
is the one privacy guarantee this domain has.
"""

import json

import pytest

from tests.conftest import captured, supabase_json

AUTH = {"Authorization": "Bearer offline-token"}

# Captured from the real project, including the timezone column.
PROFILE = captured("profiles-select-2026-11-01")

# Another real Profile, so the two-Author tests are two Authors.
OTHER = "00000000-0000-4000-8000-0000000000f1"


def _postgrest(upstream, profile, journals=None, fail_insert=None):
    """A canned Supabase that honours `select`, `user_id`, and inserts.

    `journals` is the set of rows that exist in the table, across all
    Authors; the handler filters them the way PostgREST would.
    """
    rows = journals or []

    def profile_get(request):
        asked = request.url.params["select"].split(",")
        return supabase_json(200, [{k: profile[k] for k in asked if k in profile}])

    def journal_get(request):
        wanted_user = request.url.params["user_id"].removeprefix("eq.")
        wanted_day = request.url.params["created_at"].removeprefix("eq.")
        asked = request.url.params["select"].split(",")
        hits = [
            {k: row[k] for k in asked if k in row}
            for row in rows
            if row["user_id"] == wanted_user and row["created_at"] == wanted_day
        ]
        return supabase_json(200, hits)

    def insert(request):
        if fail_insert is not None:
            return fail_insert()
        body = json.loads(request.content)
        # Enforce the composite key the way the table does. A fake that
        # appended blindly would answer 201 to a second Journal for the
        # same day, so a duplicate test written against it could never
        # fail — it would be asserting the fake's optimism, not the API.
        clash = [
            row
            for row in rows
            if row["user_id"] == body["user_id"]
            and row["created_at"] == body["created_at"]
        ]
        if clash:
            return _conflict("23505")
        rows.append(body)
        return supabase_json(201, [body])

    return upstream(
        {
            ("GET", "/rest/v1/profiles"): profile_get,
            ("GET", "/rest/v1/journals"): journal_get,
            ("POST", "/rest/v1/journals"): insert,
        }
    )


def _as(user_id: str) -> None:
    """Point the app's session at a Profile, through the real gate.

    The gate is *not* overridden here: `require_any_role` re-reads the
    session, so swapping `verified_session` is enough and the real guard
    still runs.
    """
    import app.main as main
    from app.auth import service
    from app.auth.router import verified_session

    main.app.dependency_overrides[verified_session] = lambda: service.Session(
        user_id=user_id, email="offline@gmail.com", token="offline-token"
    )


@pytest.fixture
def real_gate(mock_client):
    """Run through the real Mother-or-Companion gate.

    `mock_client` overrides the gate so most tests can ignore
    authorization. Tests *about* authorization must not, or they would be
    asserting against a permissive stub.
    """
    import app.main as main
    from app.journals import router as journals_router

    saved = main.app.dependency_overrides.pop(journals_router.mother_or_companion, None)
    yield
    if saved is not None:
        main.app.dependency_overrides[journals_router.mother_or_companion] = saved


def test_a_mother_writes_todays_journal_and_reads_it_back(mock_client, upstream):
    """The whole path: write a Journal, then read it back by date."""
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    written = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "A good day", "content": "We walked to the park."},
    )

    assert written.status_code == 201, written.text
    body = written.json()
    assert body["title"] == "A good day"
    assert body["content"] == "We walked to the park."
    # The server dated it; the client never supplied a day.
    assert len(body["created_at"]) == 10

    read = client.get(f"/journals/{body['created_at']}", headers=AUTH)

    assert read.status_code == 200, read.text
    assert read.json() == body


def test_a_companion_passes_the_real_gate(mock_client, upstream, real_gate):
    """The real gate admits a Companion — the point of a multi-role gate."""
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "companion"})

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Supporting her", "content": "A quiet day."},
    )

    assert resp.status_code == 201, resp.text


def test_a_professional_is_refused_by_the_real_gate(
    mock_client, upstream, real_gate
):
    """A Professional is outside the gate, and told which Roles would pass."""
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "professional"})

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Notes", "content": "Clinical notes."},
    )

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "mother" in detail and "companion" in detail


def test_another_authors_journal_is_not_found(mock_client, upstream):
    """Privacy: another Author's Journal is invisible, and reported as absent.

    The canned table really holds another Author's row for this day. If
    the transport ever stopped scoping by user_id, this test would return
    200 with a stranger's Journal — which is the failure it exists to
    catch.
    """
    client, _ = mock_client
    _postgrest(
        upstream,
        {**PROFILE, "role": "mother"},
        journals=[
            {
                "user_id": OTHER,
                "created_at": "2026-10-30",
                "title": "Not yours",
                "content": "Private.",
            }
        ],
    )

    resp = client.get("/journals/2026-10-30", headers=AUTH)

    assert resp.status_code == 404, resp.text
    assert resp.status_code != 403


def test_two_authors_hold_their_own_journal_on_the_same_day(
    mock_client, upstream, real_gate
):
    """The key is per Author: two people, one day, two Journals.

    Runs through the real gate so the session actually changes; the
    permissive override would hand back the same Author twice and the
    test would prove nothing.
    """
    client, _ = mock_client
    mine = {
        "user_id": PROFILE["user_id"],
        "created_at": "2026-10-30",
        "title": "Mine",
        "content": "My day.",
    }
    theirs = {
        "user_id": OTHER,
        "created_at": "2026-10-30",
        "title": "Theirs",
        "content": "Their day.",
    }
    _postgrest(upstream, {**PROFILE, "role": "mother"}, journals=[theirs, mine])

    _as(PROFILE["user_id"])
    first = client.get("/journals/2026-10-30", headers=AUTH)
    assert first.status_code == 200, first.text
    assert first.json()["title"] == "Mine"

    _as(OTHER)
    second = client.get("/journals/2026-10-30", headers=AUTH)
    assert second.status_code == 200, second.text
    assert second.json()["title"] == "Theirs"


def test_a_second_journal_for_the_same_day_is_a_conflict(mock_client, upstream):
    """The composite key refuses a second Journal, and the API says 409.

    The body is the one PostgREST actually returns for this table,
    captured from the real project: 409 with the SQLSTATE in `code`.
    """
    client, _ = mock_client
    _postgrest(
        upstream,
        {**PROFILE, "role": "mother"},
        fail_insert=lambda: _conflict("23505"),
    )

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Again", "content": "Twice today."},
    )

    assert resp.status_code == 409, resp.text
    # The message names the colliding day, so the client can act on it.
    assert "2026-11-01" in resp.json()["detail"]


def test_a_foreign_key_violation_is_not_reported_as_a_duplicate(
    mock_client, upstream
):
    """PostgREST answers 409 for an FK violation too (verified live).

    A Profile deleted between the guard and the insert must not be
    reported as "you already wrote today" — that would be a false
    statement and the wrong status.
    """
    client, _ = mock_client
    _postgrest(
        upstream,
        {**PROFILE, "role": "mother"},
        fail_insert=lambda: _conflict("23503"),
    )

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Orphan", "content": "No such Author."},
    )

    assert resp.status_code != 409, resp.text
    assert resp.status_code == 503, resp.text


def _conflict(code: str):
    import httpx

    return httpx.Response(
        409,
        json={
            "code": code,
            "details": "Key (user_id, created_at)=(...) already exists.",
            "hint": None,
            "message": "duplicate key value violates unique constraint",
        },
    )


def test_a_soft_deleted_profile_may_not_write(mock_client, upstream):
    """A tombstoned Profile cannot act (ADR-0003)."""
    client, _ = mock_client
    _postgrest(
        upstream,
        {**PROFILE, "role": "mother", "deleted_at": "2026-10-30T00:00:00+00:00"},
    )

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Ghost", "content": "Should not exist."},
    )

    assert resp.status_code == 404, resp.text


def test_a_malformed_date_is_refused(mock_client, upstream):
    """A typo in the day is a 400, not a new Journal."""
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Typo", "content": "x", "created_at": "2026-13-45"},
    )

    assert resp.status_code == 400, resp.text
    assert "date" in resp.json()["detail"].lower()


def test_a_future_date_is_refused(mock_client, upstream):
    """A Journal describes a day that has happened."""
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Tomorrow", "content": "x", "created_at": "2099-12-31"},
    )

    assert resp.status_code == 400, resp.text
    assert "future" in resp.json()["detail"].lower()


def test_a_past_date_is_accepted(mock_client, upstream):
    """Backfilling a missed day is allowed."""
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Last week", "content": "x", "created_at": "2026-10-25"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["created_at"] == "2026-10-25"


def test_backfilling_the_same_past_day_twice_is_a_conflict(mock_client, upstream):
    """A backfilled day is a day like any other: it holds one Journal.

    Nothing about an explicit past date weakens the one-per-day rule. The
    second write is refused, and the message names the day that collided
    rather than today's.
    """
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    first = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Catching up", "content": "x", "created_at": "2026-10-25"},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Again", "content": "y", "created_at": "2026-10-25"},
    )

    assert second.status_code == 409, second.text
    # The colliding day is the backfilled one, not the server's today.
    assert "2026-10-25" in second.json()["detail"]


def test_two_authors_may_both_backfill_the_same_past_day(
    mock_client, upstream, real_gate
):
    """The one-per-day rule is per Author, so backfilling is not contended.

    Runs through the real gate because the session has to actually change;
    under the permissive override both writes would be the same Author and
    the second would be a legitimate 409.
    """
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    _as(PROFILE["user_id"])
    mine = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Mine", "content": "x", "created_at": "2026-10-25"},
    )
    assert mine.status_code == 201, mine.text

    _as(OTHER)
    theirs = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Theirs", "content": "y", "created_at": "2026-10-25"},
    )
    assert theirs.status_code == 201, theirs.text


def test_an_empty_title_is_refused(mock_client, upstream):
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    resp = client.post(
        "/journals", headers=AUTH, json={"title": "", "content": "x"}
    )

    assert resp.status_code == 422, resp.text


def test_an_overlong_title_is_refused(mock_client, upstream):
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    resp = client.post(
        "/journals", headers=AUTH, json={"title": "a" * 256, "content": "x"}
    )

    assert resp.status_code == 422, resp.text


def test_an_empty_content_is_refused(mock_client, upstream):
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    resp = client.post(
        "/journals", headers=AUTH, json={"title": "x", "content": ""}
    )

    assert resp.status_code == 422, resp.text


def test_overlong_content_is_refused(mock_client, upstream):
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    resp = client.post(
        "/journals", headers=AUTH, json={"title": "x", "content": "a" * 10_001}
    )

    assert resp.status_code == 422, resp.text


def test_an_unknown_field_is_refused(mock_client, upstream):
    """`created_at` is accepted; anything else is a mistake worth surfacing."""
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "x", "content": "y", "mood": 4},
    )

    assert resp.status_code == 422, resp.text


def test_no_token_is_refused():
    """401 before anything else: no token, no Journal.

    Uses the real app without the offline session override — the override
    exists precisely so other tests can skip authentication, so it cannot
    be in play here. JWT verification rejects a missing token before any
    transport call, so this still needs no network.
    """
    from fastapi.testclient import TestClient

    import app.main as main
    from app.config import reset_settings
    import os

    os.environ.setdefault("SUPABASE_URL", "https://offline.supabase.test")
    os.environ.setdefault("SUPABASE_PUBLISHABLE_KEY", "offline-publishable")
    os.environ.setdefault("SUPABASE_SECRET_KEY", "offline-secret")
    reset_settings()

    resp = TestClient(main.app).post(
        "/journals", json={"title": "x", "content": "y"}
    )

    assert resp.status_code == 401


def test_an_unusable_timezone_falls_back_to_utc(mock_client, upstream):
    """A garbage timezone must not break writing a Journal (ADR-0007)."""
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother", "timezone": "Not/AZone"})

    resp = client.post(
        "/journals",
        headers=AUTH,
        json={"title": "Fallback", "content": "Still works."},
    )

    assert resp.status_code == 201, resp.text
    assert len(resp.json()["created_at"]) == 10


def test_the_day_is_the_authors_local_one(mock_client, upstream):
    """The day comes from the Author's timezone, not the server's (ADR-0007).

    UTC+14 and UTC-12 are 26 hours apart, so their local calendar dates
    always differ — whatever the wall clock says when this runs. Asserting
    that they differ is independent of how the date is computed.
    """
    client, _ = mock_client

    def day_for(zone: str) -> str:
        _postgrest(upstream, {**PROFILE, "role": "mother", "timezone": zone})
        resp = client.post(
            "/journals", headers=AUTH, json={"title": "Day", "content": "x"}
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["created_at"]

    assert day_for("Pacific/Kiritimati") != day_for("Etc/GMT+12")
