"""Validated values shared across discovery, parsing, and persistence."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from equibase_scraper.config import validate_source_url


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Track(Model):
    code: str = Field(pattern=r"^[A-Z0-9]{1,6}$")
    name: str | None = None
    country: str = Field(default="USA", pattern=r"^[A-Z]{2,3}$")


class Card(Model):
    track: Track
    race_date: date
    listing_url: str

    _url = field_validator("listing_url")(validate_source_url)


class DiscoveredRace(Model):
    track: Track
    race_date: date
    race_number: int = Field(ge=1, le=99)
    race_type: str | None = None
    purse: Decimal | None = Field(default=None, ge=0)
    chart_url: str
    listing_url: str
    pdf_url: str | None = None
    warnings: list[str] = Field(default_factory=list)

    _urls = field_validator("chart_url", "listing_url")(validate_source_url)

    @field_validator("pdf_url")
    @classmethod
    def check_pdf_url(cls, value: str | None) -> str | None:
        return validate_source_url(value) if value else None


class Entry(Model):
    horse_name: str = Field(min_length=1)
    program_number: str | None = None
    post_position: int | None = Field(default=None, ge=1, le=99)
    finishing_position: int | None = Field(default=None, ge=1, le=99)
    finish_text: str | None = None
    status: Literal["finished", "scratched", "disqualified", "did_not_finish", "unknown"]
    jockey: str | None = None
    trainer: str | None = None
    odds: Decimal | None = Field(default=None, ge=0)
    weight: Decimal | None = Field(default=None, gt=0, le=250)


class Fraction(Model):
    label: str
    time: str
    order: int = Field(ge=1)


class ParsedRace(Model):
    track: Track
    race_date: date
    race_number: int = Field(ge=1, le=99)
    race_type: str | None = None
    distance: str | None = None
    surface: str | None = None
    track_condition: str | None = None
    purse: Decimal | None = Field(default=None, ge=0)
    final_time: str | None = None
    footnotes: str | None = None
    entries: list[Entry] = Field(default_factory=list)
    fractions: list[Fraction] = Field(default_factory=list)
    raw_text: str
    warnings: list[str] = Field(default_factory=list)
    complete: bool = False


class RunSummary(Model):
    mode: str
    tracks_checked: int = 0
    dates_checked: int = 0
    charts_discovered: int = 0
    charts_downloaded: int = 0
    charts_parsed: int = 0
    charts_saved: int = 0
    charts_skipped: int = 0
    failures: int = 0
    requests: int = 0
    limit_reached: bool = False
    runtime_seconds: float = 0
