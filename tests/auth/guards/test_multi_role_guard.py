"""Multi-role gate: an endpoint can accept any of several Roles (ADR-0005).

The gate is a FastAPI dependency, so these tests mount it on a throwaway
app and observe it through HTTP — the same seam every other test uses
(ADR-0006). The profile is canned; nothing here reaches Supabase.
"""

import httpx
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import guards, service
from app.auth.router import verified_session
from tests.conftest import captured, supabase_json

# The row shape the transport reads, captured from the real project. The
# 2026-11-01 capture was taken with `timezone` in the select.
PROFILE = captured("profiles-select-2026-11-01")

AUTH = {"Authorization": "Bearer offline-token"}


def _client_gated_by(gate, session):
    """A one-route app whose only endpoint sits behind `gate`."""
    app = FastAPI()

    @app.get("/gated")
    def gated(caller: service.Session = Depends(gate)):
        return {"user_id": caller.user_id}

    app.dependency_overrides[verified_session] = lambda: session
    return TestClient(app)


def _profile_route(profile):
    return ("GET", "/rest/v1/profiles"), supabase_json(200, [profile])


def test_mother_passes_a_mother_or_companion_gate(mock_client, upstream):
    """A Mother is one of the accepted Roles."""
    _, session = mock_client
    (method, suffix), response = _profile_route({**PROFILE, "role": "mother"})
    upstream({(method, suffix): response})

    client = _client_gated_by(
        guards.require_any_role("mother", "companion"), session
    )

    resp = client.get("/gated", headers=AUTH)

    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == session.user_id


def test_companion_passes_a_mother_or_companion_gate(mock_client, upstream):
    """A Companion is the other accepted Role — the point of the factory."""
    _, session = mock_client
    (method, suffix), response = _profile_route({**PROFILE, "role": "companion"})
    upstream({(method, suffix): response})

    client = _client_gated_by(
        guards.require_any_role("mother", "companion"), session
    )

    resp = client.get("/gated", headers=AUTH)

    assert resp.status_code == 200, resp.text


def test_professional_is_refused_and_the_message_names_the_roles(
    mock_client, upstream
):
    """A Role outside the list is refused, and the caller is told which
    Roles would have been accepted."""
    _, session = mock_client
    (method, suffix), response = _profile_route(
        {**PROFILE, "role": "professional"}
    )
    upstream({(method, suffix): response})

    client = _client_gated_by(
        guards.require_any_role("mother", "companion"), session
    )

    resp = client.get("/gated", headers=AUTH)

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "mother" in detail and "companion" in detail


def test_a_missing_profile_is_refused(mock_client, upstream):
    """No profile row means no Role, so the gate cannot pass."""
    _, session = mock_client
    upstream({("GET", "/rest/v1/profiles"): supabase_json(200, [])})

    client = _client_gated_by(
        guards.require_any_role("mother", "companion"), session
    )

    resp = client.get("/gated", headers=AUTH)

    assert resp.status_code == 403, resp.text


def test_the_single_role_gate_is_unchanged(mock_client, upstream):
    """The existing gate still admits exactly one Role: a Companion is
    refused where only a Mother is allowed."""
    _, session = mock_client
    (method, suffix), response = _profile_route({**PROFILE, "role": "companion"})
    upstream({(method, suffix): response})

    client = _client_gated_by(guards.require_role("mother"), session)

    resp = client.get("/gated", headers=AUTH)

    assert resp.status_code == 403, resp.text


def test_the_timezone_reaches_the_caller(mock_client, upstream):
    """A Profile's timezone is readable through the transport.

    The canned upstream honours `select` the way PostgREST does — it
    returns only the named columns. So this fails if the transport stops
    asking for the timezone, without the test inspecting the request.
    """
    _, session = mock_client

    def postgrest(request):
        asked = request.url.params["select"].split(",")
        return supabase_json(
            200, [{key: PROFILE[key] for key in asked if key in PROFILE}]
        )

    upstream({("GET", "/rest/v1/profiles"): postgrest})

    from app.auth import supabase as auth_supabase

    profile = auth_supabase.fetch_profile(session.user_id)

    assert profile["timezone"] == PROFILE["timezone"]


def test_a_profile_that_predates_the_timezone_column_still_works(
    mock_client, upstream
):
    """A project whose profiles table has no timezone column must not
    break the guard: the gate only needs the role."""
    _, session = mock_client
    old_shape = captured("profiles-select-2026-10-31")

    def postgrest(request):
        asked = request.url.params["select"].split(",")
        return supabase_json(
            200, [{key: old_shape[key] for key in asked if key in old_shape}]
        )

    upstream({("GET", "/rest/v1/profiles"): postgrest})

    client = _client_gated_by(
        guards.require_any_role("mother", "companion"), session
    )

    resp = client.get("/gated", headers=AUTH)

    assert resp.status_code == 200, resp.text
