"""API surface of the journals domain: routes, status codes, token auth."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import service as auth_service
from app.auth.guards import require_any_role
from app.journals import service, supabase
from app.journals.schemas import JournalCreate, JournalOut

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
