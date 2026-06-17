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
    risk_profiles_path: str = "/app/config/risk_profiles.json"
    log_level: str = "info"
    cors_origins: list[str] = ["https://trackwild.ru", "https://www.trackwild.ru"]
    secret_key: str = "change-me"
    env: str = "production"

    # Pre-generation settings
    tile_workers: int = 2
    pregen_enabled: bool = True
    pregen_z_min: int = 5
    pregen_z_max: int = 10
    # Bbox for NW Federal District in EPSG:4326 (min_lon, min_lat, max_lon, max_lat)
    pregen_bbox: str = "28.0,65.5,42.0,71.5"
    # Stale tile re-generation
    pregen_ttl_hours: int = 24
    pregen_stale_check_seconds: int = 300
    pregen_stale_batch: int = 50

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
