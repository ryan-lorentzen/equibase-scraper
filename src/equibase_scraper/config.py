"""Central configuration; credentials are never included in representations."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

ALLOWED_HOSTS = frozenset({"tvg.equibase.com", "www.equibase.com", "equibase.com"})


def validate_source_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_HOSTS
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise ValueError("Source URLs must use HTTPS on an approved Equibase host")
    return url


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supabase_url: str | None = None
    supabase_secret_key: SecretStr | None = Field(default=None, repr=False)
    storage_bucket: str = "race-charts"
    scraper_enabled: bool = False
    lookback_days: int = Field(default=7, ge=1, le=31)
    log_level: str = "INFO"
    request_delay_seconds: float = Field(default=2, ge=1, le=300)
    request_timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_retries: int = Field(default=3, ge=0, le=5)
    max_charts_per_run: int = Field(default=25, ge=1, le=250)
    max_requests_per_run: int = Field(default=150, ge=1, le=1000)
    max_retry_wait_seconds: float = Field(default=300, ge=1, le=3600)
    max_pdf_bytes: int = Field(default=20_000_000, ge=1024, le=100_000_000)
    user_agent: str = (
        "equibase-scraper/0.1.0 (+https://github.com/ryan-lorentzen/equibase-scraper)"
    )
    chart_index_url: str = "https://tvg.equibase.com/static/chart/pdf/"
    data_dir: Path = Path("data/charts")

    @field_validator("chart_index_url")
    @classmethod
    def check_source(cls, value: str) -> str:
        return validate_source_url(value)

    @field_validator("log_level")
    @classmethod
    def check_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Use DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return value

    @field_validator("user_agent", "storage_bucket")
    @classmethod
    def check_nonempty(cls, value: str) -> str:
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("Must be nonempty and contain no newlines")
        return value

    @model_validator(mode="after")
    def check_credentials(self) -> "Settings":
        if self.supabase_url and urlsplit(self.supabase_url).scheme != "https":
            raise ValueError("SUPABASE_URL must use HTTPS")
        return self

    def require_supabase(self) -> None:
        if not self.supabase_url or not self.supabase_secret_key:
            raise ValueError("Set SUPABASE_URL and SUPABASE_SECRET_KEY for persistent scraping")

    @classmethod
    def from_env(cls, *, env_file: Path | str = ".env") -> "Settings":
        load_dotenv(env_file, override=False)
        values = {
            name: os.environ[name.upper()]
            for name in cls.model_fields
            if os.environ.get(name.upper(), "").strip()
        }
        return cls.model_validate(values)
