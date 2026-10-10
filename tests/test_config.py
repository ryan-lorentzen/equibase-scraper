import pytest
from pydantic import ValidationError

from equibase_scraper.config import Settings


def test_disabled_by_default():
    assert Settings().scraper_enabled is False


@pytest.mark.parametrize(
    "values",
    [
        {"lookback_days": 0},
        {"request_delay_seconds": 0},
        {"max_retries": 99},
        {"chart_index_url": "https://unapproved.example/charts"},
        {"log_level": "invalid"},
    ],
)
def test_invalid_settings(values):
    with pytest.raises(ValidationError):
        Settings(**values)


def test_secret_hidden():
    assert "example-secret" not in repr(Settings(supabase_secret_key="example-secret"))


def test_persistence_requires_credentials():
    with pytest.raises(ValueError, match="SUPABASE_URL"):
        Settings().require_supabase()
