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
  created_at timestamptz not null default now()
);

-- No policies on purpose: only the service_role/secret key
-- (used by the app's server-side profile reads, and by tests)
-- may touch this table.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
stable
as $$
begin
  insert into public.profiles (user_id, name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'name', ''));
  return new;
end;
$$;

create trigger on_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
```

- `on delete cascade`: deleting an auth user (tests do this in teardown) removes the profile too.
- If you run this after creating some users, backfill them with:
  `insert into public.profiles (user_id, name) select id, coalesce(raw_user_meta_data ->> 'name', '') from auth.users on conflict do nothing;`

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

**Local (tests):** copy `dampingi/tests/.env.example` → `dampingi/tests/.env` with the same values. Without it, all live tests *skip* (CI without a project stays green); with it, `pytest` runs the whole live suite and cleans up its users. From the `dampingi/` directory:

```powershell
pytest
```

**Render:** add the three env vars to the web service (Dashboard → Environment) — same names as the `.env` keys. `render.yaml` needs no start-command changes.

## 7. Smoke test (curl/PowerShell)

```powershell
# Register (name + Gmail + password)
Invoke-RestMethod -Method Post "$url/auth/v1/signup" ...   # not needed directly; use the app:

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/auth/register" `
  -ContentType "application/json" `
  -Body '{"name":"Ada","email":"somebody@gmail.com","password":"Sup3rSecure!"}'
# => 201 { user: { id, email, name: "Ada" }, access_token: "..." }

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/auth/login" `
  -ContentType "application/json" `
  -Body '{"email":"somebody@gmail.com","password":"Sup3rSecure!"}'

$t = "<access_token from above>"
Invoke-RestMethod -Uri "http://localhost:8000/me" -Headers @{ Authorization = "Bearer $t" }
# => { id, email, name }
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| Login returns 401 for a just-registered user | Email confirmation wasn't disabled (step 3), or the user made a typo'd password |
| `name` is `null` in responses | The trigger/table from step 4 was never run (fetching the profile returns no row) |
| Register fails with a password policy error | Supabase enforces a minimum length (default 8); the app pre-validates to match |
| `/me` always 401 with "no trusted signing key matched" | Project still uses the legacy JWT secret (step 5) |
| 409 on register of a brand-new email | That Gmail already registered in this project (a previous test run that crashed before cleanup, for example) — delete it from **Authentication → Users** |
