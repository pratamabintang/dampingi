# dampingi

FastAPI backend backed by Supabase Auth (register / login / `/me` /
logout / password change).

Deployed on Render (see `render.yaml`); identity is delegated to
Supabase Auth — design decisions in the parent repo's `docs/adr/`.

## Layout

```
app/
  main.py       FastAPI assembly            (uvicorn app.main:app)
  config.py     pydantic-settings configuration (env / .env)
  supabase.py   thin httpx transport for the Supabase REST API (shared)
  admin.py      test-user hygiene CLI       (python -m app.admin)
  seed.py       seed script for local dev   (python -m app.seed)
  auth/         DOMAIN MODEL: auth          (ADR-0004 — the pattern)
    router.py     the API surface: routes, status-code mapping, token auth
    service.py    the logic: the flows; never speaks HTTP
    schemas.py    the data contracts: custom types, requests, responses
tests/          live integration suite (talks to the real project)
supabase/
  migrations/   baseline + delta migrations (applied by supabase db reset)
  config.toml   Supabase CLI config (local Docker stack)
```

Each domain model gets its own folder under `app/` with this logic /
API / data-contracts split; shared infrastructure stays at `app/` root.
`auth` is the reference implementation — copy it for the next domain.

## Local run

**Option 1: Local Docker stack (recommended)**

```powershell
supabase start                        # start local Postgres + Auth
supabase db reset                     # apply migrations and seed
copy .env.example .env                # will be populated by supabase start
uvicorn app.main:app --reload
```

See `../../docs/supabase-setup.md` for detailed setup (includes troubleshooting).

**Option 2: Hosted project**

```powershell
copy .env.example .env                # fill in SUPABASE_* values
uvicorn app.main:app --reload
```

See `../../docs/supabase-setup.md` for project creation and config.

## Tests

```powershell
pytest                     # offline suite; skips live tests cleanly when tests/.env is absent
pytest -m live             # live suite against hosted project (requires tests/.env + ~5 min between runs)
python -m app.admin status # what test users exist right now
python -m app.seed         # create 4 seed Profiles (Mother, Companion, Professional, deleted Mother)
```

## Deploy

1. Push this repo; create a Web Service on Render from it.
2. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   (already set in `render.yaml`).
3. Add `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`
   in the Render dashboard. See `../../docs/supabase-setup.md` for hosted project setup.
