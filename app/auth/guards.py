"""Authorization guards: role-based access control dependencies.

Guards are FastAPI dependencies that check authorization before route handlers
run. They use the auth seam (ADR-0006) to verify role from the profiles table.

Usage:
    from app.auth.guards import require_role
    from app.auth.service import Session

    @router.get("/resource")
    def get_resource(session: Session = Depends(require_role("mother"))):
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
