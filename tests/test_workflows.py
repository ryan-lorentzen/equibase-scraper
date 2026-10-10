import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml


def scrape_workflow():
    return yaml.safe_load(Path(".github/workflows/scrape.yml").read_text())


def collection_step():
    return next(step for step in scrape_workflow()["jobs"]["scrape"]["steps"]
                if step.get("name") == "Run conservative collection")


def run_collection_step(monkeypatch, *, event="workflow_dispatch", returncode=0, **inputs):
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setenv("SCRAPER_ENABLED", "false")
    for name in ("track", "date", "race", "dry_run", "listing_url"):
        monkeypatch.setenv(f"INPUT_{name.upper()}", inputs.get(name, ""))
    run = MagicMock(return_value=SimpleNamespace(returncode=returncode))
    monkeypatch.setattr(subprocess, "run", run)
    script = collection_step()["run"]
    assert script.startswith("python - <<'PY'\n") and script.endswith("\nPY\n")
    code = script.removeprefix("python - <<'PY'\n").removesuffix("\nPY\n")
    with pytest.raises(SystemExit) as exit_info:
        exec(compile(code, "scrape.yml:Run conservative collection", "exec"), {})
    assert os.environ["SCRAPER_ENABLED"] == "false"
    return exit_info.value.code, run


def test_workflow_safety_and_schedule_configuration():
    workflow = scrape_workflow()
    assert workflow["on"]["schedule"] == [
        {"cron": "17 6,18 * * *", "timezone": "America/New_York"},
    ]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["group"] == "equibase-chart-collection"
    assert workflow["concurrency"]["cancel-in-progress"] is False
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True
    assert inputs["dry_run"]["type"] == "boolean"
    assert all(inputs[name]["required"] for name in ("track", "date", "race", "dry_run"))
    job = workflow["jobs"]["scrape"]
    assert job["timeout-minutes"] == 30
    assert job["env"]["SCRAPER_ENABLED"] == "${{ vars.SCRAPER_ENABLED || 'false' }}"
    assert job["env"]["SUPABASE_SECRET_KEY"] == "${{ secrets.SUPABASE_SECRET_KEY }}"
    assert job["env"]["STORAGE_BUCKET"] == "${{ vars.STORAGE_BUCKET || 'race-charts' }}"
    assert job["env"]["SUPABASE_URL"] == "${{ secrets.SUPABASE_URL }}"
    for setting, default in {
        "LOOKBACK_DAYS": "7", "LOG_LEVEL": "INFO", "REQUEST_DELAY_SECONDS": "2",
        "REQUEST_TIMEOUT_SECONDS": "30", "MAX_RETRIES": "3", "MAX_CHARTS_PER_RUN": "25",
        "MAX_REQUESTS_PER_RUN": "150", "MAX_RETRY_WAIT_SECONDS": "300",
        "MAX_PDF_BYTES": "20000000",
    }.items():
        assert job["env"][setting] == "${{ vars." + setting + " || '" + default + "' }}"
    step = collection_step()
    for name in ("track", "date", "race", "dry_run", "listing_url"):
        assert step["env"][f"INPUT_{name.upper()}"] == "${{ inputs." + name + " }}"
    assert "${{ inputs." not in step["run"]


@pytest.mark.parametrize("dry_run", ["", "true", "false"])
def test_manual_invocation_is_one_specific_race(monkeypatch, dry_run):
    code, run = run_collection_step(monkeypatch, track="CD", date="2026-09-27",
                                    race="1", dry_run=dry_run)
    assert code == 0
    args = [sys.executable, "-m", "equibase_scraper.main",
            "--track", "CD", "--date", "2026-09-27", "--race", "1"]
    if dry_run != "false":
        args.append("--dry-run")
    run.assert_called_once_with(args, check=False)
    assert "--all-tracks" not in args


def test_manual_input_normalization_and_optional_listing(monkeypatch):
    url = "https://tvg.equibase.com/static/chart/pdf/CD092726USA.html"
    code, run = run_collection_step(monkeypatch, track=" cd ", date=" 2026-09-27 ",
                                    race=" 01 ", dry_run=" TRUE ", listing_url=url)
    assert code == 0
    run.assert_called_once_with(
        [sys.executable, "-m", "equibase_scraper.main", "--track", "CD",
         "--date", "2026-09-27", "--race", "1", "--listing-url", url, "--dry-run"],
        check=False,
    )


@pytest.mark.parametrize("invalid", [
    {"track": ""}, {"date": ""}, {"race": ""},
    {"track": "--all-tracks"}, {"track": "CD; echo unsafe"}, {"track": "ABCDEFG"},
    {"date": "2026-9-27"}, {"date": "2026-02-30"}, {"date": "20260927"},
    {"date": "2026-09-27\n--all-tracks"},
    {"race": "0"}, {"race": "100"}, {"race": "-1"}, {"race": "1.5"},
    {"race": "1 --all-tracks"}, {"dry_run": "yes"},
    {"listing_url": "https://unapproved.example/CD092726USA.html"},
    {"listing_url": "https://tvg.equibase.com/static/chart/pdf/WO092726CAN.html"},
    {"listing_url": "https://tvg.equibase.com/static/chart/pdf/CD092626USA.html"},
])
def test_invalid_manual_inputs_never_launch_cli(monkeypatch, capsys, invalid):
    inputs = {"track": "CD", "date": "2026-09-27", "race": "1", "dry_run": "true"}
    inputs.update(invalid)
    code, run = run_collection_step(monkeypatch, **inputs)
    assert code == 2
    run.assert_not_called()
    assert "::error::Invalid manual inputs:" in capsys.readouterr().err


def test_empty_manual_run_cannot_fall_back_to_all_tracks(monkeypatch):
    code, run = run_collection_step(monkeypatch)
    assert code == 2
    run.assert_not_called()


def test_schedule_uses_normal_recent_discovery_and_ignores_manual_inputs(monkeypatch):
    code, run = run_collection_step(monkeypatch, event="schedule", track="CD",
                                    date="invalid", race="invalid", dry_run="true",
                                    listing_url="invalid")
    assert code == 0
    run.assert_called_once_with([sys.executable, "-m", "equibase_scraper.main"], check=False)


@pytest.mark.parametrize("returncode", [1, 2])
def test_cli_failure_exit_code_is_preserved(monkeypatch, returncode):
    code, run = run_collection_step(monkeypatch, track="CD", date="2026-09-27",
                                    race="1", returncode=returncode)
    assert code == returncode
    run.assert_called_once()


def test_test_workflow_needs_no_credentials():
    workflow = yaml.safe_load(Path(".github/workflows/tests.yml").read_text())
    assert {"push", "pull_request"} <= workflow["on"].keys()
    job = workflow["jobs"]["tests"]
    assert job["env"]["SCRAPER_ENABLED"] == "false"
    commands = [s.get("run", "") for s in job["steps"]]
    assert "python -m pytest" in commands
    assert "ruff check ." in commands
