"""Authorization guards: role-based access control dependencies.

Guards are FastAPI dependencies that check authorization before route handlers
run. They use the auth seam (ADR-0006) to verify role from the profiles table.

Usage:
    from app.auth.guards import require_role, require_any_role
    from app.auth.service import Session

    @router.get("/resource")
    def get_resource(session: Session = Depends(require_role("mother"))):
        ...

    @router.get("/shared")
    def get_shared(session: Session = Depends(require_any_role("mother", "companion"))):
        ...

Guard failures raise 403 Forbidden (authenticated but not authorized).
"""

from fastapi import Depends, HTTPException

from app.auth import supabase as auth_supabase, service as auth_service
from app.auth.router import verified_session


def require_role(role: str):
    """Dependency factory: verify session user has the required role.
    
    Args:
        role: Required role (e.g., "mother", "professional", "companion").
              Must match profiles.role exactly (case-sensitive, lowercase).
    
    Returns:
        A FastAPI dependency that validates role and returns the session.
        Raises 403 if role doesn't match.
    
    Example:
        mother_only = require_role("mother")
        
        @router.post("/moods")
        def create_mood(
            payload: MoodCreate,
            session: Session = Depends(mother_only),
        ):
            ...
    """
    
    def _check_role(
        session: auth_service.Session = Depends(verified_session),
    ) -> auth_service.Session:
        """Check that the session's user has the required role.
        
        Fetches the profile from the DB (profiles table) — the immutable,
        server-side source of truth. Never trusts role from JWT
        (user_metadata is user-writable; ADR-0005).
        
        Raises:
            HTTPException(403) if role doesn't match or profile not found.
        """
        profile = auth_supabase.fetch_profile(session.user_id)
        if not profile or profile.get("role") != role:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied: {role} role required",
            )
        return session
    
    return _check_role


def require_any_role(*roles: str):
    """Dependency factory: verify session user has any one of `roles`.

    For endpoints that serve more than one kind of participant. The Role is
    still exactly one per Profile (ADR-0005) — this gate widens who may
    enter, it does not make a Profile hold several Roles.

    Args:
        roles: Accepted roles (e.g. "mother", "companion"). Must match
               profiles.role exactly (case-sensitive, lowercase).

    Returns:
        A FastAPI dependency that validates role and returns the session.
        Raises 403 if the profile's role is not among `roles`.

    Example:
        mother_or_companion = require_any_role("mother", "companion")

        @router.post("/journals")
        def create_journal(
            payload: JournalCreate,
            session: Session = Depends(mother_or_companion),
        ):
            ...
    """
    if not roles:
        raise ValueError("require_any_role needs at least one role")

    def _check_any_role(
        session: auth_service.Session = Depends(verified_session),
    ) -> auth_service.Session:
        """Check that the session's user has one of the accepted roles.

        Fetches the profile from the DB (profiles table) — the immutable,
        server-side source of truth. Never trusts role from JWT
        (user_metadata is user-writable; ADR-0005).

        Raises:
            HTTPException(403) if the role is not accepted or no profile.
        """
        profile = auth_supabase.fetch_profile(session.user_id)
        if not profile or profile.get("role") not in roles:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Access denied: "
                    f"{' or '.join(roles)} role required"
                ),
            )
        return session

    return _check_any_role
