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


# --- Shared journals fixtures (write, edit, delete) ---------------------------
#
# The row builders and the two Author ids are identical across the journals
# suites, so they live here. A per-module copy is a copy that drifts, and a
# drifted copy is a test asserting a shape the API does not produce.

def journals_profile(**overrides):
    """The captured Profile, defaulted to the Mother role.

    Captured from the real project (ADR-0006): fields are exactly what
    `fetch_profile` selects, `timezone` included (ADR-0007).
    """
    return {**captured("profiles-select-2026-11-01"), "role": "mother", **overrides}


# Another real Profile, so two-Author tests really are two Authors.
OTHER_AUTHOR = "00000000-0000-4000-8000-0000000000f1"


def journal_row(user_id, day, title="A day", content="As written."):
    """A Journals row in the shape the real API returns, with fields replaced.

    Built from the captured select body rather than hand-written, so the fake
    cannot drift into a shape the API does not actually produce (ADR-0006).
    """
    return {
        **captured("journals-select-2026-11-01")[0],
        "user_id": user_id,
        "created_at": day,
        "title": title,
        "content": content,
    }


# --- One canned PostgREST table, shared by both domains ----------------------
#
# Journals and Moods each need a fake that actually *holds rows*: applies the
# `user_id` filter, honours `select`, enforces the composite key, and mutates.
# Two copies of that is two chances to drift, and the drift is silent — the
# first Moods copy already ignored `gte.`, so every date-ranged list answered
# with an empty array and would have agreed with a transport that had stopped
# filtering at all (ADR-0006: a fake that cannot fail makes its tests
# unfalsifiable). So the behaviour lives here once; the fixtures below only
# name their table and row shape.


def _canned_table(upstream, *, table, profile, rows, row_for=None, max_rows=1000):
    """A canned Supabase holding `rows` for one PostgREST table.

    Modelled deliberately, each part for a reason:

    - `select` is honoured, so a transport asking for a column that is not
      there fails loudly instead of returning an invented default.
    - `user_id` is a filter, never a suggestion: a transport that stopped
      scoping by user would return a stranger's row here.
    - `created_at` understands `eq.`, `gte.` and `lte.`, because the transports
      send all three (`list_moods` filters with `gte.` and `lte.`). Repeats
      are *conjunctive*: PostgREST applies every `created_at` condition it is
      given, which is how a closed range is expressed.
    - `order` and `offset`/`limit` are applied, so paging can be exercised.
    - `max_rows` stands in for PGRST_DB_MAX_ROWS (`supabase/config.toml` has
      1000): a response is capped *whatever* `limit` asks for, and the cap is
      silent. Without it a fake would hand back the whole table and agree with
      a transport that had stopped paging — the truncation bug could not fail.
    - The composite key `(user_id, created_at)` is enforced because the tables
      enforce it. A fake that appended blindly would answer 201 to a second row
      for the same day, so a duplicate test written against it could never fail.
    - A duplicate answers 409 with the SQLSTATE in `code`, which is the shape
      PostgREST really returns.
    - PATCH and DELETE answer with the rows they matched, as PostgREST does
      under `Prefer: return=representation`; DELETE without that header answers
      204 with no body, as PostgREST really does.
    - `row_for` lets a table with server-side defaults fill them in. Journals
      stores the body as sent; Moods normalises `description` to None.

    `rows` is the whole table, every user's rows; mutations persist for the
    life of the test, so "write, read back, edit" is one table rather than
    three independent stubs.
    """

    import json

    import httpx

    stored = list(rows)
    fill = row_for or (lambda body: body)

    def _day_matches(row, wanted: str) -> bool:
        if wanted.startswith("gte."):
            return row["created_at"] >= wanted.removeprefix("gte.")
        if wanted.startswith("lte."):
            return row["created_at"] <= wanted.removeprefix("lte.")
        return row["created_at"] == wanted.removeprefix("eq.")

    def _match(request):
        wanted_user = request.url.params.get("user_id")
        # A closed range sends created_at twice (gte. and lte.). Every
        # condition applies, so this is a conjunction, not "the first one".
        wanted_days = request.url.params.get_list("created_at")
        return [
            row
            for row in stored
            if (
                wanted_user is None
                or row["user_id"] == wanted_user.removeprefix("eq.")
            )
            and all(_day_matches(row, wanted) for wanted in wanted_days)
        ]

    def _select(request, matched):
        asked = request.url.params["select"].split(",")
        return [{k: row[k] for k in asked if k in row} for row in matched]

    def profile_get(request):
        asked = request.url.params["select"].split(",")
        return supabase_json(200, [{k: profile[k] for k in asked if k in profile}])

    def table_get(request):
        hits = _match(request)
        order = request.url.params.get("order")
        if order == "created_at.desc":
            hits = sorted(hits, key=lambda r: r["created_at"], reverse=True)
        elif order == "created_at.asc":
            hits = sorted(hits, key=lambda r: r["created_at"])
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", len(hits) or 1))
        # The server's cap wins over whatever the caller asked for, and it
        # does so silently — no header, no short-page signal.
        return supabase_json(
            200, _select(request, hits[offset : offset + min(limit, max_rows)])
        )

    def table_post(request):
        body = json.loads(request.content)
        clash = [
            row
            for row in stored
            if row["user_id"] == body["user_id"]
            and row["created_at"] == body["created_at"]
        ]
        if clash:
            return httpx.Response(
                409,
                json={
                    "code": "23505",
                    "details": "Key (user_id, created_at) already exists.",
                    "message": "duplicate key value violates unique constraint",
                },
            )
        row = fill(body)
        stored.append(row)
        return supabase_json(201, [row])

    def table_patch(request):
        hits = _match(request)
        body = json.loads(request.content)
        for row in hits:
            row.update(body)
        return supabase_json(200, _select(request, hits))

    def table_delete(request):
        hits = _match(request)
        for row in hits:
            stored.remove(row)
        if "return=representation" in (request.headers.get("prefer") or ""):
            return supabase_json(200, _select(request, hits))
        return httpx.Response(204, content=b"")

    return upstream(
        {
            ("GET", "/rest/v1/profiles"): profile_get,
            ("GET", f"/rest/v1/{table}"): table_get,
            ("POST", f"/rest/v1/{table}"): table_post,
            ("PATCH", f"/rest/v1/{table}"): table_patch,
            ("DELETE", f"/rest/v1/{table}"): table_delete,
        }
    )


@pytest.fixture
def journals_table(upstream):
    """A canned Supabase holding a real Journals table, behaving like PostgREST.

    Used by the edit and delete suites. (The write suite predates it and still
    carries its own `_postgrest`; consolidating that is a separate change, not
    something to smuggle into this one.)

    `journals` is the whole table, every Author's rows. See `_canned_table` for
    what the fake models and why.

    The date is applied verbatim from the PATCH body rather than protected
    here: PostgREST would move it too, so a transport that wrongly forwards
    `created_at` must fail the date-immutability test rather than be quietly
    corrected by the fake.
    """

    def _use(profile, journals=None):
        return _canned_table(
            upstream, table="journals", profile=profile, rows=journals or []
        )

    return _use


# --- Shared moods fixture ----------------------------------------------------
#
# Moods predates the captured-fixture discipline and has no captured success
# payload of its own (see tests/fixtures/README.md, "Known capture gaps"). The
# insert body is therefore echoed from the request rather than read from a
# file: for `Prefer: return=representation` PostgREST returns the row that was
# inserted, and the moods table has no server-side defaults, so the echo is the
# faithful answer. It is a gap in *capture*, not licence to invent a shape —
# the live suite stays the authority on what the real table returns.


def mood_row(user_id, day, mood=3, description=None):
    """A moods row as this fake stores it: the columns the transport sends."""
    return {
        "user_id": user_id,
        "created_at": day,
        "mood": mood,
        "description": description,
    }


@pytest.fixture
def moods_table(upstream):
    """A canned Supabase holding a real moods table, behaving like PostgREST.

    `moods` is the whole table, every user's rows; mutations persist for the
    life of the test. See `_canned_table` for what the fake models and why.

    The one moods-specific detail is the row shape: `public.moods` has no
    server-side defaults, but a POST may omit `description` entirely, so the
    stored row is normalised to the four columns the table actually has.
    """

    def _use(profile, moods=None, max_rows=1000):
        return _canned_table(
            upstream,
            table="moods",
            profile=profile,
            rows=moods or [],
            max_rows=max_rows,
            row_for=lambda body: {
                **mood_row(body["user_id"], body["created_at"]),
                **body,
            },
        )

    return _use


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
