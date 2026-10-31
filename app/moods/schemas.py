"""Data contracts for the moods domain: custom types, requests, responses."""

from typing import Optional

from pydantic import BaseModel, Field


class MoodCreate(BaseModel):
    """Create a mood entry: date, mood value, and optional description."""

    created_at: str = Field(
        description="Date in YYYY-MM-DD format (user's timezone)."
    )
    mood: int = Field(ge=1, le=5, description="Mood on 1–5 scale (1=very low, 5=very good).")
    description: Optional[str] = Field(
        default=None, max_length=500, description="Optional contextual note (max 500 chars)."
    )


class MoodUpdate(BaseModel):
    """Update a mood entry: mood and/or description."""

    model_config = {"extra": "forbid"}  # Reject any other fields

    mood: Optional[int] = Field(
        default=None, ge=1, le=5, description="Mood on 1–5 scale (1=very low, 5=very good)."
    )
    description: Optional[str] = Field(
        default=None, max_length=500, description="Optional contextual note (max 500 chars)."
    )


class MoodOut(BaseModel):
    """A mood entry in the API response."""

    user_id: str
    created_at: str  # YYYY-MM-DD date
    mood: int  # 1–5 scale
    description: Optional[str]  # Optional contextual note


class MoodListOut(BaseModel):
    """List of mood entries."""

    moods: list[MoodOut]
    count: int
