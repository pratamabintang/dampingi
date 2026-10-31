"""Thin transport helpers for the Supabase REST API.

We deliberately call the stable `/auth/v1/*` and `/rest/v1/*` endpoints
directly instead of using the `supabase-py` SDK: fewer dependencies, and
the REST contract is platform-guaranteed.

This module provides core infrastructure. Domain-specific functions live in
their respective supabase modules (e.g., app.auth.supabase, app.moods.supabase).
"""

from typing import Optional

import httpx

from app.config import get_settings

TIMEOUT = 10.0

# Soft delete (ADR-0003): 100 years, the documented Supabase ban example.
BAN_DURATION = "876000h"


# --- The seam (ADR-0006) -----------------------------------------------------
#
# Every call goes through one client, so a test can hand in an
# httpx.Client backed by httpx.MockTransport and exercise this module's
# real URL building, header assembly, and error mapping against canned
# responses. Tests replace it with `use_client()`; the default talks to
# the network.

_client_override: Optional[httpx.Client] = None
_default_client: Optional[httpx.Client] = None


def _client() -> httpx.Client:
    """The httpx client every request uses (real unless overridden)."""
    global _default_client
    if _client_override is not None:
        return _client_override
    if _default_client is None:
        _default_client = httpx.Client(timeout=TIMEOUT)
    return _default_client


def use_client(client: Optional[httpx.Client]) -> None:
    """Route requests through `client` (None restores the real network)."""
    global _client_override
    _client_override = client


def _base_url() -> str:
    """Supabase project URL, without a trailing slash."""
    return get_settings().supabase_url.rstrip("/")


def _publishable_key() -> str:
    """The publishable (public) key; safe to expose, used for auth flows."""
    return get_settings().supabase_publishable_key


class SupabaseError(Exception):
    """Raised when Supabase does not answer 2xx."""

    def __init__(self, status: int, message: str, error_code: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.error_code = error_code

    @property
    def is_transport(self) -> bool:
        """True when we never got a usable answer (unreachable, timeout).

        Lets the logic layer distinguish "upstream is down" from "upstream
        said no" without doing HTTP status arithmetic (ADR-0004).
        """
        return self.status == 503 and self.error_code is None


def _error_from_response(resp: httpx.Response) -> SupabaseError:
    error_code = None
    message = resp.text
    try:
        body = resp.json()
        error_code = body.get("error_code") or body.get("code")
        message = body.get("msg") or body.get("message") or resp.text
    except ValueError:
        pass
    return SupabaseError(resp.status_code, message, error_code)


def _request(func, *args, **kwargs) -> httpx.Response:
    """Call httpx, mapping transport failures to SupabaseError(503)."""
    try:
        return func(*args, **kwargs)
    except httpx.HTTPError as exc:
        raise SupabaseError(503, f"Supabase unreachable: {exc}") from exc


def _utc_now_iso() -> str:
    """UTC timestamp string, Postgres-timestamptz-parseable."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
