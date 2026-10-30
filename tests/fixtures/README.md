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

To capture one:

1. Hit the real endpoint once (a live test, or a scratch script).
2. Save the body as `tests/fixtures/<endpoint>-<YYYY-MM-DD>.json`.
3. **Scrub it**: replace access/refresh tokens, emails, and user ids with
   obvious placeholders. Never commit a real token.
4. Note the endpoint and date at the top of the file.

The date in the filename is the only signal that a fixture may predate a
Supabase API change. A stale fixture fails in the optimistic direction — the
mocked test keeps passing while the real API moved. The live suite is the
authority; that is why it is never deleted or ported to a mock.
