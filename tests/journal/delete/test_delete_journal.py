"""Deleting a Journal (ticket 06): gone, and 404 for anything not yours.

Offline tests run through the real router, service, and transport with
Supabase's socket canned (ADR-0006); live tests are the authority on what
the real project does.

Two properties of the canned table make these tests able to fail, and both
are about a fake that would otherwise be too kind:

- It applies the `user_id` filter, so a transport that stopped scoping by
  Author would delete a stranger's entry and the cross-Author test would
  notice rather than pass.
- It answers a DELETE with the rows it matched *only* when the request asks
  for them. PostgREST answers 204 with no body otherwise, and 204 looks
  identical whether one row or none was deleted. A transport that forgot to
  ask could therefore never tell "deleted" from "nothing there", which is
  exactly the distinction ticket 06 requires.

Delete is deliberately *not* idempotent here, unlike moods: ticket 06 says a
date with no entry is 404, so the caller learns their delete did nothing.
"""

from tests.conftest import captured

AUTH = {"Authorization": "Bearer offline-token"}

# Captured from the real project, including the timezone column.
PROFILE = captured("profiles-select-2026-11-01")

# Captured from the real project: the row shape a Journals select returns.
JOURNAL_ROW = captured("journals-select-2026-11-01")[0]

# Another real Profile, so the two-Author tests are two Authors.
OTHER = "00000000-0000-4000-8000-0000000000f1"

DAY = "2026-10-30"


def _journal(user_id=PROFILE["user_id"], day=DAY, title="A day", content="As written."):
    """A Journals row in the shape the real API returns."""
    return {
        **JOURNAL_ROW,
        "user_id": user_id,
        "created_at": day,
        "title": title,
        "content": content,
    }


def _mother(**overrides):
    return {**PROFILE, "role": "mother", **overrides}


def test_an_author_deletes_their_own_journal(mock_client, journals_table):
    """Deleting is a 204 and the row is gone from the table."""
    client, _ = mock_client
    table = journals_table(_mother(), journals=[_journal()])

    resp = client.delete(f"/journals/{DAY}", headers=AUTH)

    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert table.rows == []


def test_reading_the_deleted_day_afterwards_is_not_found(
    mock_client, journals_table
):
    """It is gone, not hidden: the day reads back as absent."""
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    deleted = client.delete(f"/journals/{DAY}", headers=AUTH)
    assert deleted.status_code == 204, deleted.text

    read = client.get(f"/journals/{DAY}", headers=AUTH)

    assert read.status_code == 404, read.text


def test_the_deleted_journal_leaves_the_list(mock_client, journals_table):
    """Deleting one day removes it from the Author's history."""
    client, _ = mock_client
    journals_table(
        _mother(),
        journals=[
            _journal(day="2026-10-30", title="Deleted"),
            _journal(day="2026-10-29", title="Kept"),
        ],
    )

    deleted = client.delete("/journals/2026-10-30", headers=AUTH)
    assert deleted.status_code == 204, deleted.text

    listed = client.get("/journals", headers=AUTH)

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 1
    assert [j["title"] for j in body["journals"]] == ["Kept"]


def test_deleting_a_day_with_no_journal_is_not_found(mock_client, journals_table):
    """Unlike moods, delete is not idempotent: a miss is a 404.

    Ticket 06 asks for this explicitly, so a caller learns their delete did
    nothing rather than assuming it succeeded.
    """
    client, _ = mock_client
    journals_table(_mother())

    resp = client.delete("/journals/2026-01-01", headers=AUTH)

    assert resp.status_code == 404, resp.text


def test_a_malformed_day_is_refused(mock_client, journals_table):
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    resp = client.delete("/journals/2026-13-45", headers=AUTH)

    assert resp.status_code == 400, resp.text


def test_another_authors_journal_may_not_be_deleted(
    mock_client, journals_table, real_gate, as_author
):
    """Privacy: B deleting A's day gets 404, and A's entry survives.

    Runs through the real gate because the session has to actually change;
    under the permissive override both calls would be the same Author and
    the delete would legitimately succeed.

    The survival assertion is the important half: a 404 that still deleted
    the row would be the worst possible outcome, so the table is inspected
    directly rather than trusting the status code alone.
    """
    client, _ = mock_client
    table = journals_table(_mother(), journals=[_journal(title="Mine")])

    as_author(OTHER)
    resp = client.delete(f"/journals/{DAY}", headers=AUTH)

    assert resp.status_code == 404, resp.text
    assert resp.status_code != 403
    assert [row["title"] for row in table.rows] == ["Mine"]


def test_a_soft_deleted_profile_may_not_delete(mock_client, journals_table):
    """A tombstoned Profile cannot act (ADR-0003), deletions included."""
    client, _ = mock_client
    table = journals_table(
        _mother(deleted_at="2026-10-30T00:00:00+00:00"), journals=[_journal()]
    )

    resp = client.delete(f"/journals/{DAY}", headers=AUTH)

    assert resp.status_code == 404, resp.text
    assert len(table.rows) == 1, "a tombstoned Profile still deleted an entry"


def test_a_professional_is_refused_by_the_real_gate(
    mock_client, journals_table, real_gate
):
    """The gate covers deletion too — offline, without Supabase.

    Every other test here runs under the permissive gate override, so
    dropping `Depends(mother_or_companion)` from the delete route would
    leave them all green. This is the one that notices.
    """
    client, _ = mock_client
    table = journals_table(_mother(role="professional"), journals=[_journal()])

    resp = client.delete(f"/journals/{DAY}", headers=AUTH)

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "mother" in detail and "companion" in detail
    assert len(table.rows) == 1


def test_no_token_is_refused(offline_env):
    """401 before anything else: no token, no delete.

    Uses the real app without the offline session override — the override
    exists precisely so other tests can skip authentication, so it cannot be
    in play here. JWT verification rejects a missing token before any
    transport call, so this still needs no network.
    """
    from fastapi.testclient import TestClient

    import app.main as main

    resp = TestClient(main.app).delete(f"/journals/{DAY}")

    assert resp.status_code == 401
