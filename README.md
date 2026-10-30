# dampingi

FastAPI backend backed by Supabase Auth (register / login / `/me`).

Deployed on Render (see `render.yaml`); identity is delegated to
Supabase Auth — design decisions in the parent repo's `docs/adr/`.

## Layout

```
app/
  main.py       FastAPI assembly            (uvicorn app.main:app)
  auth.py       register / login / /me endpoints
  supabase.py   thin httpx transport for the Supabase REST API
  config.py     pydantic-settings configuration (env / .env)
  admin.py      test-user hygiene CLI       (python -m app.admin)
tests/          live integration suite (talks to the real project)
docs/           supabase-setup.md — project setup + troubleshooting
```

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
