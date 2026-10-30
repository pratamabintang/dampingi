"""The test-user email patterns: which addresses the sweep may delete.

Pure functions, no network: this is the guard that decides whether the
purge is allowed to touch a user, so it is tested without Supabase.
"""


def test_is_test_email_patterns():
    from app.admin import is_test_email

    assert is_test_email("pytest.abc123@gmail.com")
    assert is_test_email("probe.xyz@gmail.com")
    assert not is_test_email("realuser@gmail.com")
    assert not is_test_email(None)
    assert not is_test_email("notpytest@gmail.com")  # must start with pattern
