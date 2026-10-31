"""Integration tests for local Supabase stack and seed script.

Requires: supabase start (Docker running), or marked @pytest.mark.integration to skip.
Tests the full workflow: migrations → seed script → verify data.

Run with: pytest tests/integration/test_seed.py -m integration
Or manually start supabase first: cd ../.. && npx supabase start
"""

import subprocess
from typing import Optional

import httpx
import pytest

from app.config import get_settings


@pytest.mark.integration
@pytest.fixture(scope="session")
def supabase_running() -> bool:
    """Check if supabase is running (status endpoint reachable)."""
    try:
        resp = httpx.get("http://127.0.0.1:54321/auth/v1/.well-known/jwks.json", timeout=2)
        return resp.status_code < 500
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


@pytest.mark.integration
@pytest.fixture(scope="session")
def supabase_db_client(supabase_running: bool) -> httpx.Client:
    """HTTP client for PostgREST queries (uses service key)."""
    if not supabase_running:
        pytest.skip("supabase not running: start with `cd ../.. && npx supabase start`")
    
    settings = get_settings()
    return httpx.Client(
        base_url="http://127.0.0.1:54321",
        headers={
            "apikey": settings.supabase_secret_key,
            "Authorization": f"Bearer {settings.supabase_secret_key}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )


@pytest.mark.integration
def test_migrations_apply(supabase_running: bool, supabase_db_client: httpx.Client) -> None:
    """Green: migrations apply and create the correct schema."""
    if not supabase_running:
        pytest.skip("supabase not running: start with `cd ../.. && npx supabase start`")

    # Reset the database (apply migrations)
    result = subprocess.run(
        ["npx", "supabase", "db", "reset", "--linked"],
        cwd="D:\\comp",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"supabase db reset failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    # Verify: profiles table exists
    resp = supabase_db_client.get(
        "/rest/v1/information_schema.tables",
        params={"table_schema": "eq.public", "table_name": "eq.profiles"},
    )
    assert resp.status_code == 200, f"Failed to query tables: {resp.text}"
    tables = resp.json()
    assert len(tables) > 0, "profiles table does not exist"

    # Verify: profiles columns
    resp = supabase_db_client.get(
        "/rest/v1/information_schema.columns",
        params={"table_schema": "eq.public", "table_name": "eq.profiles"},
    )
    assert resp.status_code == 200, f"Failed to query columns: {resp.text}"
    columns = {c["column_name"] for c in resp.json()}
    expected_columns = {"user_id", "name", "role", "deleted_at", "created_at"}
    assert expected_columns.issubset(columns), f"Missing columns. Expected {expected_columns}, got {columns}"


@pytest.mark.integration
def test_seed_creates_four_profiles(supabase_running: bool, supabase_db_client: httpx.Client) -> None:
    """Green: seed script creates 4 test Profiles via signup."""
    if not supabase_running:
        pytest.skip("supabase not running: start with `cd ../.. && npx supabase start`")

    # First reset to clean state
    result = subprocess.run(
        ["npx", "supabase", "db", "reset", "--linked"],
        cwd="D:\\comp",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"supabase db reset failed: {result.stderr}"

    # Run the seed script
    result = subprocess.run(
        ["python", "-m", "app.seed"],
        cwd="D:\\comp\\dampingi",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"seed script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "Created 4 new profile(s)" in result.stdout, f"Unexpected output: {result.stdout}"

    # Query profiles and verify
    resp = supabase_db_client.get("/rest/v1/profiles")
    assert resp.status_code == 200, f"Failed to query profiles: {resp.text}"
    profiles = resp.json()
    assert len(profiles) == 4, f"Expected 4 profiles, got {len(profiles)}"

    # Verify each profile
    seed_emails = {
        "seed.mother@gmail.com": {"name": "Mother", "role": "mother", "deleted": False},
        "seed.companion@gmail.com": {"name": "Companion", "role": "companion", "deleted": False},
        "seed.professional@gmail.com": {"name": "Professional", "role": "professional", "deleted": False},
        "seed.deleted.mother@gmail.com": {"name": "Deleted Mother", "role": "mother", "deleted": True},
    }

    for profile in profiles:
        email = profile["email"] or "unknown"
        assert email in seed_emails, f"Unexpected email: {email}"
        expected = seed_emails[email]
        assert profile["name"] == expected["name"], f"Email {email}: name mismatch"
        assert profile["role"] == expected["role"], f"Email {email}: role mismatch"
        if expected["deleted"]:
            assert profile["deleted_at"] is not None, f"Email {email}: should be soft-deleted"
        else:
            assert profile["deleted_at"] is None, f"Email {email}: should not be deleted"


@pytest.mark.integration
def test_seed_idempotency(supabase_running: bool, supabase_db_client: httpx.Client) -> None:
    """Green: running seed script twice creates no duplicates."""
    if not supabase_running:
        pytest.skip("supabase not running: start with `cd ../.. && npx supabase start`")

    # First reset to clean state
    result = subprocess.run(
        ["npx", "supabase", "db", "reset", "--linked"],
        cwd="D:\\comp",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"supabase db reset failed: {result.stderr}"

    # Run the seed script
    result1 = subprocess.run(
        ["python", "-m", "app.seed"],
        cwd="D:\\comp\\dampingi",
        capture_output=True,
        text=True,
    )
    assert result1.returncode == 0, f"First seed run failed: {result1.stderr}"
    assert "Created 4 new profile(s)" in result1.stdout

    # Query count after first run
    resp = supabase_db_client.get("/rest/v1/profiles")
    assert resp.status_code == 200
    count_after_first = len(resp.json())
    assert count_after_first == 4, f"Expected 4 profiles after first run, got {count_after_first}"

    # Run the seed script again
    result2 = subprocess.run(
        ["python", "-m", "app.seed"],
        cwd="D:\\comp\\dampingi",
        capture_output=True,
        text=True,
    )
    assert result2.returncode == 0, f"Second seed run failed: {result2.stderr}"
    assert "Created 0 new profile(s)" in result2.stdout, f"Expected no new profiles, got: {result2.stdout}"
    assert "already exists" in result2.stdout, "Expected 'already exists' messages"

    # Query count after second run
    resp = supabase_db_client.get("/rest/v1/profiles")
    assert resp.status_code == 200
    count_after_second = len(resp.json())
    assert count_after_second == 4, f"Expected still 4 profiles after second run, got {count_after_second}"
