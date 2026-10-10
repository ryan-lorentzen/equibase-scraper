from datetime import date
from pathlib import Path

import httpx
import pytest

from equibase_scraper.config import Settings
from equibase_scraper.discovery import Discovery, card_from_url, parse_index, parse_listing
from equibase_scraper.http import FetchError, PacedHttp

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://tvg.equibase.com/static/chart/pdf/"
CARD = BASE + "CD092726USA.html"


def test_index_tracks_dates_deduplicated():
    tracks, cards = parse_index((FIXTURES / "index.html").read_text(), BASE)
    assert [t.code for t in tracks] == ["CD", "WO"]
    assert len(cards) == 2
    assert cards[0].race_date == date(2026, 9, 27)


def test_listing_races_metadata_and_mobile_pdfs():
    races = parse_listing((FIXTURES / "listing.html").read_text(), card_from_url(CARD))
    assert [r.race_number for r in races] == [1, 2]
    assert races[0].purse == 92000
    assert races[0].track.name == "Synthetic Downs"
    assert races[0].pdf_url == BASE + "CD092726USA1.pdf"
    assert races[1].race_type is None
    assert len(races[1].warnings) == 2


def test_invalid_and_empty_pages():
    with pytest.raises(FetchError):
        parse_listing("<h1>Something else</h1>", card_from_url(CARD))
    with pytest.raises(FetchError):
        parse_index("<h1>Something else</h1>", BASE)
    assert parse_listing("<h1>No charts available</h1>", card_from_url(CARD)) == []


def test_recent_window_uses_actual_links():
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=(FIXTURES / "index.html").read_text())

    with PacedHttp(Settings(scraper_enabled=True, lookback_days=1),
                   transport=httpx.MockTransport(handle)) as http:
        cards = Discovery(http).cards(today=date(2026, 9, 27))
    assert len(cards) == 1
    assert calls == [BASE]


def test_explicit_listing_identity_and_historical_limit():
    with PacedHttp(Settings()) as http:
        discovery = Discovery(http)
        with pytest.raises(ValueError, match="--track"):
            discovery.cards(today=date(2026, 9, 28), track_code="WO", listing_url=CARD)
        with pytest.raises(ValueError, match="31 days"):
            discovery.cards(today=date(2026, 11, 28), listing_url=CARD)
