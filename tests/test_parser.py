from datetime import date

import pytest
from pdf_factory import chart_pdf

from equibase_scraper.downloader import DownloadError
from equibase_scraper.models import DiscoveredRace, Track
from equibase_scraper.parser import ParseError, parse_chart

SOURCE = DiscoveredRace(
    track=Track(code="CD", name="Synthetic Downs"), race_date=date(2026, 9, 27),
    race_number=1, chart_url="https://tvg.equibase.com/static/chart/pdf/CD092726USA1.pdf",
    listing_url="https://tvg.equibase.com/static/chart/pdf/CD092726USA.html",
)


def test_required_fields_and_separate_finish_from_post_calls_margins():
    race = parse_chart(chart_pdf(), SOURCE)
    assert race.complete
    assert race.race_type == "MAIDEN SPECIAL WEIGHT"
    assert race.distance == "Six Furlongs" and race.surface == "Dirt"
    assert race.track_condition == "Fast" and race.purse == 92000
    assert race.final_time == "1:10.95"
    assert len(race.entries) == 2
    first, second = race.entries
    assert first.horse_name == "Test Horse 1"
    assert first.program_number == "7" and first.post_position == 9
    assert first.finishing_position == 1 and second.finishing_position == 2
    assert first.jockey == "Rider, Test" and second.trainer == "Trainer, Two"
    assert first.weight == 119 and float(first.odds) == 1.35
    assert [f.time for f in race.fractions] == ["23.20", "47.04", "58.87"]
    assert "TEST HORSE 1 won" in race.footnotes
    assert "Footnotes" in race.raw_text


def test_missing_fields_are_null_with_warnings():
    race = parse_chart(chart_pdf(missing=True), SOURCE)
    assert race.purse is None
    assert race.entries[0].odds is None and race.entries[0].weight is None
    assert race.warnings


@pytest.mark.parametrize("layout", ["unexpected", "misaligned"])
def test_unreliable_layout_not_complete(layout):
    race = parse_chart(chart_pdf(layout=layout), SOURCE)
    assert not race.complete
    assert race.warnings and race.raw_text


def test_scratches_and_dead_heat():
    race = parse_chart(chart_pdf(finishes=("1DH", "1DH"), scratches=True), SOURCE)
    assert [e.finishing_position for e in race.entries[:2]] == [1, 1]
    assert race.entries[-1].status == "scratched"
    assert race.entries[-1].finishing_position is None
    assert race.entries[-1].jockey is None


def test_disqualified_finish_not_forced():
    race = parse_chart(chart_pdf(finishes=("DQ", "DNF")), SOURCE)
    assert [e.finishing_position for e in race.entries] == [None, None]
    assert [e.status for e in race.entries] == ["disqualified", "did_not_finish"]


def test_ambiguous_finish_not_inferred_from_table_order():
    race = parse_chart(chart_pdf(finishes=("?", "?")), SOURCE)
    assert all(e.finishing_position is None for e in race.entries)
    assert not race.complete


def test_malformed_pdf_and_card_identity_rejected():
    with pytest.raises(DownloadError):
        parse_chart(b"bad PDF", SOURCE)
    with pytest.raises(ParseError, match="multiple race"):
        parse_chart(chart_pdf(race_numbers=(1, 2)), SOURCE)
    with pytest.raises(ParseError, match="identity"):
        parse_chart(chart_pdf(race_numbers=(2,)), SOURCE)


def test_fragmented_horse_and_jockey_spans_share_semantic_column():
    race = parse_chart(chart_pdf(fragmented_horses=True), SOURCE)
    assert race.complete
    assert race.entries[0].horse_name == "Test Horse 1"
    assert race.entries[0].jockey == "Rider, Test"


def test_incomplete_first_page_cannot_be_hidden_by_complete_second_page():
    race = parse_chart(chart_pdf(race_numbers=(1, 1), no_boundary_first=True), SOURCE)
    assert not race.complete
    assert any("ending boundary" in w for w in race.warnings)


def test_duplicate_primary_rows_across_pages_are_incomplete():
    race = parse_chart(chart_pdf(race_numbers=(1, 1)), SOURCE)
    assert not race.complete
    assert any("across pages" in w for w in race.warnings)


def test_disqualification_narrative_requires_review():
    race = parse_chart(chart_pdf(disqualification=True), SOURCE)
    assert not race.complete
    assert all(e.finishing_position is None for e in race.entries)
