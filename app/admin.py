"""Admin CLI for the Supabase test-environment.

Controls the authentication testing process:

    python -m app.admin status         # what test users exist right now
    python -m app.admin purge          # delete leaked test users
    python -m app.admin purge --all    # delete every user in the project

Only runs with SUPABASE_SECRET_KEY configured (dampingi/.env). purge's
default pattern only matches test users (pytest.*/probe.* local parts);
--all is the explicit, unguarded variant.

Why this exists: a test run can crash after registering a user but
before teardown tracks it (endpoint bug, keyboard interrupt). Per-test
teardown (conftest) misses those, so they accumulate. This tool is the
sweep: run it any time, and conftest runs it automatically after each
session.
"""

import argparse
import sys
from typing import Callable, List, Optional

from app import supabase

TEST_EMAIL_PATTERNS = ("pytest.", "probe.")


def is_test_email(email: Optional[str]) -> bool:
    if not email:
        return False
    local = email.split("@", 1)[0]
    return any(local.startswith(p) for p in TEST_EMAIL_PATTERNS)


def all_user_ids() -> List[dict]:
    """Every auth user in the project, following pagination."""
    users = []
    page = 1
    while True:
        batch, next_page, _total = supabase.admin_list_users(page=page)
        users.extend(batch)
        if not next_page:
            break
        page = next_page
    return users


def find_leaked_users() -> List[dict]:
    """Test-pattern users that still exist after (or without) teardown.

    A healthy run leaves none: conftest deletes each test user, then a
    session-end sweep catches anything that slipped past.
    """
    return [u for u in all_user_ids() if is_test_email(u.get("email"))]


def delete_users(users: List[dict], log: Callable[[str], None] = print) -> int:
    deleted = 0
    for user in users:
        user_id = user.get("id")
        email = user.get("email")
        try:
            if supabase.delete_user(user_id):
                log(f"deleted {email}")
                deleted += 1
            else:
                log(f"already gone {email}")
        except supabase.SupabaseError as exc:
            log(f"FAILED {email}: {exc.message}")
    return deleted


def cmd_status(_args) -> int:
    users = all_user_ids()
    leaked = [u for u in users if is_test_email(u.get("email"))]
    # Soft-deleted (ADR-0003): the auth user carries banned_until (the
    # tombstone itself lives on profiles). Test-hygiene view: a deleted
    # test user is inert but still occupies its email (register would
    # 409), so purge must still hard-delete it.
    banned = [u for u in users if u.get("banned_until")]
    print(f"total users:    {len(users)}")
    print(f"test users:    {len(leaked)}  (patterns: {', '.join(TEST_EMAIL_PATTERNS)})")
    print(f"deleted users:  {len(banned)}  (soft-deleted; purge removes test-pattern ones)")
    print(f"other users:   {len(users) - len(leaked)}")
    if leaked:
        print("\nleaked test users:")
        for u in leaked:
            print(f"  {u.get('email')}  id={u.get('id')}")
    return 0


def cmd_purge(args) -> int:
    if args.all:
        users = all_user_ids()
        answer = input(
            f"Delete ALL {len(users)} users in the project? "
            "This cannot be undone. Type the user count to confirm: "
        )
        if answer.strip() != str(len(users)):
            print("aborted")
            return 1
    else:
        users = find_leaked_users()
        print(f"test users to delete: {len(users)}")
    deleted = delete_users(users)
    print(f"deleted {deleted} user(s)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage auth test users in the Supabase project."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="list users and identify leaked test users")

    purge = sub.add_parser("purge", help="delete test users")
    purge.add_argument(
        "--all",
        action="store_true",
        help="delete EVERY user in the project, not just test-pattern ones",
    )

    args = parser.parse_args(argv)
    commands = {"status": cmd_status, "purge": cmd_purge}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
