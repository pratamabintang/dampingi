"""dampingi application package.

Layout:
- main.py     FastAPI app assembly (`uvicorn app.main:app`)
- auth.py     register / login / /me endpoints (JWT verified locally via JWKS)
- supabase.py thin httpx transport for the Supabase REST API
- config.py   pydantic-settings configuration (env / .env)
- admin.py    test-user hygiene CLI (`python -m app.admin status|purge`)
"""
