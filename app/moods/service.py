"""Logic for the moods domain: the flows.

Never speaks HTTP: validation failures and business rules surface as
domain exceptions. Router maps them to status codes.
"""

from datetime import date, datetime, timezone
from typing import Optional

from app.auth import supabase as auth_supabase
from app.moods import supabase
from app.supabase import SupabaseError
from app.moods.schemas import MoodOut, MoodListOut


class MoodFutureDate(Exception):
    """Attempted to create/update a mood entry for a future date."""


class MoodNotFound(Exception):
    """No mood entry exists for the given user and date."""


class MoodDuplicate(Exception):
    """The user already has a Mood recorded for that day.

    Carries the day so the router can name it in the refusal: "already
    recorded" is only actionable if the client learns *which* day collided.
    """

    def __init__(self, created_at: str):
        super().__init__(created_at)
        self.created_at = created_at


class ProfileNotFound(Exception):
    """The user has no active profile."""


class InvalidDate(Exception):
    """The provided date string is malformed or invalid."""


# Postgres SQLSTATE for a unique violation. PostgREST forwards it as the
# body's `code`, so a duplicate day is distinguishable without matching on
# prose.
#
# Keying on the SQLSTATE and NOT on the HTTP status is load-bearing:
# PostgREST answers 409 for a foreign-key violation too (23503), verified
# against the real project. A Profile hard-deleted between the guard and
# the insert would otherwise be reported as "you already recorded today".
_UNIQUE_VIOLATION = "23505"


def _is_duplicate(exc: SupabaseError) -> bool:
    """True when PostgREST refused the insert on the composite key.

    Deliberately the SQLSTATE only, never `status == 409`: a foreign-key
    violation is also a 409, and calling that "duplicate day" would be a
    lie — and a 409 tells a client not to retry a write that never landed.
    """
    return exc.error_code == _UNIQUE_VIOLATION


def _parse_date(date_str: str) -> date:
    """Parse YYYY-MM-DD string to date object.
    
    Raises InvalidDate if format or value is invalid.
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError) as e:
        raise InvalidDate(f"Invalid date format: expected YYYY-MM-DD, got '{date_str}'") from e


def _today() -> date:
    """Current date in UTC."""
    return datetime.now(timezone.utc).date()


def _is_future(date_str: str) -> bool:
    """Check if the given date is in the future (UTC).
    
    Raises InvalidDate if date_str is malformed.
    """
    parsed_date = _parse_date(date_str)
    return parsed_date > _today()


def create_mood(
    user_id: str, created_at: str, mood: int, description: Optional[str] = None
) -> MoodOut:
    """Create a mood entry.

    Validates that the date is not in the future and the user has an
    active profile. Raises InvalidDate, MoodFutureDate, MoodDuplicate,
    ProfileNotFound, or SupabaseError.

    A second Mood for a day the user has already recorded raises
    MoodDuplicate, which the router answers with 409. That is a conflict the
    client can act on, not an outage it should retry.
    """
    # Strict date validation: malformed dates raise InvalidDate
    if _is_future(created_at):
        raise MoodFutureDate()

    # Verify the user has an active (not soft-deleted) profile
    profile = auth_supabase.fetch_profile(user_id)
    if not profile or profile.get("deleted_at"):
        raise ProfileNotFound()

    # Insert via PostgREST
    try:
        row = supabase.create_mood(user_id, created_at, mood, description)
    except SupabaseError as exc:
        if _is_duplicate(exc):
            raise MoodDuplicate(created_at) from exc
        raise

    return MoodOut(
        user_id=row.get("user_id", ""),
        created_at=row.get("created_at", ""),
        mood=row.get("mood", 0),
        description=row.get("description"),
    )


def get_mood(user_id: str, created_at: str) -> Optional[MoodOut]:
    """Fetch a single mood entry.

    Returns None if no entry exists. Raises InvalidDate on malformed date
    or SupabaseError on transport failure.
    """
    # Validate date format
    _parse_date(created_at)
    
    row = supabase.fetch_mood(user_id, created_at)
    if not row:
        return None
    return MoodOut(
        user_id=row.get("user_id", ""),
        created_at=row.get("created_at", ""),
        mood=row.get("mood", 0),
        description=row.get("description"),
    )


def list_moods(
    user_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> MoodListOut:
    """List mood entries for a user within a date range with optional pagination.

    Both dates are optional. If provided, they must be YYYY-MM-DD format.
    Results are ordered newest first. 
    
    If offset and limit are both None, returns all matching entries (backward compatible).
    If either is provided, uses pagination. Returns paginated results with total count.
    
    Raises InvalidDate on malformed dates.
    """
    # Validate date formats
    if start_date:
        _parse_date(start_date)
    if end_date:
        _parse_date(end_date)
    
    # If no pagination params, fetch all (backward compatible)
    if offset is None and limit is None:
        rows, total = supabase.list_moods_all(user_id, start_date, end_date)
        moods = [
            MoodOut(
                user_id=row.get("user_id", ""),
                created_at=row.get("created_at", ""),
                mood=row.get("mood", 0),
                description=row.get("description"),
            )
            for row in rows
        ]
        return MoodListOut(
            moods=moods,
            count=len(moods),
            offset=0,
            limit=len(moods),
            total=len(moods),
        )
    
    # Pagination mode
    offset = offset or 0
    limit = limit or 20
    rows, total = supabase.list_moods(user_id, start_date, end_date, offset, limit)
    moods = [
        MoodOut(
            user_id=row.get("user_id", ""),
            created_at=row.get("created_at", ""),
            mood=row.get("mood", 0),
            description=row.get("description"),
        )
        for row in rows
    ]
    
    return MoodListOut(
        moods=moods,
        count=len(moods),
        offset=offset,
        limit=limit,
        total=total,
    )


def update_mood(
    user_id: str,
    created_at: str,
    mood: Optional[int] = None,
    description: Optional[str] = None,
) -> MoodOut:
    """Update the mood value and/or description for an entry.

    The created_at date is immutable; only mood and description fields can change.
    At least one of mood or description must be provided.
    Raises InvalidDate, MoodNotFound, or SupabaseError.
    """
    # Validate date format
    _parse_date(created_at)
    
    row = supabase.update_mood(user_id, created_at, mood, description)
    if not row:
        raise MoodNotFound()
    return MoodOut(
        user_id=row.get("user_id", ""),
        created_at=row.get("created_at", ""),
        mood=row.get("mood", 0),
        description=row.get("description"),
    )


def delete_mood(user_id: str, created_at: str) -> bool:
    """Delete a mood entry.

    Returns True if an entry was deleted, False if no entry existed.
    Idempotent: deleting a non-existent entry is not an error.

    Raises InvalidDate on a malformed date, so a typo is the caller's 400
    rather than a rejected SQL literal surfacing as a 503 "outage". Every
    other verb validates the date first; delete doing so too keeps the
    contract uniform.
    """
    _parse_date(created_at)
    return supabase.delete_mood(user_id, created_at)
