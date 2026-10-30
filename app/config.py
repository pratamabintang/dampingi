from typing import Optional

from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError


class Settings(BaseSettings):
    """Central app configuration, loaded from environment / .env file."""

    app_env: str = "development"
    # Render injects PORT automatically; default for local runs
    port: int = 8000

    # Supabase (required: app fails fast at startup when unset)
    supabase_url: str
    # Publishable key (sb_publishable_...) or legacy anon key
    supabase_publishable_key: str = Field(
        validation_alias=AliasChoices(
            "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY"
        )
    )
    # Secret key (sb_secret_...) or legacy service_role key; used
    # server-side only: profile reads and (in tests) user cleanup
    supabase_secret_key: str = Field(
        validation_alias=AliasChoices(
            "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"
        )
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


try:
    settings = Settings()
except (ValidationError, SettingsError) as exc:
    raise SystemExit(f"Invalid configuration: {exc}") from exc


def supabase_auth_http() -> dict:
    """Headers for unauthenticated / auth-flow Supabase REST calls."""
    return {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {settings.supabase_publishable_key}",
        "Content-Type": "application/json",
    }


def supabase_service_http() -> dict:
    """Headers for server-side Supabase REST calls (PostgREST with the
    secret key, which acts with the service_role and bypasses RLS)."""
    return {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
        "Content-Type": "application/json",
    }
