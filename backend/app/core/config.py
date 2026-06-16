import json

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def json_or_list(value: str | list[str]) -> list[str]:
    """Parse list from env var: handles both JSON string and plain list."""
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return [value]


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/trackwild"
    tile_cache_dir: str = "/tile_cache"
    log_level: str = "info"
    cors_origins: list[str] = ["https://trackwild.ru", "https://www.trackwild.ru"]
    secret_key: str = "change-me"
    env: str = "production"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        return json_or_list(v)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"debug", "info", "warning", "error", "critical"}
        v = v.lower()
        if v not in allowed:
            raise ValueError(f"Invalid log level: {v}, must be one of {allowed}")
        return v


settings = Settings()
