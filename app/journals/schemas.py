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
