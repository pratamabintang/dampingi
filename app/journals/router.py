"""API surface of the journals domain: routes, status codes, token auth."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.auth import service as auth_service
from app.auth.guards import require_any_role
from app.journals import service, supabase
from app.journals.schemas import (
    JournalCreate,
    JournalListOut,
    JournalOut,
    JournalUpdate,
)

router = APIRouter(tags=["journals"])

# Role gate: Mothers and Companions both write Journals. Stored at module
# level so offline tests can override it (the same seam as moods).
mother_or_companion = require_any_role("mother", "companion")


def _handle_journal_errors(func, *args):
    """Run a service call, mapping domain/transport errors to HTTP.

    Follows the pattern from auth.router._handle_auth_errors.
    """
    try:
        return func(*args)
    except service.JournalInvalidDate as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except service.JournalFutureDate:
        raise HTTPException(
            status_code=400,
            detail="A Journal cannot be written for a future date",
        )
    except service.JournalDuplicate as exc:
        raise HTTPException(
            status_code=409,
            detail=f"A journal already exists for {exc.created_at}",
        )
    except service.JournalProfileNotFound:
        raise HTTPException(
            status_code=404,
            detail="User profile not found or has been deleted",
        )
    except service.JournalNotFound:
        raise HTTPException(status_code=404, detail="Journal not found")
    except service.JournalNothingToUpdate:
        raise HTTPException(
            status_code=400,
            detail="Provide a title, a content, or both to edit a Journal",
        )
    except supabase.SupabaseError as exc:
        raise HTTPException(status_code=503, detail=exc.message)


@router.post(
    "/journals",
    status_code=201,
    response_model=JournalOut,
    summary="Write a Journal",
    description=(
        "One Journal per Author per day. The day defaults to today in the "
        "Author's own timezone; a past day may be backfilled, a future day "
        "is rejected. Only Mothers and Companions may write Journals."
    ),
)
def create_journal(
    payload: JournalCreate,
    session: auth_service.Session = Depends(mother_or_companion),
) -> JournalOut:
    return _handle_journal_errors(
        service.create_journal,
        session.user_id,
        payload.title,
        payload.content,
        payload.created_at,
    )


@router.get(
    "/journals",
    response_model=JournalListOut,
    summary="List my Journals with optional date range and pagination",
    description=(
        "Every Journal this Author has written, newest first. "
        "Optional start_date and end_date filter by YYYY-MM-DD date range. "
        "offset and limit enable pagination (default: no pagination, return all). "
        "When provided, limit is capped at 100."
    ),
)
def list_journals(
    session: auth_service.Session = Depends(mother_or_companion),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    offset: Optional[int] = Query(None, ge=0, description="Number of results to skip (enables pagination)"),
    limit: Optional[int] = Query(None, ge=1, le=100, description="Number of results to return (max 100)"),
) -> JournalListOut:
    result = _handle_journal_errors(
        service.list_journals,
        session.user_id,
        start_date,
        end_date,
        offset,
        limit,
    )
    return result


@router.patch(
    "/journals/{created_at}",
    response_model=JournalOut,
    summary="Edit the Journal for a day",
    description=(
        "Change the title, the content, or both. The day is fixed when a "
        "Journal is written: a `created_at` in the payload is ignored, and "
        "the entry never moves. A day with no Journal for this Author is "
        "reported as not found, so another Author's entry stays invisible."
    ),
)
def update_journal(
    created_at: str,
    payload: JournalUpdate,
    session: auth_service.Session = Depends(mother_or_companion),
) -> JournalOut:
    return _handle_journal_errors(
        service.update_journal,
        session.user_id,
        created_at,
        payload.title,
        payload.content,
    )


@router.delete(
    "/journals/{created_at}",
    status_code=204,
    summary="Delete the Journal for a day",
    description=(
        "Removes this Author's Journal for that day. Not idempotent: a day "
        "with no Journal for this Author is reported as not found, so a "
        "delete confirms nothing about another Author's entries."
    ),
)
def delete_journal(
    created_at: str,
    session: auth_service.Session = Depends(mother_or_companion),
) -> Response:
    _handle_journal_errors(
        service.delete_journal,
        session.user_id,
        created_at,
    )
    return Response(status_code=204)


@router.get(
    "/journals/{created_at}",
    response_model=JournalOut,
    summary="Read the Journal for a day",
    description=(
        "Returns this Author's Journal for that day. Another Author's "
        "Journal is reported as not found: its existence is never confirmed."
    ),
)
def get_journal(
    created_at: str,
    session: auth_service.Session = Depends(mother_or_companion),
) -> JournalOut:
    return _handle_journal_errors(
        service.get_journal,
        session.user_id,
        created_at,
    )
