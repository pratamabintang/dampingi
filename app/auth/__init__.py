"""Auth domain model (ADR-0004: one folder per domain model).

- router.py   the API surface: routes, status-code mapping, token auth
- service.py  the logic: the flows; never speaks HTTP
- schemas.py  the data contracts: custom types, requests, responses
"""

from app.auth.router import router

__all__ = ["router"]
