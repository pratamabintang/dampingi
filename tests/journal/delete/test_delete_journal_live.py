"""Live tests: deleting a Journal against the real project.

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
        "title": "To be deleted",
        "content": "Not for long.",
        "created_at": "2026-10-20",
        **overrides,
    }
    resp = client.post("/journals", headers=_headers(token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_deleting_an_entry_removes_it(client, register):
    """204, and the day reads back as absent afterwards."""
    _, _, token = register(role="mother")
    written = _write(client, token)
    day = written["created_at"]

    deleted = client.delete(f"/journals/{day}", headers=_headers(token))

    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/journals/{day}", headers=_headers(token)).status_code == 404


def test_the_deleted_entry_leaves_the_list(client, register):
    _, _, token = register(role="mother")
    _write(client, token, created_at="2026-10-20", title="Deleted")
    _write(client, token, created_at="2026-10-19", title="Kept")

    client.delete("/journals/2026-10-20", headers=_headers(token))

    listed = client.get("/journals", headers=_headers(token))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 1
    assert [j["title"] for j in body["journals"]] == ["Kept"]


def test_deleting_a_day_with_no_journal_is_not_found(client, register):
    """The real project answers 204 for a no-match DELETE, so the transport
    has to ask for the rows to tell the difference. This is that check."""
    _, _, token = register(role="mother")

    resp = client.delete("/journals/2026-01-01", headers=_headers(token))

    assert resp.status_code == 404, resp.text


def test_another_authors_journal_may_not_be_deleted(client, register):
    """Privacy: B gets 404 deleting A's day, and A's entry survives."""
    _, _, alice = register(role="mother")
    _, _, bob = register(role="companion")
    written = _write(client, alice)
    day = written["created_at"]

    resp = client.delete(f"/journals/{day}", headers=_headers(bob))

    assert resp.status_code == 404, resp.text
    assert resp.status_code != 403

    read = client.get(f"/journals/{day}", headers=_headers(alice))
    assert read.status_code == 200, read.text
    assert read.json()["title"] == written["title"]


def test_a_malformed_day_is_refused(client, register):
    _, _, token = register(role="mother")

    resp = client.delete("/journals/2026-13-45", headers=_headers(token))

    assert resp.status_code == 400, resp.text


def test_a_professional_may_not_delete(client, register):
    _, _, token = register(role="professional")

    resp = client.delete("/journals/2026-10-20", headers=_headers(token))

    assert resp.status_code == 403, resp.text


def test_deleting_with_no_auth_is_refused(client):
    resp = client.delete("/journals/2026-10-20")

    assert resp.status_code == 401
