import importlib
import os
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest

# dampingi is a flat module app (main, auth, config, supabase at the
# app root); tests sit inside it, so the root is already importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TESTS_DIR = Path(__file__).resolve().parent
TEST_ENV_FILE = TESTS_DIR / ".env"

REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY")
SKIP_REASON = (
    f"live Supabase credentials not configured: create {TESTS_DIR / '.env'} "
    f"from {TESTS_DIR / '.env.example'} (see docs/supabase-setup.md); "
    "missing keys are auto-detected and the whole module skips"
)


def _load_env_file(path: Path) -> dict:
    """Minimal .env loader so the tests need no python-dotenv."""
    values = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _missing_keys() -> list:
    values = {}
    for key, value in _load_env_file(TEST_ENV_FILE).items():
        # .env.example ships empty values; they don't count as configured
        if value:
            values[key] = value
    for key, value in values.items():
        os.environ[key] = value
    return [key for key in REQUIRED_ENV if not os.environ.get(key)]


_dummy_client: object = None


@pytest.fixture(scope="session")
def client(supabase_env):
    """FastAPI TestClient against the real app; Supabase is really
    called, so every test in this suite is an integration test.

    Depends on supabase_env so the skip is decided BEFORE the app is
    imported (importing it without credentials makes config exit)."""
    global _dummy_client
    from fastapi.testclient import TestClient

    main = importlib.import_module("main")
    _dummy_client = TestClient(main.app)
    return _dummy_client


@pytest.fixture(scope="session")
def supabase_env():
    missing = _missing_keys()
    if missing:
        pytest.skip(f"{SKIP_REASON} [{', '.join(missing)}]")
    base_url = os.environ["SUPABASE_URL"].rstrip("/")
    # sanity: JWKS must be reachable, otherwise /me can never work
    import httpx

    resp = httpx.get(f"{base_url}/auth/v1/.well-known/jwks.json", timeout=10)
    if resp.status_code >= 400:
        pytest.skip(f"JWKS discovery endpoint not reachable: {resp.status_code}")
    return base_url


@pytest.fixture
def unique_email():
    """Callable returning a fresh Gmail address each time (12 hex chars
    make it unique), so one test can register several users."""
    def _generate():
        return f"pytest.{uuid.uuid4().hex[:12]}@gmail.com"

    yield _generate


@pytest.fixture
def tracked_users() -> Iterator[list]:
    """Test-created user ids; deleted via the admin API at teardown
    (the profiles row goes with them via FK cascade)."""
    ids: list = []
    yield ids
    for user_id in ids:
        from config import supabase_service_http, settings
        import httpx

        resp = httpx.delete(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}",
            headers=supabase_service_http(),
            timeout=10,
        )
        assert resp.status_code < 300, f"cleanup failed for {user_id}: {resp.text}"


@pytest.fixture
def register(client, unique_email, tracked_users):
    """Register a fresh user and return (email, user dict, access_token)."""

    def _register(name: str = "Test User", password: str = "Sup3rSecure!"):
        email = unique_email()
        resp = client.post(
            "/auth/register",
            json={"name": name, "email": email, "password": password},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        tracked_users.append(body["user"]["id"])
        return email, body["user"], body["access_token"]

    return _register
