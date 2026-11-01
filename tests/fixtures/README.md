# Test fixtures: canned Supabase responses

Used by the offline tests (ADR-0006). The seam is the transport's `httpx`
client: a test hands in a client backed by `httpx.MockTransport`, so
`app/supabase.py` runs its **real** URL building, header assembly, and error
mapping. Only the socket is faked.

## Two kinds of canned response, two rules

**Error bodies — hand-written.** Their shape is just status + `error_code` +
`msg`, and the code's branches already bound the vocabulary. There is nothing
to invent, so `supabase_error(...)` builds them inline.

The vocabulary is capped at the seven cases the code distinguishes:

| `error_code` / condition | Where the code reacts |
|---|---|
| `user_banned` | login, logout, password change → 403 |
| `user_already_exists`, `email_exists` | register → 409 |
| `session_not_found` | logout → treated as already logged out |
| `invalid_credentials` | password verification → 400 |
| 429 (any code) | password verification → 503, never "wrong password" |
| 503 with no `error_code` | transport failure (`is_transport`) → 503 |
| 404 empty array from PostgREST | `update_profile_name` → 503, never a false 200 |

Adding an eighth means the code grew a branch — not that these helpers should
get cleverer.

**Success payloads — captured, never invented.** Inventing response *shape* is
exactly how four real bugs were missed (`PUT /auth/v1/user` returning no access
token; `scope=global` killing the caller's own session; Supabase's docs claiming
"204 with no sessions"; a 429 surfacing as a wrong password). A hand-written
success body encodes what we *believe* Supabase returns.

## Captured fixtures

| File | Endpoint | What it pins |
|---|---|---|
| `profiles-select-2026-11-01.json` | `GET /rest/v1/profiles` | The row shape `fetch_profile` reads, **with `timezone`** (added for Journals, ADR-0007); PostgREST returns an array |
| `journals-select-2026-11-01.json` | `GET /rest/v1/journals` | The row shape `list_journals` reads: an array, newest first, `created_at` as a `YYYY-MM-DD` string. Captured for ticket 04 so the list fake builds rows from a real body instead of inventing one |
| `profiles-select-2026-10-31.json` | `GET /rest/v1/profiles` | The older select, **without `timezone`**. Kept deliberately: a project whose `profiles` predates that column must still pass the role gate |
| `profiles-patch-representation-2026-10-31.json` | `PATCH` with `Prefer: return=representation` | `update_profile_name`: 200 + one-element array |
| `profiles-patch-tombstone-2026-10-31.json` | `PATCH` without `Prefer` | `tombstone_profile`: **204, empty body** |
| `admin-user-ban-2026-10-31.json` | `PUT /auth/v1/admin/users/<id>` | Ban returns the **full auth user**, not a stub |
| `auth-logout-2026-10-31.json` | `POST /auth/v1/logout` | 204, empty body |
| `auth-signup-2026-10-31.json` | `POST /auth/v1/signup` | **Flat** shape: `access_token` at top level, no `session` key |

Each file is `{"_fixture": {endpoint, captured, note}, "payload": …}`. Load it
with `captured("name")` from `tests/conftest.py`; use `captured_empty("name")`
for the 204 cases so a test cannot quietly assume a 200 body.

## Known capture gaps

**Journals PATCH / DELETE success shapes are not captured yet.** Tickets 05
and 06 added `PATCH` and `DELETE /rest/v1/journals` with
`Prefer: return=representation`, and the offline fake encodes the *status*
those return (200 + array; 204 + empty body for a no-match delete) without a
captured body behind it. The live suites assert the behaviour
(`test_edit_journal_live.py`, `test_delete_journal_live.py`), so the
assumption is checked against the real project rather than trusted — but no
file records *when* the shape was last seen. Capture one when the local stack
is next up, per the procedure above. Do not hand-write it: an invented
success body is the failure mode this whole section exists to prevent.

When the `select` list changes, capture a **new** dated file — never edit an
existing one. The date is the only signal that a fixture may predate an API
change, and an edited file lies about when it was taken.

To capture one:

1. Hit the real endpoint once (a live test, or a scratch script).
2. Save the body as `tests/fixtures/<endpoint>-<YYYY-MM-DD>.json`.
3. **Scrub it**: replace access/refresh tokens, emails, and user ids with
   obvious placeholders. Never commit a real token. Scrub by key, not by
   length — `authenticated` is a legitimate value for `aud`/`role` and must
   survive (an over-eager regex once rewrote both).
4. Note the endpoint and date at the top of the file.

The date in the filename is the only signal that a fixture may predate a
Supabase API change. A stale fixture fails in the optimistic direction — the
mocked test keeps passing while the real API moved. The live suite is the
authority; that is why it is never deleted or ported to a mock.

## A capture is only worth what it catches

Fidelity alone is not value. When a fixture replaces a hand-written body, check
the test still **fails** when the code it covers is broken (mutate the branch,
confirm red, restore). The tombstone `200 → 204` correction was real fidelity
that changed no outcome — `tombstone_profile` only reads `status_code < 300`.
The signup fixture earned its place: it is the only test that fails if
`register_user` stops reading the flat `access_token` field.
