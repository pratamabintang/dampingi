"""Listing an Author's Journals (ticket 04): scope, order, and the empty case.

Offline tests run through the real router, service, and transport with
Supabase's socket canned (ADR-0006); live tests are the authority on what
the real project does.

The canned upstream behaves like PostgREST where it matters: it applies the
`user_id` filter, honours `order`, and caps a response at the project's
`max_rows` while honouring `offset`. Those are what make these tests able to
fail — a fake that returned every row regardless of the filter would pass
even if the transport stopped scoping by Author, which is the one privacy
guarantee this domain has.
"""

from tests.conftest import captured, supabase_json

AUTH = {"Authorization": "Bearer offline-token"}

# Captured from the real project, including the timezone column.
PROFILE = captured("profiles-select-2026-11-01")

# Captured from the real project: the row shape a Journals select returns.
# Rows are built from this rather than hand-written, so the fake cannot
# drift into a shape the API does not actually produce (ADR-0006).
JOURNAL_ROW = captured("journals-select-2026-11-01")[0]

# Another real Profile, so the two-Author tests are two Authors.
OTHER = "00000000-0000-4000-8000-0000000000f1"

# PostgREST is configured with PGRST_DB_MAX_ROWS=1000 (max_rows in the parent
# repo's supabase/config.toml). A request for more is silently capped, which is
# exactly why the transport has to page rather than trust one response to be
# the whole history.
MAX_ROWS = 1000

def _row(user_id, day, title="A day"):
    """A Journals row in the shape the real API returns, with fields replaced."""
    return {
        **JOURNAL_ROW,
        "user_id": user_id,
        "created_at": day,
        "title": title,
        "content": f"Content for {day}.",
    }

def _postgrest(upstream, profile, journals=None, max_rows=MAX_ROWS):
    """A canned Supabase that filters by Author, orders, and caps like PostgREST.

    `journals` is every row in the table, across all Authors. `max_rows`
    stands in for PGRST_DB_MAX_ROWS, which is configured on the server and
    can be lower than the page size the transport asks for.
    """
    rows = journals or []

    def profile_get(request):
        asked = request.url.params["select"].split(",")
        return supabase_json(200, [{k: profile[k] for k in asked if k in profile}])

    def journal_get(request):
        asked = request.url.params["select"].split(",")
        order = request.url.params.get("order")

        # PostgREST with no filter returns every row, which is exactly the
        # failure this fake must reproduce: if the transport ever stopped
        # scoping by Author, these tests would see strangers' Journals
        # instead of crashing on a missing parameter.
        wanted_user = request.url.params.get("user_id")
        hits = [
            row
            for row in rows
            if wanted_user is None
            or row["user_id"] == wanted_user.removeprefix("eq.")
        ]
        if order == "created_at.desc":
            hits = sorted(hits, key=lambda r: r["created_at"], reverse=True)
        elif order == "created_at.asc":
            hits = sorted(hits, key=lambda r: r["created_at"])

        # Page like PostgREST: offset first, then the server's row cap.
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", max_rows))
        page = hits[offset : offset + min(limit, max_rows)]

        return supabase_json(
            200, [{k: row[k] for k in asked if k in row} for row in page]
        )

    return upstream(
        {
            ("GET", "/rest/v1/profiles"): profile_get,
            ("GET", "/rest/v1/journals"): journal_get,
        }
    )

def test_an_author_with_no_journals_gets_an_empty_list(mock_client, upstream):
    """No Journals is an empty list, not an error and not a 404."""
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "mother"})

    resp = client.get("/journals", headers=AUTH)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"journals": [], "count": 0}

def test_the_list_holds_only_the_requesting_authors_journals(mock_client, upstream):
    """Privacy: another Author's Journals never appear, even on shared dates."""
    client, _ = mock_client
    _postgrest(
        upstream,
        {**PROFILE, "role": "mother"},
        journals=[
            _row(PROFILE["user_id"], "2026-10-30", "Mine"),
            _row(OTHER, "2026-10-30", "Theirs"),
            _row(OTHER, "2026-10-29", "Also theirs"),
        ],
    )

    resp = client.get("/journals", headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert [j["title"] for j in body["journals"]] == ["Mine"]
    assert all(j["user_id"] == PROFILE["user_id"] for j in body["journals"])

def test_journals_are_listed_newest_first(mock_client, upstream):
    """Reviewing history reads backwards from the most recent day."""
    client, _ = mock_client
    _postgrest(
        upstream,
        {**PROFILE, "role": "mother"},
        journals=[
            _row(PROFILE["user_id"], "2026-10-28"),
            _row(PROFILE["user_id"], "2026-10-31"),
            _row(PROFILE["user_id"], "2026-10-29"),
        ],
    )

    resp = client.get("/journals", headers=AUTH)

    assert resp.status_code == 200, resp.text
    days = [j["created_at"] for j in resp.json()["journals"]]
    assert days == ["2026-10-31", "2026-10-29", "2026-10-28"]

def test_two_authors_listing_the_same_dates_see_only_their_own(
    mock_client, upstream, real_gate, as_author
):
    """Both Authors journal on the same days; each list holds only its own.

    Runs through the real gate because the session has to actually change —
    under the permissive override both calls would be the same Author.
    """
    client, _ = mock_client
    _postgrest(
        upstream,
        {**PROFILE, "role": "mother"},
        journals=[
            _row(PROFILE["user_id"], "2026-10-30", "Mine"),
            _row(OTHER, "2026-10-30", "Theirs"),
        ],
    )

    as_author(PROFILE["user_id"])
    mine = client.get("/journals", headers=AUTH)
    assert mine.status_code == 200, mine.text
    assert [j["title"] for j in mine.json()["journals"]] == ["Mine"]

    as_author(OTHER)
    theirs = client.get("/journals", headers=AUTH)
    assert theirs.status_code == 200, theirs.text
    assert [j["title"] for j in theirs.json()["journals"]] == ["Theirs"]

def test_the_list_is_not_truncated_by_the_projects_row_cap(mock_client, upstream):
    """An Author with more Journals than PostgREST returns in one response.

    PostgREST caps a response at PGRST_DB_MAX_ROWS (1000) and says nothing
    about it. An Author who writes daily passes that in under three years,
    so a single un-paged query would silently hide the beginning of their
    history. This asserts the whole history arrives, oldest included.
    """
    client, _ = mock_client
    total = MAX_ROWS + 5

    # 2026-10-31 counting backwards, so the oldest rows sort last.
    from datetime import date, timedelta

    newest = date(2026, 10, 31)
    journals = [
        _row(
            PROFILE["user_id"],
            (newest - timedelta(days=offset)).isoformat(),
            f"Day {offset}",
        )
        for offset in range(total)
    ]
    _postgrest(upstream, {**PROFILE, "role": "mother"}, journals=journals)

    resp = client.get("/journals", headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == total, "the row cap truncated the Author's history"
    assert body["journals"][0]["title"] == "Day 0"
    assert body["journals"][-1]["title"] == f"Day {total - 1}"
    # Still newest first across the page boundary.
    days = [j["created_at"] for j in body["journals"]]
    assert days == sorted(days, reverse=True)

def test_the_list_survives_a_server_cap_lower_than_the_page_size(
    mock_client, upstream
):
    """A short page does not mean the history is finished.

    The row cap is configured on the server, so it can be lower than the
    page size the transport asks for. Every page then comes back short, and
    a loop that stopped on "fewer rows than I asked for" would return after
    page one and truncate the history — silently, which is the failure this
    whole mechanism exists to prevent.
    """
    client, _ = mock_client
    total = 7

    from datetime import date, timedelta

    newest = date(2026, 10, 31)
    journals = [
        _row(
            PROFILE["user_id"],
            (newest - timedelta(days=offset)).isoformat(),
            f"Day {offset}",
        )
        for offset in range(total)
    ]
    # The server hands back at most 2 rows per request, whatever is asked.
    _postgrest(
        upstream, {**PROFILE, "role": "mother"}, journals=journals, max_rows=2
    )

    resp = client.get("/journals", headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == total, "a short page was mistaken for the last page"
    days = [j["created_at"] for j in body["journals"]]
    assert days == sorted(days, reverse=True)

def test_an_upstream_that_ignores_offset_does_not_hang(mock_client, upstream):
    """Paging must terminate even when the upstream ignores `offset`.

    If a proxy or gateway dropped `offset`, every page would repeat the
    first, and a loop that only stopped on an empty page would spin
    forever holding the request open. The ceiling turns that into a 503,
    which is an honest "upstream is misbehaving" rather than a hang.
    """
    client, _ = mock_client

    def profile_get(request):
        asked = request.url.params["select"].split(",")
        return supabase_json(200, [{k: PROFILE[k] for k in asked if k in PROFILE}])

    def journal_get(request):
        # Always the same full page: `offset` is ignored, as a broken
        # gateway would.
        return supabase_json(
            200,
            [
                _row(PROFILE["user_id"], f"2026-10-{day:02d}")
                for day in range(1, 11)
            ]
            * 100,
        )

    upstream(
        {
            ("GET", "/rest/v1/profiles"): profile_get,
            ("GET", "/rest/v1/journals"): journal_get,
        }
    )

    resp = client.get("/journals", headers=AUTH)

    assert resp.status_code == 503, resp.text


def test_a_professional_is_refused_by_the_real_gate(
    mock_client, upstream, real_gate
):
    """The gate covers listing too — offline, without Supabase.

    Every other test here runs under the permissive gate override, so
    dropping `Depends(mother_or_companion)` from the list route would leave
    them all green. This is the one that notices.
    """
    client, _ = mock_client
    _postgrest(upstream, {**PROFILE, "role": "professional"})

    resp = client.get("/journals", headers=AUTH)

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "mother" in detail and "companion" in detail


def test_no_token_is_refused(offline_env):
    """401 before anything else: no token, no list.

    Uses the real app without the offline session override — the override
    exists precisely so other tests can skip authentication, so it cannot
    be in play here. JWT verification rejects a missing token before any
    transport call, so this still needs no network.

    `offline_env` supplies and restores the credentials rather than setting
    them by hand: a bare os.environ write here would leak into later tests.
    """
    from fastapi.testclient import TestClient

    import app.main as main

    resp = TestClient(main.app).get("/journals")

    assert resp.status_code == 401