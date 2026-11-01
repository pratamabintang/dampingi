"""Editing a Journal (ticket 05): partial updates, a fixed date, and 404s.

Offline tests run through the real router, service, and transport with
Supabase's socket canned (ADR-0006); live tests are the authority on what
the real project does.

The canned table is the shared `journals_table` fixture: it holds rows across
Authors, applies the `user_id` filter, and answers PATCH with the rows it
matched, the way PostgREST does. Those are what make these tests able to fail.
A fake that ignored the filter would let an Author edit a stranger's entry and
still pass; a fake that answered 200 regardless of what matched would make the
404 cases unfalsifiable.
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


def test_the_title_can_be_edited_on_its_own(mock_client, journals_table):
    """Fixing a typo in the title leaves the content alone."""
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal(content="Untouched.")])

    resp = client.patch(
        f"/journals/{DAY}", headers=AUTH, json={"title": "A better title"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "A better title"
    assert body["content"] == "Untouched."


def test_the_content_can_be_edited_on_its_own(mock_client, journals_table):
    """Adding a thought later leaves the title alone."""
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal(title="Untouched")])

    resp = client.patch(
        f"/journals/{DAY}", headers=AUTH, json={"content": "Added afterwards."}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"] == "Added afterwards."
    assert body["title"] == "Untouched"


def test_both_fields_can_be_edited_together(mock_client, journals_table):
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    resp = client.patch(
        f"/journals/{DAY}",
        headers=AUTH,
        json={"title": "Rewritten", "content": "The whole entry, again."},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Rewritten"
    assert body["content"] == "The whole entry, again."


def test_the_edit_is_stored_and_reads_back(mock_client, journals_table):
    """The change persists: reading the day returns the edited entry."""
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    edited = client.patch(
        f"/journals/{DAY}", headers=AUTH, json={"title": "Persisted"}
    )
    assert edited.status_code == 200, edited.text

    read = client.get(f"/journals/{DAY}", headers=AUTH)

    assert read.status_code == 200, read.text
    assert read.json()["title"] == "Persisted"


def test_the_day_never_moves(mock_client, journals_table):
    """Editing is not a way to rewrite history into a different day."""
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    resp = client.patch(
        f"/journals/{DAY}", headers=AUTH, json={"title": "Still the same day"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["created_at"] == DAY


def test_a_day_in_the_payload_is_ignored(mock_client, journals_table):
    """A client that sends `created_at` is tolerated, not obeyed.

    The field is accepted and dropped: the day comes from the URL, and the
    entry stays on it. The fake applies a PATCH body verbatim, so a transport
    that forwarded this field would move the row and fail here.
    """
    client, _ = mock_client
    table = journals_table(_mother(), journals=[_journal()])

    resp = client.patch(
        f"/journals/{DAY}",
        headers=AUTH,
        json={"title": "Moved?", "created_at": "2020-01-01"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["created_at"] == DAY
    assert [row["created_at"] for row in table.rows] == [DAY]


def test_an_update_with_no_fields_is_refused(mock_client, journals_table):
    """An edit that changes nothing is a mistake, not a silent no-op."""
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    resp = client.patch(f"/journals/{DAY}", headers=AUTH, json={})

    assert resp.status_code == 400, resp.text


def test_an_unknown_field_is_refused(mock_client, journals_table):
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    resp = client.patch(f"/journals/{DAY}", headers=AUTH, json={"mood": 4})

    assert resp.status_code == 422, resp.text


def test_an_empty_title_is_refused(mock_client, journals_table):
    """The same length limits as creation apply to an edit."""
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    resp = client.patch(f"/journals/{DAY}", headers=AUTH, json={"title": ""})

    assert resp.status_code == 422, resp.text


def test_an_overlong_title_is_refused(mock_client, journals_table):
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    resp = client.patch(
        f"/journals/{DAY}", headers=AUTH, json={"title": "a" * 256}
    )

    assert resp.status_code == 422, resp.text


def test_an_overlong_content_is_refused(mock_client, journals_table):
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    resp = client.patch(
        f"/journals/{DAY}", headers=AUTH, json={"content": "a" * 10_001}
    )

    assert resp.status_code == 422, resp.text


def test_a_day_with_no_journal_is_not_found(mock_client, journals_table):
    """Editing nothing is a 404, not an empty 200."""
    client, _ = mock_client
    journals_table(_mother())

    resp = client.patch(
        "/journals/2026-01-01", headers=AUTH, json={"title": "Nowhere"}
    )

    assert resp.status_code == 404, resp.text


def test_a_malformed_day_is_refused(mock_client, journals_table):
    client, _ = mock_client
    journals_table(_mother(), journals=[_journal()])

    resp = client.patch(
        "/journals/2026-13-45", headers=AUTH, json={"title": "Typo"}
    )

    assert resp.status_code == 400, resp.text


def test_another_authors_journal_is_not_found(
    mock_client, journals_table, real_gate, as_author
):
    """Privacy: B editing A's day gets 404, and A's entry is untouched.

    Runs through the real gate because the session has to actually change;
    under the permissive override both calls would be the same Author, and
    the edit would legitimately succeed.
    """
    client, _ = mock_client
    table = journals_table(
        _mother(), journals=[_journal(title="Mine", content="Private.")]
    )

    as_author(OTHER)
    resp = client.patch(
        f"/journals/{DAY}", headers=AUTH, json={"title": "Hijacked"}
    )

    assert resp.status_code == 404, resp.text
    assert resp.status_code != 403
    # The entry itself is unchanged: the 404 was a real refusal, not a
    # politely-worded success.
    assert [row["title"] for row in table.rows] == ["Mine"]
    assert [row["content"] for row in table.rows] == ["Private."]


def test_a_soft_deleted_profile_may_not_edit(mock_client, journals_table):
    """A tombstoned Profile cannot act (ADR-0003), edits included."""
    client, _ = mock_client
    table = journals_table(
        _mother(deleted_at="2026-10-30T00:00:00+00:00"),
        journals=[_journal(title="Before deletion")],
    )

    resp = client.patch(
        f"/journals/{DAY}", headers=AUTH, json={"title": "Ghost edit"}
    )

    assert resp.status_code == 404, resp.text
    assert [row["title"] for row in table.rows] == ["Before deletion"]


def test_a_professional_is_refused_by_the_real_gate(
    mock_client, journals_table, real_gate
):
    """The gate covers editing too — offline, without Supabase.

    Every other test here runs under the permissive gate override, so
    dropping `Depends(mother_or_companion)` from the edit route would leave
    them all green. This is the one that notices.
    """
    client, _ = mock_client
    journals_table(_mother(role="professional"), journals=[_journal()])

    resp = client.patch(
        f"/journals/{DAY}", headers=AUTH, json={"title": "Not mine to edit"}
    )

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "mother" in detail and "companion" in detail


def test_no_token_is_refused(offline_env):
    """401 before anything else: no token, no edit.

    Uses the real app without the offline session override — the override
    exists precisely so other tests can skip authentication, so it cannot be
    in play here. JWT verification rejects a missing token before any
    transport call, so this still needs no network.
    """
    from fastapi.testclient import TestClient

    import app.main as main

    resp = TestClient(main.app).patch(
        f"/journals/{DAY}", json={"title": "Anonymous"}
    )

    assert resp.status_code == 401
