"""Logic for the auth domain: the flows.

Never speaks HTTP: transport failures surface as SupabaseError, the
domain rule as AccountDeleted. Router maps both to status codes.
"""

from dataclasses import dataclass

from app import supabase
from app.auth.schemas import UserOut


class AccountDeleted(Exception):
    """The account is soft-deleted (ADR-0003): no login, no actions."""


@dataclass(frozen=True)
class Session:
    """The caller's verified identity, plus the raw token (used to
    revoke the session it arrived on during soft delete)."""

    user_id: str
    email: str
    token: str


@dataclass(frozen=True)
class Account:
    """A user and their profile, as assembled from Supabase."""

    user: dict
    token: str
    profile: dict


def register_user(name: str, email: str, password: str) -> Account:
    """Register; returns the created auth user, access token, profile.

    Raises SupabaseError (409-conflict etc.) for the router to map.
    """
    body = supabase.signup(name, email, password)
    user = body.get("user") or {}
    # Signup responses come in two shapes: flat (access_token at top
    # level — current API) or {user, session}. Handle both; the token
    # is None only when email confirmation is enabled on the project.
    session = body.get("session") or {}
    access_token = body.get("access_token") or session.get("access_token")
    profile = supabase.fetch_profile(user.get("id", ""))
    return Account(user=user, token=access_token or "", profile=profile or {})


def login_user(email: str, password: str) -> Account:
    """Password grant; returns the user, access token, and profile.

    Supabase answers identically for unknown email and wrong password;
    a banned (soft-deleted) user gets error_code user_banned. The
    router distinguishes; this layer passes the error through.
    """
    body = supabase.login(email, password)
    user = body.get("user") or {}
    profile = supabase.fetch_profile(user.get("id", ""))
    return Account(user=user, token=body.get("access_token", ""), profile=profile or {})


def active_profile(user_id: str) -> dict:
    """The profile, or AccountDeleted when tombstoned (ADR-0003)."""
    profile = supabase.fetch_profile(user_id)
    if profile and profile.get("deleted_at"):
        raise AccountDeleted()
    return profile or {}


def logout(session: Session) -> None:
    """Log the caller out: revoke every session (scope=global).

    Kills all refresh tokens, so no new access tokens can be minted;
    the caller's current access token keeps verifying until it expires
    (Supabase has no access-token revocation).

    A repeat logout is not an error: Supabase answers 403
    session_not_found once the session is gone, which is the desired
    end state. A tombstoned caller raises AccountDeleted — deleted
    users can't act (ADR-0003), and their logout would fail upstream
    anyway because soft delete already revoked their sessions.
    """
    active_profile(session.user_id)

    try:
        supabase.revoke_sessions(session.token)
    except supabase.SupabaseError as exc:
        if exc.error_code == "session_not_found":
            return
        raise


def soft_delete_user(session: Session) -> None:
    """Soft delete the caller (ADR-0003): revoke sessions, ban, tombstone.

    Order is load-bearing: the logout must run before the ban — Supabase
    rejects a banned user's logout call. The whole call is idempotent;
    a retry after a partial failure completes it.
    """
    profile = supabase.fetch_profile(session.user_id)
    if profile and profile.get("deleted_at"):
        raise AccountDeleted()

    supabase.revoke_sessions(session.token)
    supabase.ban_user(session.user_id)
    supabase.tombstone_profile(session.user_id)


def user_out(account: Account) -> UserOut:
    """The API shape of a user, from an assembled Account."""
    return UserOut(
        id=account.user.get("id", ""),
        email=account.user.get("email", ""),
        name=account.profile.get("name"),
    )
