"""Live tests: listing an Author's Journals against the real project.

These are the authority (ADR-0006). They need the journals migration applied
to the project under test.
"""

import pytest

pytestmark = pytest.mark.live


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_an_author_with_no_journals_gets_an_empty_list(client, register):
    """No Journals is an empty list, not an error."""
    _, _, token = register(role="mother")

    resp = client.get("/journals", headers=_headers(token))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"journals": [], "count": 0, "offset": 0, "limit": 0, "total": 0}


def test_listed_newest_first(client, register):
    """The real table orders by created_at desc."""
    _, _, token = register(role="mother")

    for day in ("2026-10-28", "2026-10-31", "2026-10-29"):
        wrote = client.post(
            "/journals",
            headers=_headers(token),
            json={"title": day, "content": "x", "created_at": day},
        )
        assert wrote.status_code == 201, wrote.text

    resp = client.get("/journals", headers=_headers(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 3
    assert body["total"] == 3
    assert [j["created_at"] for j in body["journals"]] == [
        "2026-10-31",
        "2026-10-29",
        "2026-10-28",
    ]


def test_the_list_holds_only_the_callers_journals(client, register):
    """Privacy: two Authors on the same dates never see each other."""
    _, alice_user, alice = register(role="mother")
    _, bob_user, bob = register(role="companion")

    for token, title in ((alice, "Alice"), (bob, "Bob")):
        wrote = client.post(
            "/journals",
            headers=_headers(token),
            json={"title": title, "content": "x", "created_at": "2026-10-30"},
        )
        assert wrote.status_code == 201, wrote.text

    alice_list = client.get("/journals", headers=_headers(alice))
    assert alice_list.status_code == 200, alice_list.text
    alice_body = alice_list.json()
    assert alice_body["count"] == 1
    assert alice_body["total"] == 1
    assert alice_body["journals"][0]["title"] == "Alice"
    assert alice_body["journals"][0]["user_id"] == alice_user["id"]

    bob_list = client.get("/journals", headers=_headers(bob))
    assert bob_list.status_code == 200, bob_list.text
    bob_body = bob_list.json()
    assert bob_body["count"] == 1
    assert bob_body["total"] == 1
    assert bob_body["journals"][0]["title"] == "Bob"
    assert bob_body["journals"][0]["user_id"] == bob_user["id"]


def test_a_professional_may_not_list(client, register):
    """The gate covers listing, not just writing."""
    _, _, token = register(role="professional")

    resp = client.get("/journals", headers=_headers(token))

    assert resp.status_code == 403, resp.text


def test_listing_with_no_auth_is_refused(client):
    resp = client.get("/journals")

    assert resp.status_code == 401
