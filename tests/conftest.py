"""Canned Supabase responses and the offline client (ADR-0006).

The seam is the transport's httpx client: a test hands in a client backed
by httpx.MockTransport, so `app/supabase.py` runs its real URL building,
header assembly, and error mapping — only the socket is faked.

Fidelity rules (ADR-0006):
- Error bodies are hand-written; their shape is just status + error_code
  + msg, already bounded by the code's branches. The vocabulary is capped
  at the seven cases the code actually distinguishes — adding an eighth
  means the code grew a branch, not that these helpers need to get
  cleverer.
- Success payloads must be *captured* from the real project, not invented;
  inventing shape is how the transport bugs were missed. See
  tests/fixtures/README.md before adding one.
"""

import httpx
import pytest


def supabase_error(status: int, message: str, error_code: str) -> httpx.Response:
    """A Supabase error response, in the shape the API actually uses."""
    return httpx.Response(
        status,
        json={"code": status, "error_code": error_code, "msg": message},
    )


def supabase_json(status: int, body) -> httpx.Response:
    """A Supabase success response."""
    return httpx.Response(status, json=body)


class Recorded:
    """A stand-in upstream that answers by method+path and records calls."""

    def __init__(self, routes: dict):
        # routes: {(METHOD, path-suffix): response-or-callable}
        self.routes = routes
        self.requests: list = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for (method, suffix), handler in self.routes.items():
            if request.method == method and request.url.path.endswith(suffix):
                return handler(request) if callable(handler) else handler
        return httpx.Response(
            599,
            json={
                "msg": f"no canned response for {request.method} {request.url.path}",
                "error_code": "unrouted_in_test",
            },
        )


@pytest.fixture
def upstream():
    """Build an offline Supabase and route the transport through it.

    Usage: `upstream({("POST", "/auth/v1/token"): response})`, then call
    the app; `recorded.requests` holds what the transport sent.
    """
    from app import supabase

    created = []

    def _use(routes: dict) -> Recorded:
        recorded = Recorded(routes)
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
