"""Live tests: writing and reading a Journal against the real project.

These are the authority (ADR-0006). They need the journals migration
applied to the project under test, and two real Profiles.
"""

import pytest

pytestmark = pytest.mark.live


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_write_today_and_read_it_back(client, register):
    """POST then GET: the Journal the Author wrote is the one they read."""
    _, user, token = register(role="mother")

    written = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "First Journal", "content": "A real day, recorded."},
    )
    assert written.status_code == 201, written.text
    body = written.json()
    assert body["user_id"] == user["id"]
    assert body["title"] == "First Journal"
    assert body["content"] == "A real day, recorded."
    day = body["created_at"]

    read = client.get(f"/journals/{day}", headers=_headers(token))

    assert read.status_code == 200, read.text
    assert read.json() == body


def test_a_companion_may_write_a_journal(client, register):
    """Companions are inside the gate, not just Mothers."""
    _, _, token = register(role="companion")

    resp = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "From the companion", "content": "I am here too."},
    )

    assert resp.status_code == 201, resp.text


def test_a_professional_is_refused(client, register):
    """Professionals are outside the gate."""
    _, _, token = register(role="professional")

    resp = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "Clinical", "content": "Not allowed."},
    )

    assert resp.status_code == 403, resp.text


def test_a_second_journal_for_the_same_day_conflicts(client, register):
    """The composite key refuses a second Journal for the same day."""
    _, _, token = register(role="mother")
    payload = {"title": "Once", "content": "Only once."}

    first = client.post("/journals", headers=_headers(token), json=payload)
    assert first.status_code == 201, first.text

    second = client.post(
        "/journals", headers=_headers(token), json={**payload, "title": "Twice"}
    )

    assert second.status_code == 409, second.text


def test_another_authors_journal_is_not_found(client, register):
    """Privacy: Author B cannot see Author A's Journal, and is not told it exists."""
    _, _, alice = register(role="mother")
    _, _, bob = register(role="companion")

    written = client.post(
        "/journals",
        headers=_headers(alice),
        json={"title": "Private", "content": "For my eyes only."},
    )
    assert written.status_code == 201, written.text
    day = written.json()["created_at"]

    peek = client.get(f"/journals/{day}", headers=_headers(bob))

    assert peek.status_code == 404, peek.text
    assert peek.status_code != 403

    # And Bob may write his own Journal for the same day without colliding:
    # the composite key is per Author, not global.
    own = client.post(
        "/journals",
        headers=_headers(bob),
        json={"title": "Mine", "content": "My own day.", "created_at": day},
    )
    assert own.status_code == 201, own.text


def test_a_future_date_is_refused(client, register):
    """A Journal describes a day that has happened."""
    _, _, token = register(role="mother")

    resp = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "Tomorrow", "content": "x", "created_at": "2099-12-31"},
    )

    assert resp.status_code == 400, resp.text


def test_a_malformed_date_is_refused(client, register):
    _, _, token = register(role="mother")

    resp = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "Typo", "content": "x", "created_at": "2026-13-45"},
    )

    assert resp.status_code == 400, resp.text


def test_backfilling_the_same_past_day_twice_conflicts(client, register):
    """A backfilled day holds one Journal, same as today does.

    The offline suite asserts this against a fake that enforces the key;
    this is the authority on what the real table does.
    """
    _, _, token = register(role="mother")
    day = {"title": "Catching up", "content": "x", "created_at": "2026-10-25"}

    first = client.post("/journals", headers=_headers(token), json=day)
    assert first.status_code == 201, first.text

    second = client.post(
        "/journals", headers=_headers(token), json={**day, "title": "Again"}
    )

    assert second.status_code == 409, second.text
    assert "2026-10-25" in second.json()["detail"]


def test_a_past_day_may_be_backfilled(client, register):
    _, _, token = register(role="mother")

    resp = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "Catching up", "content": "x", "created_at": "2026-10-25"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["created_at"] == "2026-10-25"


def test_an_empty_title_is_refused(client, register):
    _, _, token = register(role="mother")

    resp = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "", "content": "x"},
    )

    assert resp.status_code == 422, resp.text


def test_an_overlong_title_is_refused(client, register):
    _, _, token = register(role="mother")

    resp = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "a" * 256, "content": "x"},
    )

    assert resp.status_code == 422, resp.text


def test_an_empty_content_is_refused(client, register):
    _, _, token = register(role="mother")

    resp = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "x", "content": ""},
    )

    assert resp.status_code == 422, resp.text


def test_no_auth_is_refused(client):
    """401 before anything else: no token, no Journal."""
    resp = client.post("/journals", json={"title": "x", "content": "y"})

    assert resp.status_code == 401


def test_reading_with_no_auth_is_refused(client):
    resp = client.get("/journals/2026-10-30")

    assert resp.status_code == 401


def test_deleting_the_account_removes_its_journals(client, register):
    """Journals are personal data: they go when the account goes (FK cascade).

    Uses the admin hard delete, which is the only path that fires the
    cascade — the product's own delete is a soft delete (ADR-0003).
    """
    from app.auth import supabase as auth_supabase

    _, user, token = register(role="mother")

    written = client.post(
        "/journals",
        headers=_headers(token),
        json={"title": "Temporary", "content": "Will be swept away."},
    )
    assert written.status_code == 201, written.text
    day = written.json()["created_at"]

    auth_supabase.delete_user(user["id"])

    # The row is gone from the table itself, not merely hidden from the API.
    rows = auth_supabase.fetch_profile(user["id"])
    assert rows is None, "profile should be gone"

    from app.config import supabase_service_http
    from app.supabase import _base_url, _client, _request, TIMEOUT

    resp = _request(
        _client().get,
        f"{_base_url()}/rest/v1/journals",
        params={"user_id": f"eq.{user['id']}", "select": "user_id,created_at"},
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == [], f"journals outlived their author: {resp.json()}"
