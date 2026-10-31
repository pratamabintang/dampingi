"""Seed script for local development.

Creates four deterministic test Profiles via the real signup path:
one Mother, one Companion, one Professional, and one soft-deleted Mother.
The script is idempotent: running it twice is safe.

    python -m app.seed

Seed emails are fixed (seed.mother@gmail.com, etc.), so re-seeding is
deterministic. If a signup fails, the script halts with a clear error
message and does NOT silently skip (a wrongly-labelled role is worse than
a failed signup). Cleanup is manual: run `python -m app.admin purge` if
needed.
"""

import sys
from typing import List, Optional

from app.auth import supabase as auth_supabase
from app.config import get_settings

SEED_PROFILES = [
    {"name": "Mother", "email": "seed.mother@gmail.com", "role": "mother"},
    {"name": "Companion", "email": "seed.companion@gmail.com", "role": "companion"},
    {
        "name": "Professional",
        "email": "seed.professional@gmail.com",
        "role": "professional",
    },
    {
        "name": "Deleted Mother",
        "email": "seed.deleted.mother@gmail.com",
        "role": "mother",
        "delete": True,
    },
]

SEED_PASSWORD = "Seed!123"

def is_seed_email(email: str) -> bool:
    """True if the email is one of the deterministic seed emails."""
    return email.startswith("seed.") and email.endswith("@gmail.com")

def find_existing_seed_users() -> dict:
    """Find any existing seed users (by admin API).

    Returns a dict: {email: user_id} for users matching seed.*@gmail.com.
    """
    existing = {}
    try:
        page = 1
        while True:
            batch, next_page, _total = auth_supabase.admin_list_users(page=page)
            for user in batch:
                email = user.get("email")
                if email and is_seed_email(email):
                    existing[email] = user.get("id")
            if not next_page:
                break
            page = next_page
    except auth_supabase.SupabaseError as exc:
        raise SystemExit(
            f"Failed to list users: {exc.message} (code={exc.error_code})"
        ) from exc
    return existing

def seed_one(name: str, email: str, role: str, delete: bool = False) -> dict:
    """Create or skip one seed Profile.

    Returns {email, user_id, created, message}.
    - If the user exists, skips creation.
    - If delete=True, bans and tombstones after creation.
    """
    existing = find_existing_seed_users()
    if email in existing:
        return {
            "email": email,
            "user_id": existing[email],
            "created": False,
            "message": "already exists",
        }

    try:
        resp = auth_supabase.signup(
            name=name, email=email, password=SEED_PASSWORD, role=role
        )
        user_id = resp.get("user", {}).get("id")
        if not user_id:
            raise auth_supabase.SupabaseError(
                400, f"Signup returned no user ID for {email}"
            )

        if delete:
            try:
                auth_supabase.ban_user(user_id)
                auth_supabase.tombstone_profile(user_id)
                message = "created and soft-deleted"
            except auth_supabase.SupabaseError as exc:
                raise auth_supabase.SupabaseError(
                    exc.status,
                    f"Failed to soft-delete {email}: {exc.message}",
                    exc.error_code,
                ) from exc
        else:
            message = "created"

        return {"email": email, "user_id": user_id, "created": True, "message": message}

    except auth_supabase.SupabaseError as exc:
        raise SystemExit(
            f"Signup failed for {email} (role={role}): {exc.message} "
            f"(code={exc.error_code})"
        ) from exc

def main(argv: Optional[List[str]] = None) -> int:
    """Seed the database with test Profiles."""
    try:
        settings = get_settings()
    except SystemExit as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    print(f"Seeding {settings.supabase_url}...")
    print()

    results = []
    for profile in SEED_PROFILES:
        result = seed_one(
            name=profile["name"],
            email=profile["email"],
            role=profile["role"],
            delete=profile.get("delete", False),
        )
        results.append(result)
        print(f"  {result['email']:<35} {result['message']}")

    print()
    created = sum(1 for r in results if r["created"])
    print(f"Done. Created {created} new profile(s).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
