from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError


class Settings(BaseSettings):
    """Central app configuration, loaded from environment / .env file."""

    app_env: str = "development"
    # Render injects PORT automatically; default for local runs
    port: int = 8000
    # Required: app fails fast at startup when unset (no default)
    secret_key: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


try:
    settings = Settings()
except (ValidationError, SettingsError) as exc:
    raise SystemExit(f"Invalid configuration: {exc}") from exc
