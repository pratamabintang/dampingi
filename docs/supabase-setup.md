# Supabase setup for dampingi auth

Step-by-step: create the project, keys, and database objects the app relies on.
Total time: ~5 minutes. Nothing here needs a frontend.

## 1. Create the project

1. Go to [supabase.com](https://supabase.com/) → **New project**. A free-tier project is enough.
2. Pick a database password (store it somewhere safe) and a region close to your Render deployment.
3. Wait for provisioning.

For the rest of this guide, `<project-url>` means `https://<project-ref>.supabase.co`.

## 2. Grab the API keys

Dashboard → **Project Settings → API Keys**. You need three values:

| What | Where it's used |
|---|---|
| Project URL | `SUPABASE_URL` |
| **Publishable** key (`sb_publishable_...`, or the legacy **anon** key) | `SUPABASE_PUBLISHABLE_KEY` — sent on register/login flows |
| **Secret** key (`sb_secret_...`, or the legacy **service_role** key) | `SUPABASE_SECRET_KEY` — server-side profile reads + test cleanup. **Never put this in client code or a repo.** |

Both legacy (`anon`/`service_role`) and new (`sb_publishable_`/`sb_secret_`) keys work with this app.

## 3. Disable email confirmation

Dashboard → **Authentication → Sign In / Providers → Email → turn "Confirm email" OFF**.

Without this, a fresh registration returns no `access_token` until the user clicks a confirmation email, and login before confirmation fails — which would make the automated live tests meaningless.

## 4. Create the `profiles` table + trigger

The signup flow sends the user's name as signup metadata; the trigger below copies it into a `profiles` row at auth-user creation (ADR-0002, in the parent repo's `docs/adr/`). Open **SQL Editor** and run:

```sql
create table public.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  name text not null,
  role text not null
    check (role in ('mother', 'companion', 'professional')),
  deleted_at timestamptz,
  created_at timestamptz not null default now()
);

-- Soft-delete tombstone (ADR-0003): NULL = active; set only by the app
-- (never by the DB). "Deleting" a user = Supabase ban + this timestamp.

-- role is authorization data (ADR-0005): exactly one per Profile, fixed at
-- registration, lowercase. text + CHECK, not a Postgres ENUM (an ENUM value
-- cannot be dropped or renamed without a type rebuild).

-- No policies on purpose: only the service_role/secret key
-- (used by the app's server-side profile reads, and by tests)
-- may touch this table.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
-- NOT "stable": PostgreSQL forbids INSERT in non-volatile functions,
-- and a stable trigger function makes every signup fail with
-- "Database error saving new user"
as $$
begin
  insert into public.profiles (user_id, name, role)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'name', ''),
    -- no fallback: a signup without a role must fail, not be
    -- silently mislabelled (ADR-0005)
    new.raw_user_meta_data ->> 'role'
  );
  return new;
end;
$$;

create trigger on_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
```

- `on delete cascade`: deleting an auth user (tests do this in teardown) removes the profile too.
- **Existing projects** (created before soft delete): add the tombstone column with
  `alter table public.profiles add column if not exists deleted_at timestamptz;`
- **Existing projects** (created before roles): run `docs/migrations/0001-profiles-role.sql`
  in the SQL editor — it adds `role`, drops the temporary default, and replaces
  the trigger.
- If you run this after creating some users, backfill them with:
  `insert into public.profiles (user_id, name, role) select id, coalesce(raw_user_meta_data ->> 'name', ''), coalesce(raw_user_meta_data ->> 'role', 'mother') from auth.users on conflict do nothing;`
  (the `coalesce` to `'mother'` is only for this one-off backfill of
  pre-existing rows — the live trigger has no fallback, by design)

## 5. Check JWT signing keys

Dashboard → **Project Settings → JWT Keys**. The app verifies tokens locally against the JWKS discovery endpoint (`<project-url>/auth/v1/.well-known/jwks.json`), so:

- ✅ If there is an asymmetric **current** key (ES256 default on new projects) — nothing to do.
- ⚠️ If the project is still on the legacy JWT secret only, click **Migrate JWT secret** and then **Rotate keys** (zero downtime; see Supabase's signing-keys guide). Legacy-HS256 tokens cannot be verified from the JWKS endpoint.

Quick check that discovery works:

```powershell
Invoke-RestMethod "https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json"
```

## 6. Configure the environments

**Local (app):** copy `.env.example` → `.env` (in `dampingi/`) and fill in the values.

**Local (tests):** copy `dampingi/tests/.env.example` → `dampingi/tests/.env` with the same values. Without it, live tests *skip* (CI without a project stays green). From the `dampingi/` directory:

```powershell
pytest            # offline: mocked tests only, no credentials or network needed
pytest -m live    # the live suite: hits the real project, cleans up its users
```

`pytest` on its own is offline by design (ADR-0006): live tests carry a `live`
marker and are deselected. Note the live suite makes ~110 auth calls per run and
Supabase throttles per IP — wait ~5 minutes between consecutive live runs.

**Render:** add the three env vars to the web service (Dashboard → Environment) — same names as the `.env` keys. `render.yaml` needs no start-command changes.

## 7. Smoke test (curl/PowerShell)

```powershell
# Register (name + Gmail + password)
Invoke-RestMethod -Method Post "$url/auth/v1/signup" ...   # not needed directly; use the app:

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/auth/register" `
  -ContentType "application/json" `
  -Body '{"name":"Ada","email":"somebody@gmail.com","password":"Sup3rSecure!","role":"mother"}'
# => 201 { user: { id, email, name: "Ada", role: "mother" }, access_token: "..." }
# role is required and one of: mother | companion | professional (lowercase)

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/auth/login" `
  -ContentType "application/json" `
  -Body '{"email":"somebody@gmail.com","password":"Sup3rSecure!"}'

$t = "<access_token from above>"
Invoke-RestMethod -Uri "http://localhost:8000/me" -Headers @{ Authorization = "Bearer $t" }
# => { id, email, name }

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/auth/logout" -Headers @{ Authorization = "Bearer $t" }
# => 204: every session revoked. NOTE: $t itself keeps working until it
#    expires (~1h) — Supabase has no access-token revocation; logout kills
#    refresh tokens, so no NEW tokens can be minted from any session.

Invoke-RestMethod -Method Patch -Uri "http://localhost:8000/me" `
  -ContentType "application/json" -Headers @{ Authorization = "Bearer $t" } `
  -Body '{"name":"Ada L."}'
# => 200 { id, email, name: "Ada L.", role: "mother" }
# Only `name` is accepted; sending `role` is 422 (it is immutable).

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/auth/password" `
  -ContentType "application/json" -Headers @{ Authorization = "Bearer $t" } `
  -Body '{"current_password":"Sup3rSecure!","new_password":"N3wSecure!pass"}'
# => 204: password changed and every session revoked — log in again.
```

## 8. Test-user hygiene (admin CLI)

Test users are cleaned up in three layers:

1. **Per-test teardown** — the `tracked_users` fixture deletes each user a test created.
2. **Session sweep** — after every `pytest` run, `pytest_sessionfinish` deletes any user matching the test patterns (`pytest.*`, `probe.*` local parts), catching users that leaked when a run crashed between registration and teardown.
3. **Manual CLI** — `python -m app.admin`:

```powershell
python -m app.admin status    # totals + list of leaked test users
python -m app.admin purge      # delete leaked test users (patterns only)
python -m app.admin purge --all  # delete EVERY user (type count to confirm)
```

`purge` without flags only ever touches `pytest.*`/`probe.*` emails, so a teammate's manual test account is never deleted by accident. `--all` is unguarded by pattern but requires typing the user count.

A run that ends nonzero can still have swept successfully — the sweep never turns a red run green-to-red. If a sweep itself fails (network), `python -m app.admin purge` is the manual remedy.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Register fails with "Database error saving new user" | The `handle_new_user` trigger function is not `volatile` (PostgreSQL forbids INSERT in stable functions). Fix: `alter function public.handle_new_user() volatile;` |
| Login returns 401 for a just-registered user | Email confirmation wasn't disabled (step 3), or the user made a typo'd password |
| `name` is `null` in responses | The trigger/table from step 4 was never run (fetching the profile returns no row) |
| Register fails with a password policy error | Supabase enforces a minimum length (default 8); the app pre-validates to match |
| `/me` always 401 with "no trusted signing key matched" | Project still uses the legacy JWT secret (step 5) |
| 409 on register of a brand-new email | That Gmail already registered in this project (a previous test run that crashed before cleanup, for example) — delete it from **Authentication → Users** |
| Tests fail with `{"detail":"Request rate limit reached"}` (429) but pass when rerun after a pause | Supabase throttles the Auth API per IP. The suite now makes **~110 auth-API calls per run** (register, login, role checks, name updates, password changes each with a verification login + revokes), so **back-to-back runs always trip it**. Wait ~5 minutes between full runs; a red run still sweeps its users. Single-file or single-test runs are fine within the window |
| A signup fails with "Database error saving new user" and the user was created by hand in the dashboard | The trigger writes `role` with no fallback (ADR-0005), so a user without `role` in its metadata fails the insert rather than being silently mislabelled. Set `role` (mother / companion / professional) in the user's metadata when creating it by hand |
| `role` is `null` in a response | The `profiles` table has no `role` column yet — run `docs/migrations/0001-profiles-role.sql` (step 4) |
| A user's `role` disagrees between `/me` and their token's `user_metadata` | Working as designed. `profiles.role` is the only source of truth; `user_metadata` is user-writable with the publishable key and must never be authorized against (ADR-0005) |
| A logged-out user's token still works for `GET /me` | Working as designed: logout revokes refresh tokens only. Supabase has no access-token revocation, so a live JWT verifies until it expires (default ~1h). Use `DELETE /me` for immediate lockout (soft delete bans + tombstones) |
| Login returns 403 "This account has been deleted" | Working as designed: the account was soft-deleted (`DELETE /me` bans the auth identity + tombstones the profile; ADR-0003). The data still exists — this is not a bug to fix |
| `DELETE /me` returns 503 | A Supabase write failed mid-delete (the order is deliberate: revoke → ban → tombstone; failure after the ban fails closed — login already blocked). Retry the call: every write is idempotent |
| `python -m app.admin` calls fail with "Forbidden use of secret API key in browser" | The Supabase gateway blocks secret-key calls from browser-like user agents. The CLI uses plain `httpx` (not a browser UA), so this shouldn't happen; if it does, check you're running the CLI from the repo, not through some browser-based tool |
