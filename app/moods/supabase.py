"""Supabase transport functions for the moods domain."""

from app.config import supabase_service_http
from app.supabase import (
    TIMEOUT,
    _client,
    _base_url,
    _error_from_response,
    _request,
    SupabaseError,
)


def create_mood(user_id: str, created_at: str, mood: int, description: str | None = None) -> dict:
    """Insert a mood entry via PostgREST.

    created_at is a date string in YYYY-MM-DD format.
    mood is an int from 1–5.
    description is an optional contextual note (max 500 chars).
    Returns the inserted row. Raises SupabaseError on conflict (duplicate
    user_id + created_at) or validation failure.
    """
    payload = {
        "user_id": user_id,
        "created_at": created_at,
        "mood": mood,
    }
    if description is not None:
        payload["description"] = description
    
    resp = _request(
        _client().post,
        f"{_base_url()}/rest/v1/moods",
        headers={**supabase_service_http(), "Prefer": "return=representation"},
        json=payload,
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    if not rows:
        raise SupabaseError(500, "No row returned after insert")
    return rows[0]


def fetch_mood(user_id: str, created_at: str) -> dict | None:
    """Read a single mood entry.

    created_at is a date string in YYYY-MM-DD format.
    Returns the row or None if not found. Raises SupabaseError on transport
    failure.
    """
    resp = _request(
        _client().get,
        f"{_base_url()}/rest/v1/moods",
        params={
            "user_id": f"eq.{user_id}",
            "created_at": f"eq.{created_at}",
            "select": "user_id,created_at,mood,description",
        },
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    return rows[0] if rows else None


# PostgREST answers at most PGRST_DB_MAX_ROWS rows per response (1000 in the
# parent repo's supabase/config.toml) and does not say when it truncated.
# Reading every page is what makes "all of this user's moods" true rather than
# "the newest N".
_PAGE_SIZE = 1000

# A ceiling on pages, so an upstream that ignores `offset` surfaces as an
# error instead of an unbounded loop holding a request open. 200 pages is
# 200,000 moods: far past any real history, and a number only reached when
# paging has stopped working.
_MAX_PAGES = 200


def list_moods(user_id: str, start_date: str | None = None, end_date: str | None = None) -> list:
    """List mood entries for a user with optional date range.

    start_date and end_date are YYYY-MM-DD strings (inclusive). If omitted,
    all entries are returned. Results ordered by created_at desc (newest first).
    Returns empty list if no entries found.

    Both bounds are pushed down to the query, as two `created_at` conditions
    that PostgREST applies together. `end_date` used to be filtered in Python
    after the fetch, which was silently wrong once an Author had more moods
    than one response can carry: the fetch returns the newest `max_rows`, so a
    range pointing at older days was filtered over the wrong window and
    answered [] or a partial list.

    Paged for the same reason the journals list is: a single response is
    capped at PGRST_DB_MAX_ROWS with no truncation signal, and a truncated
    list is a false answer to "list my moods". Termination is the first
    *empty* page, never a short one — the cap is configured on the server, so
    a lower cap would make every page short and a short-page check would stop
    after the first.

    Raises SupabaseError on a transport failure, or when paging never reaches
    an empty page.
    """
    filters = {"user_id": f"eq.{user_id}"}
    # Repeated keys, not a dict entry: a closed range needs `gte.` and `lte.`
    # on the same column and PostgREST applies every condition it is given.
    # httpx accepts a list of tuples for exactly this.
    conditions: list[tuple[str, str]] = []
    if start_date:
        conditions.append(("created_at", f"gte.{start_date}"))
    if end_date:
        conditions.append(("created_at", f"lte.{end_date}"))

    collected: list = []
    offset = 0

    for _ in range(_MAX_PAGES):
        resp = _request(
            _client().get,
            f"{_base_url()}/rest/v1/moods",
            params=[
                *filters.items(),
                ("select", "user_id,created_at,mood,description"),
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
            return collected

        collected.extend(page)
        offset += len(page)

    raise SupabaseError(
        500,
        f"Listing moods did not terminate after {_MAX_PAGES} pages; "
        "the upstream appears to be ignoring the offset parameter",
    )


def update_mood(user_id: str, created_at: str, mood: int | None = None, description: str | None = None) -> dict | None:
    """Update the mood value and/or description for an entry.

    created_at is a date string in YYYY-MM-DD format.
    mood is an optional int from 1–5.
    description is an optional contextual note.
    Returns the updated row or None if no entry matched.
    """
    payload = {}
    if mood is not None:
        payload["mood"] = mood
    if description is not None:
        payload["description"] = description
    
    resp = _request(
        _client().patch,
        f"{_base_url()}/rest/v1/moods",
        params={
            "user_id": f"eq.{user_id}",
            "created_at": f"eq.{created_at}",
            "select": "user_id,created_at,mood,description",
        },
        headers={**supabase_service_http(), "Prefer": "return=representation"},
        json=payload,
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    return rows[0] if rows else None


def delete_mood(user_id: str, created_at: str) -> bool:
    """Delete a mood entry.

    created_at is a date string in YYYY-MM-DD format.
    Returns True if a row was deleted, False if no row matched. Raises
    SupabaseError on transport failure.
    """
    resp = _request(
        _client().delete,
        f"{_base_url()}/rest/v1/moods",
        params={
            "user_id": f"eq.{user_id}",
            "created_at": f"eq.{created_at}",
        },
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    return resp.status_code < 300
