"""Branches the live suite cannot reach, or can only reach expensively.

Every test here runs offline through the real transport, service, and
router — only Supabase's socket is canned (ADR-0006). These supplement
the live suite; they never replace it. The live suite remains the
authority on what Supabase actually does.
"""

import httpx
import pytest

from tests.conftest import (
    captured,
    captured_empty,
    supabase_error,
    supabase_json,
)

# The account the overridden session claims to be (see mock_client).
# Captured from the real project, not invented: the fields are exactly what
# fetch_profile selects.
PROFILE = captured("profiles-select-2026-10-31")

AUTH = {"Authorization": "Bearer offline-token"}


def _profile_route(profile=None):
    return ("GET", "/rest/v1/profiles"), supabase_json(200, [profile or PROFILE])


def test_throttled_verification_is_503_and_changes_nothing(mock_client, upstream):
    """A 429 while verifying the current password must surface as 503.

    It must NOT be reported as a wrong password (Q18), and the password
    must not be updated. Reaching a real 429 on demand would burn the
    suite's rate-limit budget, so it cannot be a live test.
    """
    client, _ = mock_client
    (method, suffix), response = _profile_route()
    rec = upstream(
        {
            (method, suffix): response,
            ("POST", "/auth/v1/token"): supabase_error(
                429, "Request rate limit reached", "over_email_send_rate_limit"
            ),
        }
    )

    resp = client.post(
        "/auth/password",
        headers=AUTH,
        json={"current_password": "current", "new_password": "N3wSecure!pass"},
    )

    assert resp.status_code == 503, resp.text
    # The wrong-password message would be a lie here.
    assert "incorrect" not in resp.text.lower()
    # Prove the canned 429 was what we hit, not a network failure that
    # also happens to be a 503.
    assert "rate limit" in resp.text.lower()
    # Nothing may have been written: no password update was attempted.
    assert not [r for r in rec.requests if r.url.path == "/auth/v1/user"]


def test_unreachable_upstream_is_503_on_login(mock_client, upstream):
    """An unreachable Supabase is 503, not a 401 that blames the caller."""
    client, _ = mock_client

    def unreachable(_request):
        raise httpx.ConnectError("connection refused")

    rec = upstream({("POST", "/auth/v1/token"): unreachable})

    resp = client.post(
        "/auth/login", json={"email": "someone@gmail.com", "password": "Sup3rSecure!"}
    )

    assert resp.status_code == 503, resp.text
    # The transport reported the outage in its own words (it maps httpx
    # failures to "Supabase unreachable"), so this is the canned path and
    # not merely any 503.
    assert "unreachable" in resp.text.lower()
    assert len(rec.requests) == 1


def test_unreachable_upstream_is_503_on_patch_me(mock_client, upstream):
    """Same on PATCH /me: an upstream outage must not read as a bad token."""
    client, _ = mock_client

    def unreachable(_request):
        raise httpx.ConnectError("connection refused")

    rec = upstream({("GET", "/rest/v1/profiles"): unreachable})

    resp = client.patch("/me", headers=AUTH, json={"name": "New Name"})

    assert resp.status_code == 503, resp.text
    assert "unreachable" in resp.text.lower()
    assert len(rec.requests) == 1


def test_patch_me_when_no_row_matched_is_not_a_false_200(mock_client, upstream):
    """A profile PATCH that wrote nothing must not report success.

    The transport raises 404 when PostgREST returns an empty array; the
    router maps that to 503 rather than a 200 with stale data. Live, this
    needs a row to vanish between the read and the write — not reproducible
    on demand.
    """
    client, _ = mock_client
    (method, suffix), response = _profile_route()
    rec = upstream(
        {
            (method, suffix): response,
            ("PATCH", "/rest/v1/profiles"): supabase_json(200, []),
        }
    )

    resp = client.patch("/me", headers=AUTH, json={"name": "New Name"})

    assert resp.status_code == 503, resp.text
    assert resp.status_code != 200
    # It got as far as writing, and the write matched nothing — that is
    # the condition being tested, not an unreachable upstream.
    assert [r for r in rec.requests if r.method == "PATCH"]


def test_partial_soft_delete_retry_completes_it(mock_client, upstream):
    """Soft delete is idempotent across a partial failure (ADR-0003).

    The first attempt revokes sessions then fails at the ban. A retry must
    run the whole sequence again and succeed — the order is load-bearing,
    and a retry after a partial failure must not be refused.
    """
    client, _ = mock_client
    (method, suffix), response = _profile_route()
    ban_attempts = {"n": 0}

    def ban(_request):
        ban_attempts["n"] += 1
        if ban_attempts["n"] == 1:
            return supabase_error(500, "upstream exploded", "unexpected_failure")
        return supabase_json(200, captured("admin-user-ban-2026-10-31"))

    upstream(
        {
            (method, suffix): response,
            ("POST", "/auth/v1/logout"): captured_empty("auth-logout-2026-10-31"),
            ("PUT", f"/auth/v1/admin/users/{PROFILE['user_id']}"): ban,
            # Captured reality: no Prefer header -> 204, empty body.
            ("PATCH", "/rest/v1/profiles"): captured_empty(
                "profiles-patch-tombstone-2026-10-31"
            ),
        }
    )

    first = client.delete("/me", headers=AUTH)
    assert first.status_code == 503, first.text

    second = client.delete("/me", headers=AUTH)
    assert second.status_code == 204, second.text
    assert ban_attempts["n"] == 2, "retry must attempt the ban again"


def test_deleted_profile_read_is_403_not_500(mock_client, upstream):
    """A tombstoned profile is a domain refusal, not a server error."""
    client, _ = mock_client
    (method, suffix), _ = _profile_route()
    upstream(
        {
            (method, suffix): supabase_json(
                200, [{**PROFILE, "deleted_at": "2026-10-30T00:00:00+00:00"}]
            )
        }
    )

    resp = client.get("/me", headers=AUTH)

    assert resp.status_code == 403, resp.text
    assert "deleted" in resp.json()["detail"].lower()


def test_transport_still_builds_real_requests(mock_client, upstream):
    """Guard the seam itself: the transport is not bypassed.

    If the client injection ever stopped taking effect, the canned
    responses would go unused and requests would hit the network — which
    would show up here as an unrouted call rather than passing quietly.
    """
    client, _ = mock_client
    (method, suffix), response = _profile_route()
    rec = upstream({(method, suffix): response})

    assert client.get("/me", headers=AUTH).status_code == 200

    assert len(rec.requests) == 1
    sent = rec.requests[0]
    # The URL and key came from settings, through the real code path.
    assert sent.url.host == "offline.supabase.test"
    assert sent.url.path == "/rest/v1/profiles"
    assert sent.headers["apikey"] == "offline-secret"


def test_register_reads_the_flat_signup_shape(mock_client, upstream):
    """A real signup answers with the FLAT shape, and register must read it.

    `service.register_user` accepts both `{user, session}` and a flat body
    with `access_token` at the top level. This project returns the flat one
    (captured 2026-10-31), so a regression that only reads `session` would
    return `access_token: null` while still answering 201 — a silent break
    no status assertion would catch. The payload is captured, not invented.
    """
    client, _ = mock_client
    signup = captured("auth-signup-2026-10-31")
    user_id = signup["user"]["id"]

    upstream(
        {
            ("POST", "/auth/v1/signup"): supabase_json(200, signup),
            ("GET", "/rest/v1/profiles"): supabase_json(
                200, [{**PROFILE, "user_id": user_id}]
            ),
        }
    )

    resp = client.post(
        "/auth/register",
        json={
            "name": "Capture User",
            "email": "offline@gmail.com",
            "password": "Sup3rSecure!",
            "role": "mother",
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    # The token came from the flat field, through the real service branch.
    assert body["access_token"] == signup["access_token"]
    assert body["user"]["email"] == "offline@gmail.com"
    assert body["user"]["role"] == "mother"
