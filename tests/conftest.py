import importlib
import os
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest

# dampingi keeps its modules in the app/ package at the repo root;
# tests sit next to it, so the root is already importable, but make it
# explicit so the suite works from any cwd.
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

# Set when a live test actually runs. The session sweep below must not
# reach for Supabase on a run that never touched it (ADR-0006: the
# default run is offline).
_live_ran = False


@pytest.fixture(scope="session")
def client(supabase_env):
    """FastAPI TestClient against the real app; Supabase is really
    called, so every test in this suite is an integration test.

    Depends on supabase_env so the skip is decided BEFORE the app is
    imported, and records that a live run happened so the session sweep
    only runs when there is something to sweep."""
    global _dummy_client, _live_ran
    from fastapi.testclient import TestClient

    main = importlib.import_module("app.main")
    _dummy_client = TestClient(main.app)
    _live_ran = True
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
    (the profiles row goes with them via FK cascade).

    Goes through the transport's delete_user, which treats 404 as "already
    gone": the session sweep may have removed the user first, and cleanup
    must not fail on that.
    """
    ids: list = []
    yield ids
    from app.auth import supabase

    for user_id in ids:
        supabase.delete_user(user_id)


def pytest_sessionfinish(session, exitstatus):
    """Post-run sweep: delete leaked test users.

    Per-test teardown (tracked_users) deletes the users a test knows it
    created. It misses users created just before a hard failure (the
    register-before-track window) or a keyboard interrupt. This sweep
    runs after every session and clears anything left matching the test
    patterns, so a failed run never poisons the next one.

    Silently skipped when no live test ran (the default run is offline and
    must not reach for Supabase), when credentials aren't configured, or
    when run with -p no:terminalreporter.
    """
    if not _live_ran:
        return
    missing = _missing_keys()
    if missing:
        return
    try:
        from app import admin
        from app.auth import supabase
    except Exception:
        return
    try:
        leaked = admin.find_leaked_users()
        if leaked:
            admin.delete_users(leaked, log=lambda _msg: None)
    except supabase.SupabaseError:
        # Sweep must never turn a green run red; admin.py purge is the
        # manual remedy when this happens.
        pass


@pytest.fixture
def register(client, unique_email, tracked_users):
    """Register a fresh user and return (email, user dict, access_token)."""

    def _register(
        name: str = "Test User",
        password: str = "Sup3rSecure!",
        role: str = "mother",
    ):
        email = unique_email()
        resp = client.post(
            "/auth/register",
            json={"name": name, "email": email, "password": password, "role": role},
        )
        # A 429 mid-run is Supabase's per-IP throttle, not a code defect.
        # Fail with the remedy in the message instead of a bare assert.
        if resp.status_code == 400 and "rate limit" in resp.text.lower():
            pytest.fail(
                "Supabase rate limit hit (per-IP Auth throttle). The suite "
                "makes ~110 auth calls per run, so back-to-back runs trip it. "
                "Wait ~5 minutes and rerun (see docs/supabase-setup.md, "
                "Troubleshooting)."
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        tracked_users.append(body["user"]["id"])
        return email, body["user"], body["access_token"]

    return _register


# --- Offline seam (ADR-0006) -------------------------------------------------
#
# The seam is the transport's httpx client: a test hands in a client backed
# by httpx.MockTransport, so app/supabase.py runs its real URL building,
# header assembly, and error mapping — only the socket is faked.
#
# Fidelity rules (ADR-0006):
# - Error bodies are hand-written; their shape is status + error_code + msg,
#   already bounded by the code's branches. The vocabulary is capped at the
#   seven cases the code distinguishes — adding an eighth means the code grew
#   a branch, not that these helpers need to get cleverer.
# - Success payloads must be *captured* from the real project, not invented;
#   inventing shape is how the transport bugs were missed.


def supabase_error(status: int, message: str, error_code: str):
    """A Supabase error response, in the shape the API actually uses."""
    import httpx

    return httpx.Response(
        status, json={"code": status, "error_code": error_code, "msg": message}
    )


def supabase_json(status: int, body):
    """A Supabase success response."""
    import httpx

    return httpx.Response(status, json=body)


FIXTURES_DIR = TESTS_DIR / "fixtures"


def captured(name: str):
    """A success payload captured from the real project (ADR-0006 decision 7).

    Success *shape* is never hand-written: it is read from
    `tests/fixtures/<name>.json`, which records the endpoint and capture date.
    A stale fixture fails in the optimistic direction, which is why the live
    suite stays the authority.
    """
    import json

    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no captured fixture {path.name}; see tests/fixtures/README.md"
        )
    return json.loads(path.read_text(encoding="utf-8"))["payload"]


def captured_empty(name: str):
    """A captured success response that carries no body (204).

    Logout and the tombstone PATCH both answer 204 with an empty body; the
    fixture records the status, so the test cannot quietly assume a 200.
    """
    import httpx

    payload = captured(name)
    return httpx.Response(payload["status"], content=b"")


class _Recorded:
    """Stand-in upstream: answers by method + path, records what arrived."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list = []

    def __call__(self, request):
        import httpx

        self.requests.append(request)
        for (method, suffix), handler in self.routes.items():
            if request.method == method and request.url.path.endswith(suffix):
                return handler(request) if callable(handler) else handler
        # An unrouted call is a broken test, not a 404 to assert on.
        return httpx.Response(
            599,
            json={
                "msg": f"no canned response for {request.method} {request.url.path}",
                "error_code": "unrouted_in_test",
            },
        )


@pytest.fixture
def upstream():
    """Route the transport through an offline, canned Supabase.

    Usage: `rec = upstream({("POST", "/auth/v1/token"): supabase_error(...)})`,
    then call the app. `rec.requests` holds what the transport sent.
    """
    import httpx

    from app import supabase

    created = []

    def _use(routes: dict) -> _Recorded:
        recorded = _Recorded(routes)
        client = httpx.Client(transport=httpx.MockTransport(recorded))
        created.append(client)
        supabase.use_client(client)
        return recorded

    yield _use

    supabase.use_client(None)
    for client in created:
        client.close()


@pytest.fixture
def offline_env(monkeypatch):
    """Credentials for a mocked run: nothing real is contacted, but the
    transport still assembles headers from settings."""
    from app import config

    monkeypatch.setenv("SUPABASE_URL", "https://offline.supabase.test")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "offline-publishable")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "offline-secret")
    config.reset_settings()
    yield
    config.reset_settings()


@pytest.fixture
def real_gate(mock_client):
    """Run journals tests through the real Mother-or-Companion gate.

    `mock_client` overrides the gate so most tests can ignore authorization.
    Tests *about* authorization must not, or they would be asserting against
    a permissive stub. Shared here because every journals feature needs it:
    a per-feature copy is a copy that drifts.
    """
    import app.main as main
    from app.journals import router as journals_router

    saved = main.app.dependency_overrides.pop(journals_router.mother_or_companion, None)
    yield
    if saved is not None:
        main.app.dependency_overrides[journals_router.mother_or_companion] = saved


@pytest.fixture
def as_author():
    """Point the app's session at a Profile, through the real gate.

    The gate is *not* overridden here: `require_any_role` re-reads the
    session, so swapping `verified_session` is enough and the real guard
    still runs. Used with `real_gate` to test two Authors in one test.
    """

    def _as(user_id: str) -> None:
        import app.main as main
        from app.auth import service
        from app.auth.router import verified_session

        main.app.dependency_overrides[verified_session] = lambda: service.Session(
            user_id=user_id, email="offline@gmail.com", token="offline-token"
        )

    return _as


@pytest.fixture
def mock_client(offline_env):
    """A TestClient that needs no credentials and no network.

    JWT verification is the one thing that genuinely needs Supabase (the
    JWKS endpoint), so the session dependency is overridden with a fixed
    Session. Everything below it — router, service, transport — is real,
    which is the point: that is the layer the live suite caught bugs in.
    """
    from fastapi.testclient import TestClient

    import app.main as main
    from app.auth import service
    from app.auth.router import verified_session
    from app.journals import router as journals_router
    from app.moods import router as moods_router

    session = service.Session(
        user_id="00000000-0000-4000-8000-000000000001",
        email="offline@gmail.com",
        token="offline-token",
    )
    main.app.dependency_overrides[verified_session] = lambda: session
    main.app.dependency_overrides[moods_router.mother_only] = lambda: session
    main.app.dependency_overrides[journals_router.mother_or_companion] = (
        lambda: session
    )
    yield TestClient(main.app), session
    main.app.dependency_overrides.clear()
