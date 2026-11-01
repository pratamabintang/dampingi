"""Live tests: editing a Journal against the real project.

These are the authority (ADR-0006). They need the journals migration applied
to the project under test, and two real Profiles.
"""

import pytest

pytestmark = pytest.mark.live


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _write(client, token, **overrides):
    """Write a Journal for a past day and return its body."""
    payload = {
        "title": "As written",
        "content": "The first version.",
        "created_at": "2026-10-20",
        **overrides,
    }
    resp = client.post("/journals", headers=_headers(token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_the_title_can_be_edited_on_its_own(client, register):
    """A typo in the title is fixable without touching the content."""
    _, _, token = register(role="mother")
    written = _write(client, token)

    resp = client.patch(
        f"/journals/{written['created_at']}",
        headers=_headers(token),
        json={"title": "A better title"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "A better title"
    assert body["content"] == written["content"]


def test_the_content_can_be_edited_on_its_own(client, register):
    _, _, token = register(role="mother")
    written = _write(client, token)

    resp = client.patch(
        f"/journals/{written['created_at']}",
        headers=_headers(token),
        json={"content": "Added afterwards."},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"] == "Added afterwards."
    assert body["title"] == written["title"]


def test_an_edit_is_stored_and_the_day_never_moves(client, register):
    """The change persists, and the entry stays on its day."""
    _, _, token = register(role="mother")
    written = _write(client, token)
    day = written["created_at"]

    client.patch(
        f"/journals/{day}",
        headers=_headers(token),
        json={"title": "Persisted", "created_at": "2020-01-01"},
    )

    read = client.get(f"/journals/{day}", headers=_headers(token))

    assert read.status_code == 200, read.text
    body = read.json()
    assert body["title"] == "Persisted"
    assert body["created_at"] == day


def test_a_day_in_the_payload_is_ignored_by_the_real_table(client, register):
    """The real table does not move the row when the body names another day."""
    _, _, token = register(role="mother")
    written = _write(client, token)
    day = written["created_at"]

    client.patch(
        f"/journals/{day}",
        headers=_headers(token),
        json={"title": "Still here", "created_at": "2020-01-01"},
    )

    # Nothing was created at the other day, and the original still stands.
    assert client.get("/journals/2020-01-01", headers=_headers(token)).status_code == 404
    assert client.get(f"/journals/{day}", headers=_headers(token)).status_code == 200


def test_an_update_with_no_fields_is_refused(client, register):
    _, _, token = register(role="mother")
    written = _write(client, token)

    resp = client.patch(
        f"/journals/{written['created_at']}", headers=_headers(token), json={}
    )

    assert resp.status_code == 400, resp.text


def test_an_unknown_field_is_refused(client, register):
    _, _, token = register(role="mother")
    written = _write(client, token)

    resp = client.patch(
        f"/journals/{written['created_at']}",
        headers=_headers(token),
        json={"mood": 4},
    )

    assert resp.status_code == 422, resp.text


def test_the_length_limits_apply_to_an_edit(client, register):
    _, _, token = register(role="mother")
    written = _write(client, token)

    empty = client.patch(
        f"/journals/{written['created_at']}",
        headers=_headers(token),
        json={"title": ""},
    )
    too_long = client.patch(
        f"/journals/{written['created_at']}",
        headers=_headers(token),
        json={"content": "a" * 10_001},
    )

    assert empty.status_code == 422, empty.text
    assert too_long.status_code == 422, too_long.text


def test_editing_a_day_with_no_journal_is_not_found(client, register):
    _, _, token = register(role="mother")

    resp = client.patch(
        "/journals/2026-01-01", headers=_headers(token), json={"title": "Nowhere"}
    )

    assert resp.status_code == 404, resp.text


def test_another_authors_journal_may_not_be_edited(client, register):
    """Privacy: B gets 404 editing A's day, and A's entry survives untouched."""
    _, _, alice = register(role="mother")
    _, _, bob = register(role="companion")
    written = _write(client, alice)
    day = written["created_at"]

    resp = client.patch(
        f"/journals/{day}", headers=_headers(bob), json={"title": "Hijacked"}
    )

    assert resp.status_code == 404, resp.text
    assert resp.status_code != 403

    read = client.get(f"/journals/{day}", headers=_headers(alice))
    assert read.status_code == 200, read.text
    assert read.json()["title"] == written["title"]
    assert read.json()["content"] == written["content"]


def test_a_professional_may_not_edit(client, register):
    _, _, token = register(role="professional")

    resp = client.patch(
        "/journals/2026-10-20", headers=_headers(token), json={"title": "Clinical"}
    )

    assert resp.status_code == 403, resp.text


def test_editing_with_no_auth_is_refused(client):
    resp = client.patch("/journals/2026-10-20", json={"title": "Anonymous"})

    assert resp.status_code == 401
