from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "Sezzle Weather Service"
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = ""
    cache_ttl_seconds: int = 300
    upstream_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    upstream_read_timeout_seconds: float = Field(default=5.0, gt=0)
    upstream_pool_timeout_seconds: float = Field(default=2.0, gt=0)
    upstream_retry_attempts: int = Field(default=3, ge=1, le=3)
    upstream_total_timeout_seconds: float = Field(default=20.0, gt=0)
    upstream_max_concurrency: int = Field(default=20, ge=1)


settings = Settings()