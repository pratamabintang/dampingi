"""Logic for the journals domain: the flows.

Never speaks HTTP: validation failures and business rules surface as domain
exceptions, and the router maps them to status codes.

A Journal's day is the Author's local calendar day, not the server's
(ADR-0007). That is why this module, and not the client, decides what
"today" means.
"""

from datetime import date, datetime, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.auth import supabase as auth_supabase
from app.journals import supabase
from app.journals.schemas import JournalOut
from app.supabase import SupabaseError


class JournalInvalidDate(Exception):
    """The supplied date is malformed or not a real calendar day."""


class JournalFutureDate(Exception):
    """A Journal cannot be written for a day that has not happened yet."""


class JournalDuplicate(Exception):
    """The Author already has a Journal for that day."""

    def __init__(self, created_at: str):
        super().__init__(created_at)
        self.created_at = created_at


class JournalProfileNotFound(Exception):
    """The caller has no active Profile."""


class JournalNotFound(Exception):
    """No Journal exists for this Author on this day."""# Postgres SQLSTATE for a unique violation. PostgREST forwards it as the
# body's `code`, so a duplicate day is distinguishable without matching on
# prose.
#
# Keying on the SQLSTATE and NOT on the HTTP status is load-bearing:
# PostgREST answers 409 for a foreign-key violation too (23503), verified
# against the real project. A Profile hard-deleted between the guard and
# the insert would otherwise be reported as "you already wrote today".
_UNIQUE_VIOLATION = "23505"


def _parse_date(date_str: str) -> date:
    """Parse a YYYY-MM-DD string, raising JournalInvalidDate."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise JournalInvalidDate(
            f"Invalid date format: expected YYYY-MM-DD, got '{date_str}'"
        ) from exc


def _zone_of(profile: Optional[dict]) -> tzinfo:
    """The Profile's timezone, or UTC when it is missing or unusable.

    profiles.timezone is text, so the database accepts anything in it. An
    unloadable zone must not break writing a Journal: fall back to UTC
    rather than raising.

    The fallback is `timezone.utc`, not `ZoneInfo("UTC")`: zoneinfo needs a
    tz database on the host (or the `tzdata` package), and "no tz database"
    must not be the thing that stops a Journal being written.
    """
    name = (profile or {}).get("timezone")
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return timezone.utc


def _today_for(profile: Optional[dict]) -> date:
    """Today in the Author's timezone (ADR-0007)."""
    return datetime.now(_zone_of(profile)).date()


def _to_out(row: dict) -> JournalOut:
    """Map a stored row to the API contract.

    No `.get(..., "")` defaults: a row missing a column means the transport
    or the schema is wrong, and inventing an empty string would turn that
    into a silent 201 with a blank Journal.
    """
    return JournalOut(
        user_id=row["user_id"],
        created_at=row["created_at"],
        title=row["title"],
        content=row["content"],
    )


def _is_duplicate(exc: SupabaseError) -> bool:
    """True when PostgREST refused the insert on the composite key.

    Deliberately the SQLSTATE only, never `status == 409`: a foreign-key
    violation is also a 409, and calling that "duplicate day" would be a
    lie to the Author.
    """
    return exc.error_code == _UNIQUE_VIOLATION


def create_journal(
    user_id: str, title: str, content: str, created_at: Optional[str] = None
) -> JournalOut:
    """Write a Journal, dated to the Author's local today unless given.

    A past day may be backfilled; a future day is refused, because a
    Journal describes a day that has happened.

    Raises JournalInvalidDate, JournalFutureDate, JournalDuplicate,
    JournalProfileNotFound, or SupabaseError.
    """
    profile = auth_supabase.fetch_profile(user_id)
    if not profile or profile.get("deleted_at"):
        raise JournalProfileNotFound()

    if created_at is None:
        day = _today_for(profile).isoformat()
    else:
        parsed = _parse_date(created_at)
        if parsed > _today_for(profile):
            raise JournalFutureDate()
        day = parsed.isoformat()

    try:
        row = supabase.create_journal(user_id, day, title, content)
    except SupabaseError as exc:
        if _is_duplicate(exc):
            raise JournalDuplicate(day) from exc
        raise

    return _to_out(row)


def get_journal(user_id: str, created_at: str) -> JournalOut:
    """Read this Author's Journal for a day.

    Raises JournalNotFound when there is none — including when the day
    belongs to another Author, which is simply not in scope. The two
    cases are deliberately indistinguishable: a 404 either way never
    confirms that someone else's Journal exists.

    Raises JournalInvalidDate on a malformed date, SupabaseError on a
    transport failure.
    """
    day = _parse_date(created_at).isoformat()
    row = supabase.fetch_journal(user_id, day)
    if not row:
        raise JournalNotFound()
    return _to_out(row)


def list_journals(user_id: str) -> list[JournalOut]:
    """Every Journal this Author has written, newest first.

    There is no parameter that could widen this to another Author: the
    transport is scoped to `user_id` and nothing here accepts a second
    identity. An Author with no Journals gets an empty list.

    Raises SupabaseError on a transport failure.
    """
    return [_to_out(row) for row in supabase.list_journals(user_id)]
