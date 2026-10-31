"""API surface of the moods domain: routes, status codes, token auth."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.auth.guards import require_role
from app.auth import service as auth_service
from app.supabase import SupabaseError
from app.moods import service, supabase
from app.moods.schemas import (
    MoodCreate,
    MoodListOut,
    MoodOut,
    MoodUpdate,
)

router = APIRouter(tags=["moods"])

# Role gate: only mothers can access mood endpoints (stored at module level for test overrides)
mother_only = require_role("mother")


def _handle_mood_errors(func, *args):
    """Run a service call, mapping domain/transport errors to HTTP.

    Follows the pattern from auth.router._handle_auth_errors.
    """
    try:
        return func(*args)
    except service.InvalidDate as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
    except service.MoodFutureDate:
        raise HTTPException(
            status_code=400,
            detail="Mood entries cannot be created or updated for future dates",
        )
    except service.ProfileNotFound:
        raise HTTPException(
            status_code=404,
            detail="User profile not found or has been deleted",
        )
    except service.MoodNotFound:
        raise HTTPException(status_code=404, detail="Mood entry not found")
    except supabase.SupabaseError as exc:
        raise HTTPException(status_code=503, detail=exc.message)


@router.post(
    "/moods",
    status_code=201,
    response_model=MoodOut,
    summary="Create a mood entry for a specific date",
    description="One entry per user per day (by UTC date). Future dates are rejected.",
)
def create_mood(
    payload: MoodCreate,
    session: auth_service.Session = Depends(mother_only),
) -> MoodOut:
    return _handle_mood_errors(
        service.create_mood,
        session.user_id,
        payload.created_at,
        payload.mood,
        payload.description,
    )


@router.get(
    "/moods/{created_at}",
    response_model=MoodOut,
    summary="Get a mood entry for a specific date",
)
def get_mood(
    created_at: str,
    session: auth_service.Session = Depends(mother_only),
) -> MoodOut:
    mood = _handle_mood_errors(
        service.get_mood,
        session.user_id,
        created_at,
    )
    if mood is None:
        raise HTTPException(status_code=404, detail="Mood entry not found")
    return mood


@router.get(
    "/moods",
    response_model=MoodListOut,
    summary="List mood entries with optional date range",
    description=(
        "Query parameters start_date and end_date are YYYY-MM-DD dates (optional). "
        "Results are ordered newest first. Default: last 7 days."
    ),
)
def list_moods(
    session: auth_service.Session = Depends(mother_only),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
) -> MoodListOut:
    moods = _handle_mood_errors(
        service.list_moods,
        session.user_id,
        start_date,
        end_date,
    )
    return MoodListOut(moods=moods, count=len(moods))


@router.patch(
    "/moods/{created_at}",
    response_model=MoodOut,
    summary="Update the mood value and/or description for an entry",
    description=(
        "Update mood, description, or both. The date (created_at) is immutable. "
        "The entry must exist. At least one field must be provided."
    ),
)
def update_mood(
    created_at: str,
    payload: MoodUpdate,
    session: auth_service.Session = Depends(mother_only),
) -> MoodOut:
    return _handle_mood_errors(
        service.update_mood,
        session.user_id,
        created_at,
        payload.mood,
        payload.description,
    )


@router.delete(
    "/moods/{created_at}",
    status_code=204,
    summary="Delete a mood entry",
    description="Idempotent: deleting a non-existent entry returns 204.",
)
def delete_mood(
    created_at: str,
    session: auth_service.Session = Depends(mother_only),
):
    _handle_mood_errors(
        service.delete_mood,
        session.user_id,
        created_at,
    )
    return Response(status_code=204)
