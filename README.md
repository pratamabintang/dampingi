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
  auth/         DOMAIN MODEL: auth          (ADR-0004 — the pattern)
    router.py     the API surface: routes, status-code mapping, token auth
    service.py    the logic: the flows; never speaks HTTP
    schemas.py    the data contracts: custom types, requests, responses
tests/          live integration suite (talks to the real project)
docs/           supabase-setup.md — project setup + troubleshooting
  migrations/   SQL to run in the Supabase dashboard SQL editor
```

Each domain model gets its own folder under `app/` with this logic /
API / data-contracts split; shared infrastructure stays at `app/` root.
`auth` is the reference implementation — copy it for the next domain.

## Local run

```powershell
pip install -r requirements.txt
copy .env.example .env    # fill in SUPABASE_* values (docs/supabase-setup.md)
uvicorn app.main:app --reload
```

## Tests

```powershell
pytest                     # live suite; skips cleanly when tests/.env is absent
python -m app.admin status # what test users exist right now
```

## Deploy

1. Push this repo; create a Web Service on Render from it.
2. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   (already set in `render.yaml`).
3. Add `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`
   in the Render dashboard (docs/supabase-setup.md §6).
