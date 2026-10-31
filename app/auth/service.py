"""Logic for the auth domain: the flows.

Never speaks HTTP: transport failures surface as SupabaseError, the
domain rule as AccountDeleted. Router maps both to status codes.
"""

from dataclasses import dataclass

from app.auth import supabase
from app.auth.schemas import UserOut


class AccountDeleted(Exception):
    """The account is soft-deleted (ADR-0003): no login, no actions."""


class CurrentPasswordIncorrect(Exception):
    """The caller-supplied current password did not verify."""


class PasswordUnchanged(Exception):
    """The new password is the same as the current one."""


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


def register_user(name: str, email: str, password: str, role: str) -> Account:
    """Register; returns the created auth user, access token, profile.

    Raises SupabaseError (409-conflict etc.) for the router to map.
    """
    body = supabase.signup(name, email, password, role)
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
    active_profile(session.user_id)

    supabase.revoke_sessions(session.token)
    supabase.ban_user(session.user_id)
    supabase.tombstone_profile(session.user_id)


def profile_out(session: Session, profile: dict) -> UserOut:
    """The API shape of a user, from a verified Session + Profile row.

    Every user-facing response goes through here, so `role` cannot be
    forgotten by one endpoint (it was: GET /me built UserOut by hand).
    """
    return UserOut(
        id=session.user_id,
        email=session.email,
        name=profile.get("name"),
        role=profile.get("role"),
    )


def user_out(account: Account) -> UserOut:
    """The API shape of a user, from an assembled Account."""
    return UserOut(
        id=account.user.get("id", ""),
        email=account.user.get("email", ""),
        name=account.profile.get("name"),
        role=account.profile.get("role"),
    )


def update_name(session: Session, name: str) -> UserOut:
    """Change the caller's name; returns the updated Profile shape.

    Raises AccountDeleted for a tombstoned caller (ADR-0003 invariant).
    """
    profile = active_profile(session.user_id)
    updated = supabase.update_profile_name(session.user_id, name)
    return profile_out(session, {**profile, **updated})


def change_password(session: Session, current_password: str, new_password: str) -> None:
    """Change the caller's password, verifying the current one first.

    Supabase's PUT /auth/v1/user accepts a new password without checking
    the old one, so a stolen token would be enough to seize the account.
    We verify by attempting a login; that also mints a throwaway session
    whose refresh token we revoke immediately.

    On success every session is revoked (scope=global): a password change
    normally means someone else may know the old one. The caller must log
    in again.

    Raises AccountDeleted, CurrentPasswordIncorrect, PasswordUnchanged,
    or SupabaseError.
    """
    active_profile(session.user_id)

    try:
        throwaway = supabase.login(session.email, current_password)
    except supabase.SupabaseError as exc:
        # The credential did not verify, so nothing may change. But a
        # throttled or unreachable upstream is NOT a wrong password:
        # only a 4xx that isn't 429 means "you typed it wrong".
        if exc.is_transport or exc.status == 429:
            raise
        raise CurrentPasswordIncorrect() from exc

    # Don't leave the verification session alive. scope=local on purpose:
    # scope=global here would kill the caller's own session too, and the
    # password update below would fail with session_not_found.
    _revoke_quietly(throwaway.get("access_token", ""), scope="local")

    # Only after the credential verifies can we say anything about the
    # new password (a wrong current password must win the error race).
    if current_password == new_password:
        raise PasswordUnchanged()

    supabase.update_password(session.token, new_password)
    supabase.revoke_sessions(session.token)


def _revoke_quietly(user_token: str, scope: str) -> None:
    """Best-effort session revoke: cleanup must not fail the operation.

    The throwaway verification session is a courtesy, not a guarantee —
    the global revoke that follows a successful change covers it anyway.
    """
    try:
        supabase.revoke_sessions(user_token, scope=scope)
    except supabase.SupabaseError:
        pass
