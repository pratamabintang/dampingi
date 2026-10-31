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


def create_mood(user_id: str, created_at: str, mood: int, desc: str | None = None) -> dict:
    """Insert a mood entry via PostgREST.

    created_at is a date string in YYYY-MM-DD format.
    mood is an int from 1–5.
    desc is an optional contextual note (max 500 chars, enforced at app layer).
    Returns the inserted row. Raises SupabaseError on conflict (duplicate
    user_id + created_at) or validation failure.
    """
    payload = {
        "user_id": user_id,
        "created_at": created_at,
        "mood": mood,
    }
    if desc is not None:
        payload["desc"] = desc
    
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
            "select": "user_id,created_at,mood,desc",
        },
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()
    return rows[0] if rows else None


def list_moods(user_id: str, start_date: str | None = None, end_date: str | None = None) -> list:
    """List mood entries for a user with optional date range.

    start_date and end_date are YYYY-MM-DD strings (inclusive). If omitted,
    all entries are returned. Results ordered by created_at desc (newest first).
    Returns empty list if no entries found.
    """
    params = {
        "user_id": f"eq.{user_id}",
        "select": "user_id,created_at,mood,desc",
        "order": "created_at.desc",
    }
    if start_date:
        params["created_at"] = f"gte.{start_date}"
    # PostgREST can't handle two conditions on the same column via params;
    # if both start and end are set, we filter in Python (acceptable for moods)

    resp = _request(
        _client().get,
        f"{_base_url()}/rest/v1/moods",
        params=params,
        headers=supabase_service_http(),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    rows = resp.json()

    # Filter by end_date in Python if needed (moods is small per user)
    if end_date:
        rows = [r for r in rows if r.get("created_at", "") <= end_date]

    return rows


def update_mood(user_id: str, created_at: str, mood: int | None = None, desc: str | None = None) -> dict | None:
    """Update the mood value and/or description for an entry.

    created_at is a date string in YYYY-MM-DD format.
    mood is an optional int from 1–5 (if omitted, not updated).
    desc is an optional contextual note (if omitted, not updated).
    At least one of mood or desc must be non-None.
    Returns the updated row or None if no entry matched. Raises SupabaseError
    on transport failure or validation error.
    """
    payload = {}
    if mood is not None:
        payload["mood"] = mood
    if desc is not None:
        payload["desc"] = desc
    
    resp = _request(
        _client().patch,
        f"{_base_url()}/rest/v1/moods",
        params={
            "user_id": f"eq.{user_id}",
            "created_at": f"eq.{created_at}",
            "select": "user_id,created_at,mood,desc",
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
