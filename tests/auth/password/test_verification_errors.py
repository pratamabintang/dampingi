"""POST /auth/password: change the credential, verify the current one first."""

from app.auth import supabase as sb
from app.auth import service

import pytest


def test_throttled_verification_is_not_a_wrong_password(monkeypatch):
    """A 429 from Supabase must not be reported as a wrong password
    (Q18): the change must not proceed, and the caller must not be told
    their password is wrong when the truth is "try again later".

    Exercises service.change_password with the upstream call stubbed —
    the real 429 path would burn the suite's rate-limit budget.
    """
    from app.auth import supabase as sb
    from app.auth import service

    session = service.Session(user_id="u", email="a@gmail.com", token="t")
    monkeypatch.setattr(service, "active_profile", lambda _uid: {"role": "mother"})

    def boom(_email, _password):
        raise sb.SupabaseError(429, "Request rate limit reached")

    monkeypatch.setattr(sb, "login", boom)

    with pytest.raises(sb.SupabaseError) as caught:
        service.change_password(session, "current", "N3wSecure!pass")
    assert caught.value.status == 429

    # ...whereas a genuine rejection is the domain error, not a transport one.
    def wrong(_email, _password):
        raise sb.SupabaseError(400, "Invalid login credentials", "invalid_credentials")

    monkeypatch.setattr(sb, "login", wrong)
    with pytest.raises(service.CurrentPasswordIncorrect):
        service.change_password(session, "current", "N3wSecure!pass")
