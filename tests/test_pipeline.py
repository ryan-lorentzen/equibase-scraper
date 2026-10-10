from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from pdf_factory import chart_pdf
from test_discovery import CARD, FIXTURES
from test_parser import SOURCE

from equibase_scraper.config import Settings
from equibase_scraper.database import Database, PersistenceError
from equibase_scraper.http import PacedHttp
from equibase_scraper.main import main
from equibase_scraper.pipeline import Pipeline, RunOptions


def pipeline(tmp_path, data=None, **values):
    settings = Settings(scraper_enabled=True, data_dir=tmp_path, **values)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == CARD:
            return httpx.Response(200, text=(FIXTURES / "listing.html").read_text())
        return httpx.Response(200, content=data or chart_pdf(), headers={"ETag": '"one"'})

    http = PacedHttp(settings, transport=httpx.MockTransport(handler), sleep=lambda _: None)
    db = MagicMock(spec=Database)
    db.existing.return_value = None
    db.previous.side_effect = Database.previous
    db.save.return_value = "saved"
    storage = MagicMock()
    storage.exists.return_value = True
    return Pipeline(settings, http, db, storage), calls


OPTIONS = RunOptions(track="CD", race_date=date(2026, 9, 27), race_number=1, listing_url=CARD)


def test_full_pipeline_and_unchanged_rerun(tmp_path):
    unchanged_pdf = chart_pdf()
    runner, calls = pipeline(tmp_path, data=unchanged_pdf)
    summary = runner.run(OPTIONS, today=date(2026, 10, 10))
    assert summary.charts_saved == summary.charts_parsed == summary.charts_downloaded == 1
    assert summary.failures == 0
    assert calls == [CARD, SOURCE.chart_url]
    saved_race, download, path = runner.database.save.call_args.args
    existing = {"id": "chart-id", "content_hash": download.content_hash,
                "source_url": download.source_url, "storage_path": path,
                "parsing_status": "parsed", "parser_version": "0.1.0", "etag": '"one"'}
    second, calls = pipeline(tmp_path, data=unchanged_pdf)
    second.database.existing.return_value = existing
    summary = second.run(OPTIONS, today=date(2026, 10, 10))
    assert summary.charts_skipped == 1 and summary.charts_saved == 0
    second.database.save.assert_not_called()
    second.storage.upload.assert_not_called()
    second.database.checked.assert_called_once()
    assert saved_race.complete


def test_conditional_304_no_download_or_upload(tmp_path):
    runner, _ = pipeline(tmp_path)
    existing = {"id": "chart-id", "content_hash": "a" * 64, "source_url": SOURCE.chart_url,
                "storage_path": "existing.pdf", "parsing_status": "parsed",
                "parser_version": "0.1.0", "etag": '"one"'}
    runner.database.existing.return_value = existing

    def handler(req):
        if str(req.url) == CARD:
            return httpx.Response(200, text=(FIXTURES / "listing.html").read_text())
        assert req.headers["If-None-Match"] == '"one"'
        return httpx.Response(304)

    runner.http.client.close()
    runner.http.client = httpx.Client(transport=httpx.MockTransport(handler))
    summary = runner.run(OPTIONS, today=date(2026, 10, 10))
    assert summary.charts_skipped == 1 and summary.charts_downloaded == 0
    runner.storage.upload.assert_not_called()


@pytest.mark.parametrize("failure", ["upload", "database", "parse"])
def test_failures_never_count_saved_and_preserve_snapshot(tmp_path, failure):
    data = chart_pdf(layout="unexpected") if failure == "parse" else None
    runner, _ = pipeline(tmp_path, data=data)
    if failure == "upload":
        runner.storage.upload.side_effect = PersistenceError("Upload failed")
    if failure == "database":
        runner.database.save.side_effect = PersistenceError("Transaction failed")
    summary = runner.run(OPTIONS, today=date(2026, 10, 10))
    assert summary.failures == 1 and summary.charts_saved == 0
    assert runner.database.failed.call_args.args[1] == {
        "upload": "upload_failed", "database": "database_failed", "parse": "incomplete",
    }[failure]
    if failure != "database":
        runner.database.save.assert_not_called()


def test_dry_run_only_listing_no_files_or_persistence(tmp_path):
    runner, calls = pipeline(tmp_path)
    summary = runner.run(RunOptions(track="CD", race_date=date(2026, 9, 27), listing_url=CARD,
                                    dry_run=True), today=date(2026, 10, 10))
    assert summary.charts_discovered == 2
    assert summary.charts_downloaded == summary.charts_saved == 0
    assert calls == [CARD]
    assert not list(tmp_path.iterdir())
    runner.database.existing.assert_not_called()
    runner.storage.upload.assert_not_called()


def test_targeted_dry_run_reports_only_the_requested_race(tmp_path, caplog):
    runner, calls = pipeline(tmp_path)
    with caplog.at_level("INFO"):
        summary = runner.run(replace(OPTIONS, dry_run=True), today=date(2026, 10, 10))
    actions = [record.getMessage() for record in caplog.records
               if "Would process" in record.getMessage()]
    assert len(actions) == 1 and "race=1 " in actions[0]
    assert summary.mode == "dry-run:single-card"
    assert summary.tracks_checked == summary.dates_checked == summary.charts_discovered == 1
    assert summary.charts_downloaded == summary.charts_saved == summary.failures == 0
    assert calls == [CARD]
    runner.database.existing.assert_not_called()
    runner.storage.upload.assert_not_called()


def test_disabled_dry_run_no_http(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with PacedHttp(settings) as http:
        summary = Pipeline(settings, http).run(RunOptions(dry_run=True), today=date(2026, 10, 10))
    assert summary.requests == summary.failures == 0


def test_chart_cap_limits_intended_actions(tmp_path):
    runner, calls = pipeline(tmp_path, max_charts_per_run=1)
    summary = runner.run(RunOptions(track="CD", race_date=date(2026, 9, 27), listing_url=CARD,
                                    dry_run=True), today=date(2026, 10, 10))
    assert summary.limit_reached and summary.charts_discovered == 2
    assert calls == [CARD]


def test_cli_disabled_and_invalid_config(monkeypatch, capsys):
    monkeypatch.setattr(Settings, "from_env", lambda: Settings())
    assert main(["--dry-run"]) == 0
    assert "Source requests:         0" in capsys.readouterr().out
    monkeypatch.setattr(Settings, "from_env", lambda: Settings(scraper_enabled=True))
    assert main([]) == 2


def test_offline_cli_does_not_make_requests(tmp_path, monkeypatch, capsys):
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(chart_pdf())
    monkeypatch.setattr(Settings, "from_env", lambda: Settings())
    assert main(["--parse-file", str(path), "--track", "CD", "--date", "2026-09-27",
                 "--race", "1"]) == 0
    assert '"complete": true' in capsys.readouterr().out


def test_saved_outcome_requires_confirmed_database_response():
    client = MagicMock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=None)
    from equibase_scraper.downloader import DownloadResult, pdf_hash
    from equibase_scraper.parser import parse_chart

    data = chart_pdf()
    with pytest.raises(PersistenceError, match="confirmed outcome"):
        Database(client).save(parse_chart(data, SOURCE),
                              DownloadResult(SOURCE.chart_url, pdf_hash(data), data, False), "path")


def test_changed_chart_replaces_snapshot_only_after_upload(tmp_path):
    runner, _ = pipeline(tmp_path, data=chart_pdf(finishes=("2", "1")))
    runner.database.existing.return_value = {
        "id": "chart-id", "content_hash": "a" * 64, "source_url": SOURCE.chart_url,
        "storage_path": "old-valid.pdf", "parsing_status": "parsed", "parser_version": "0.1.0",
    }
    ordering = []
    runner.storage.upload.side_effect = lambda *args: ordering.append("upload")
    runner.database.save.side_effect = lambda *args: ordering.append("transaction") or "saved"
    summary = runner.run(OPTIONS, today=date(2026, 10, 10))
    assert summary.charts_saved == 1 and summary.failures == 0
    assert ordering == ["upload", "transaction"]
    assert runner.database.save.call_args.args[1].content_hash != "a" * 64


def test_existing_snapshot_remains_untouched_after_bad_parse(tmp_path):
    runner, _ = pipeline(tmp_path, data=chart_pdf(layout="misaligned"))
    runner.database.existing.return_value = {
        "id": "valid-chart", "content_hash": "b" * 64, "source_url": SOURCE.chart_url,
        "storage_path": "old-valid.pdf", "parsing_status": "parsed", "parser_version": "0.1.0",
    }
    summary = runner.run(OPTIONS, today=date(2026, 10, 10))
    assert summary.failures == 1 and summary.charts_saved == 0
    runner.database.save.assert_not_called()
    runner.database.checked.assert_not_called()
    assert runner.database.failed.call_args.args[1] == "incomplete"


def test_full_card_cannot_be_named_or_uploaded_as_one_race(tmp_path):
    runner, _ = pipeline(tmp_path, data=chart_pdf(race_numbers=(1, 2)))
    summary = runner.run(OPTIONS, today=date(2026, 10, 10))
    assert summary.failures == 1 and summary.charts_saved == 0
    runner.storage.upload.assert_not_called()
    runner.database.save.assert_not_called()
    assert not list(tmp_path.iterdir())
