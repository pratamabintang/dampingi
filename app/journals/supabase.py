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


def update_journal(
    user_id: str,
    created_at: str,
    title: str | None = None,
    content: str | None = None,
) -> dict | None:
    """Edit this Author's Journal for a day.

    Takes the fields by name and builds the payload here, so the service never
    names a database column. Neither `user_id` nor `created_at` can enter the
    body: they are not parameters to set, they are this function's arguments,
    and `created_at` is a filter. That makes "an edit cannot move a Journal to
    another day" structural rather than a promise.

    Returns the updated row, or None when nothing matched — which is both
    "this Author has no Journal that day" and "that day belongs to someone
    else". PostgREST answers 200 with an empty array for a PATCH that matched
    no rows, so the returned rows are the only signal.
    """
    payload = {}
    if title is not None:
        payload["title"] = title
    if content is not None:
        payload["content"] = content

    resp = _request(
        _client().patch,
        f"{_base_url()}/rest/v1/journals",
        params={
            "user_id": f"eq.{user_id}",
            "created_at": f"eq.{created_at}",
            "select": _COLUMNS,
        },
        headers={**supabase_service_http(), "Prefer": "return=representation"},
        json=payload,
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    return rows[0] if rows else None


def delete_journal(user_id: str, created_at: str) -> bool:
    """Delete this Author's Journal for a day.

    Returns True when a row was deleted, False when nothing matched — which
    is both "no Journal that day" and "that day belongs to someone else".

    Asks for the deleted rows back (`Prefer: return=representation`) rather
    than trusting the status code. PostgREST answers a DELETE that matched
    nothing with **204 as well**, so a bare 204 cannot tell "deleted" from
    "was never there", and ticket 06 needs that distinction to answer 404.
    """
    resp = _request(
        _client().delete,
        f"{_base_url()}/rest/v1/journals",
        params={
            "user_id": f"eq.{user_id}",
            "created_at": f"eq.{created_at}",
            "select": _COLUMNS,
        },
        headers={**supabase_service_http(), "Prefer": "return=representation"},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    return bool(rows)


# PostgREST answers at most PGRST_DB_MAX_ROWS rows per response and does not
# say when it truncated. Reading every page is what makes "all of this
# Author's Journals" true rather than "the newest N".
_PAGE_SIZE = 1000

# A ceiling on pages, so a upstream that ignores `offset` surfaces as an
# error instead of an unbounded loop holding a request open. 200 pages is
# 200,000 Journals: far past any real history, and a number only reached
# when paging has stopped working.
_MAX_PAGES = 200


def list_journals_all(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[dict], int]:
    """Every Journal this Author has written, newest first (no pagination).

    Scoped to `user_id`. Optional start_date and end_date filter by date range.
    Paged internally to respect PostgREST row cap, but returns all collected rows.
    Returns (rows, total_count).

    Raises SupabaseError on a transport failure.
    """
    filters = {"user_id": f"eq.{user_id}"}
    conditions: list[tuple[str, str]] = []
    if start_date:
        conditions.append(("created_at", f"gte.{start_date}"))
    if end_date:
        conditions.append(("created_at", f"lte.{end_date}"))

    collected: list[dict] = []
    offset = 0

    for _ in range(_MAX_PAGES):
        resp = _request(
            _client().get,
            f"{_base_url()}/rest/v1/journals",
            params=[
                *filters.items(),
                ("select", _COLUMNS),
                ("order", "created_at.desc"),
                ("limit", str(_PAGE_SIZE)),
                ("offset", str(offset)),
                *conditions,
            ],
            headers=supabase_service_http(),
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            raise _error_from_response(resp)

        page = resp.json()
        if not page:
            return collected, len(collected)

        collected.extend(page)
        offset += len(page)

    raise SupabaseError(
        500,
        f"Listing journals did not terminate after {_MAX_PAGES} pages; "
        "the upstream appears to be ignoring the offset parameter",
    )



def list_journals(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """Every Journal this Author has written, newest first, with pagination.

    Scoped to `user_id`, so the list cannot hold another Author's rows.

    Optional start_date and end_date filter by date range (YYYY-MM-DD, inclusive).
    Returns (rows, total_count) where total_count is across all matching records.

    Raises SupabaseError on a transport failure.
    """
    filters = {"user_id": f"eq.{user_id}"}
    conditions: list[tuple[str, str]] = []
    if start_date:
        conditions.append(("created_at", f"gte.{start_date}"))
    if end_date:
        conditions.append(("created_at", f"lte.{end_date}"))

    # Fetch total count with same filters
    count_resp = _request(
        _client().get,
        f"{_base_url()}/rest/v1/journals",
        params=[
            *filters.items(),
            ("select", "count"),
            *conditions,
        ],
        headers={**supabase_service_http(), "Prefer": "count=exact"},
        timeout=TIMEOUT,
    )
    if count_resp.status_code >= 400:
        raise _error_from_response(count_resp)
    
    total = int(count_resp.headers.get("content-range", "0/0").split("/")[1])

    # Fetch paginated results
    resp = _request(
        _client().get,
        f"{_base_url()}/rest/v1/journals",
        params=[
            *filters.items(),
            ("select", _COLUMNS),
            ("order", "created_at.desc"),
            ("limit", str(limit)),
            ("offset", str(offset)),
            *conditions,
        ],
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)

    rows = resp.json()
    return rows, total