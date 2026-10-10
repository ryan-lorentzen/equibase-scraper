from pathlib import Path

import yaml


def test_workflow_safety_and_schedule_configuration():
    workflow = yaml.safe_load(Path(".github/workflows/scrape.yml").read_text())
    assert workflow["on"]["schedule"] == [
        {"cron": "17 6,18 * * *", "timezone": "America/New_York"},
    ]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["on"]["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    job = workflow["jobs"]["scrape"]
    assert job["timeout-minutes"] == 30
    assert job["env"]["SCRAPER_ENABLED"] == "${{ vars.SCRAPER_ENABLED || 'false' }}"
    assert job["env"]["SUPABASE_SECRET_KEY"] == "${{ secrets.SUPABASE_SECRET_KEY }}"
    assert job["env"]["STORAGE_BUCKET"] == "${{ vars.STORAGE_BUCKET || 'race-charts' }}"


def test_test_workflow_needs_no_credentials():
    workflow = yaml.safe_load(Path(".github/workflows/tests.yml").read_text())
    assert {"push", "pull_request"} <= workflow["on"].keys()
    job = workflow["jobs"]["tests"]
    assert job["env"]["SCRAPER_ENABLED"] == "false"
    commands = [s.get("run", "") for s in job["steps"]]
    assert "python -m pytest" in commands
    assert "ruff check ." in commands
