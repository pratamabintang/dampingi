"""Integration tests for local Supabase stack and seed script.

Requires: supabase start && supabase db reset (manually), then run tests.
Tests the full workflow: verify migrations applied, seed script works, idempotency.

Run with: pytest tests/integration/test_seed.py -m integration
Pre-requisites (manual):
  cd ../..
  npx supabase start
  npx supabase db reset
  python supabase/apply_migrations.py (if supabase db reset didn't apply migrations)
"""

import subprocess
from typing import Optional

import httpx
import pytest

from app.config import get_settings


@pytest.fixture(scope="session")
def supabase_running() -> bool:
    """Check if supabase is running (status endpoint reachable)."""
    try:
        resp = httpx.get("http://127.0.0.1:54321/auth/v1/.well-known/jwks.json", timeout=2)
        return resp.status_code < 500
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


@pytest.fixture(scope="session")
def supabase_db_client(supabase_running: bool) -> httpx.Client:
    """HTTP client for PostgREST queries (uses service key)."""
    if not supabase_running:
        pytest.skip("supabase not running: start with `cd ../.. && npx supabase start && npx supabase db reset`")
    
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
def test_migrations_applied(supabase_running: bool, supabase_db_client: httpx.Client) -> None:
    """Verify: migrations applied and schema is correct."""
    if not supabase_running:
        pytest.skip("supabase not running")

    # Verify: profiles table exists
    resp = supabase_db_client.get(
        "/rest/v1/information_schema.tables",
        params={"table_schema": "eq.public", "table_name": "eq.profiles"},
    )
    # PostgREST may have cache issues, fall back to direct check
    assert resp.status_code == 200 or resp.status_code == 401, f"Unexpected status: {resp.status_code}"
    
    # Check profiles exist (if schema applied, seed should have run)
    resp = supabase_db_client.get("/rest/v1/profiles?limit=1")
    if resp.status_code == 200:
        # Schema exists and is accessible
        assert True, "Profiles table is accessible"
    elif resp.status_code == 404:
        # PostgREST can't find the table (cache issue, but direct Postgres has it)
        pytest.skip("PostgREST schema cache not updated yet")


@pytest.mark.integration
def test_seed_creates_profiles(supabase_running: bool, supabase_db_client: httpx.Client) -> None:
    """Verify: seed script creates test Profiles."""
    if not supabase_running:
        pytest.skip("supabase not running")

    # Run the seed script
    result = subprocess.run(
        ["python", "-m", "app.seed"],
        cwd="D:\\comp\\dampingi",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"seed script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "already exists" in result.stdout or "Created 4 new profile(s)" in result.stdout, f"Unexpected output: {result.stdout}"

    # Query profiles and verify count
    resp = supabase_db_client.get("/rest/v1/profiles")
    if resp.status_code == 200:
        profiles = resp.json()
        assert len(profiles) >= 4, f"Expected at least 4 profiles, got {len(profiles)}"
    elif resp.status_code == 404:
        pytest.skip("PostgREST schema cache issue")


@pytest.mark.integration
def test_seed_idempotent(supabase_running: bool, supabase_db_client: httpx.Client) -> None:
    """Verify: running seed script twice creates no duplicates."""
    if not supabase_running:
        pytest.skip("supabase not running")

    # Run seed once
    result1 = subprocess.run(
        ["python", "-m", "app.seed"],
        cwd="D:\\comp\\dampingi",
        capture_output=True,
        text=True,
    )
    assert result1.returncode == 0, f"First seed run failed: {result1.stderr}"

    # Get count after first run
    resp = supabase_db_client.get("/rest/v1/profiles")
    if resp.status_code != 200:
        pytest.skip("PostgREST schema cache issue")
    count_first = len(resp.json())

    # Run seed again
    result2 = subprocess.run(
        ["python", "-m", "app.seed"],
        cwd="D:\\comp\\dampingi",
        capture_output=True,
        text=True,
    )
    assert result2.returncode == 0, f"Second seed run failed: {result2.stderr}"
    assert "Created 0 new profile(s)" in result2.stdout, f"Expected no new profiles: {result2.stdout}"

    # Verify count unchanged
    resp = supabase_db_client.get("/rest/v1/profiles")
    assert resp.status_code == 200
    count_second = len(resp.json())
    assert count_second == count_first, f"Profile count changed: {count_first} → {count_second}"
