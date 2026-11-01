"""Data contracts for the journals domain: requests, responses."""

from typing import Optional

from pydantic import BaseModel, Field

TITLE_MAX = 255
CONTENT_MAX = 10_000


class JournalCreate(BaseModel):
    """Write a Journal: a title, its content, and optionally the day.

    `created_at` is the Author's local calendar day (ADR-0007). Omitted
    means today, resolved server-side from the Profile's timezone — a
    client cannot name a future day, and cannot choose its own "today".
    """

    model_config = {"extra": "forbid"}

    title: str = Field(
        min_length=1,
        max_length=TITLE_MAX,
        description="Title of the Journal (1-255 chars).",
    )
    content: str = Field(
        min_length=1,
        max_length=CONTENT_MAX,
        description="Body of the Journal (1-10000 chars).",
    )
    created_at: Optional[str] = Field(
        default=None,
        description=(
            "Day the Journal belongs to, in YYYY-MM-DD (the Author's local "
            "calendar day). Omit for today. Past days may be backfilled; "
            "future days are rejected."
        ),
    )


class JournalOut(BaseModel):
    """A Journal in the API response."""

    user_id: str
    created_at: str  # YYYY-MM-DD date
    title: str
    content: str


class JournalUpdate(BaseModel):
    """Edit a Journal: a new title, new content, or both.

    The day is not part of an edit. `created_at` is accepted and ignored so
    a client can round-trip a Journal body back without having to strip the
    date first; the entry stays on the day named in the URL, because editing
    is not a way to move an entry to another day.

    Every other unknown field is refused: a typo'd key would otherwise look
    like a successful edit that changed nothing.
    """

    model_config = {"extra": "forbid"}

    title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=TITLE_MAX,
        description="New title (1-255 chars). Omit to leave it unchanged.",
    )
    content: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=CONTENT_MAX,
        description="New content (1-10000 chars). Omit to leave it unchanged.",
    )
    created_at: Optional[str] = Field(
        default=None,
        description=(
            "Ignored. A Journal's day is fixed when it is written; the day "
            "in the URL is the one that is edited."
        ),
    )


class JournalListOut(BaseModel):
    """An Author's Journals, newest first.

    Mirrors the moods list contract (`{moods, count}`) so a client reads both
    domains the same way. `count` is the number of Journals in this response,
    which is the Author's whole history — the transport pages through the
    upstream row cap, so it is never a silently truncated window.
    """

    journals: list[JournalOut]
    count: int
