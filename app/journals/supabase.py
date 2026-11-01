"""Supabase transport functions for the journals domain.

Every query is scoped to `user_id` taken from the session. No function here
accepts a *different* user's id, so one Author cannot reach another Author's
Journal even if they guess its date: the scoping is the ownership check.
"""

from app.config import supabase_service_http
from app.supabase import (
    TIMEOUT,
    _client,
    _base_url,
    _error_from_response,
    _request,
    SupabaseError,
)

_COLUMNS = "user_id,created_at,title,content"


def create_journal(user_id: str, created_at: str, title: str, content: str) -> dict:
    """Insert a Journal via PostgREST.

    `created_at` is a YYYY-MM-DD string, already resolved by the service
    (the Author's local day). Returns the inserted row.

    Raises SupabaseError; the caller distinguishes a duplicate day from a
    genuine transport failure.
    """
    resp = _request(
        _client().post,
        f"{_base_url()}/rest/v1/journals",
        headers={**supabase_service_http(), "Prefer": "return=representation"},
        json={
            "user_id": user_id,
            "created_at": created_at,
            "title": title,
            "content": content,
        },
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    if not rows:
        raise SupabaseError(500, "No row returned after insert")
    return rows[0]


def fetch_journal(user_id: str, created_at: str) -> dict | None:
    """Read one Journal for this Author on this day.

    Returns None when the Author has no Journal for that day — which is also
    the answer for "another Author's Journal": it is not scoped to them, so
    it does not exist as far as this query is concerned.
    """
    resp = _request(
        _client().get,
        f"{_base_url()}/rest/v1/journals",
        params={
            "user_id": f"eq.{user_id}",
            "created_at": f"eq.{created_at}",
            "select": _COLUMNS,
        },
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    return rows[0] if rows else None


# PostgREST answers at most PGRST_DB_MAX_ROWS rows per response and does not
# say when it truncated. Reading every page is what makes "all of this
# Author's Journals" true rather than "the newest N".
_PAGE_SIZE = 1000

# A ceiling on pages, so a upstream that ignores `offset` surfaces as an
# error instead of an unbounded loop holding a request open. 200 pages is
# 200,000 Journals: far past any real history, and a number only reached
# when paging has stopped working.
_MAX_PAGES = 200


def list_journals(user_id: str) -> list[dict]:
    """Every Journal this Author has written, newest first.

    Scoped to `user_id`, so the list cannot hold another Author's rows.

    Paged rather than a single request: the upstream row cap would otherwise
    truncate a long history silently, and a truncated history is a false
    answer to "list my Journals".

    Termination is the first *empty* page, deliberately not a short one. A
    page shorter than `_PAGE_SIZE` proves nothing: the cap is configured on
    the server (PGRST_DB_MAX_ROWS), not here, so a lower cap would make
    every page short and a short-page test would stop after the first and
    truncate exactly as before.

    Raises SupabaseError on a transport failure, or when paging never
    reaches an empty page.
    """
    collected: list[dict] = []
    offset = 0

    for _ in range(_MAX_PAGES):
        resp = _request(
            _client().get,
            f"{_base_url()}/rest/v1/journals",
            params={
                "user_id": f"eq.{user_id}",
                "select": _COLUMNS,
                "order": "created_at.desc",
                "limit": _PAGE_SIZE,
                "offset": offset,
            },
            headers=supabase_service_http(),
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            raise _error_from_response(resp)

        page = resp.json()
        if not page:
            return collected

        collected.extend(page)
        offset += len(page)

    raise SupabaseError(
        500,
        f"Listing journals did not terminate after {_MAX_PAGES} pages; "
        "the upstream appears to be ignoring the offset parameter",
    )