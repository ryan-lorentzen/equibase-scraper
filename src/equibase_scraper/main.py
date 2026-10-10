"""CLI for opt-in collection, discovery-only dry runs, and offline parsing."""

import argparse
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from equibase_scraper.config import Settings
from equibase_scraper.database import Database, PersistenceError, make_client
from equibase_scraper.discovery import card_from_url
from equibase_scraper.http import FetchError, PacedHttp
from equibase_scraper.logging_config import configure_logging
from equibase_scraper.models import DiscoveredRace, RunSummary, Track
from equibase_scraper.parser import parse_file
from equibase_scraper.pipeline import Pipeline, RunOptions
from equibase_scraper.storage import Storage

logger = logging.getLogger(__name__)


def cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    tracks = parser.add_mutually_exclusive_group()
    tracks.add_argument("--track", type=str.upper, help="Published Equibase track code")
    tracks.add_argument("--all-tracks", action="store_true", help="All index tracks (default)")
    parser.add_argument("--date", type=date.fromisoformat, help="Single card date: YYYY-MM-DD")
    parser.add_argument("--race", type=int, help="Only this race on a specified card")
    parser.add_argument("--listing-url", help="Explicit published track/date listing URL")
    parser.add_argument(
        "--dry-run", action="store_true", help="Discover only; no PDFs or persistence"
    )
    parser.add_argument("--parse-file", type=Path, help="Parse an authorized local PDF offline")
    return parser


def print_summary(summary: RunSummary) -> None:
    print("\nEquibase Scraper - Run Summary")
    print(f"Mode: {summary.mode}")
    for label, value in (
        ("Tracks checked", summary.tracks_checked), ("Track/dates checked", summary.dates_checked),
        ("Charts discovered", summary.charts_discovered),
        ("Charts downloaded", summary.charts_downloaded), ("Charts parsed", summary.charts_parsed),
        ("Charts saved", summary.charts_saved), ("Charts skipped", summary.charts_skipped),
        ("Failed", summary.failures), ("Source requests", summary.requests),
        ("Limit reached", summary.limit_reached), ("Runtime (seconds)", summary.runtime_seconds),
    ):
        print(f"{label + ':':<24} {value}")


def _offline(args: argparse.Namespace, settings: Settings) -> int:
    if args.listing_url:
        card = card_from_url(args.listing_url)
        if card.track.code != args.track or card.race_date != args.date:
            raise ValueError("Listing URL does not match offline --track/--date")
        track = card.track
        url = card.listing_url
    else:
        track = Track(code=args.track)
        # Provenance placeholder only; this URL is never fetched or persisted in offline mode.
        url = settings.chart_index_url
    source = DiscoveredRace(track=track, race_date=args.date, race_number=args.race,
                            chart_url=url, listing_url=url)
    parsed = parse_file(args.parse_file, source, max_bytes=settings.max_pdf_bytes)
    logger.info("Offline parsing finished: complete=%s warnings=%s",
                parsed.complete, len(parsed.warnings))
    print(parsed.model_dump_json(indent=2))
    return 0 if parsed.complete else 1


def main(argv: list[str] | None = None) -> int:
    parser = cli_parser()
    args = parser.parse_args(argv)
    if args.date and not args.track:
        parser.error("--date requires --track")
    if args.race is not None and (not args.date or not 1 <= args.race <= 99):
        parser.error("--race requires --date and must be between 1 and 99")
    if args.listing_url and not args.date:
        parser.error("--listing-url requires --track and --date")
    if args.parse_file and (not args.date or not args.race or args.dry_run):
        parser.error("--parse-file requires --track, --date, --race and cannot use --dry-run")
    try:
        settings = Settings.from_env()
    except ValidationError as exc:
        # Pydantic's standard repr includes invalid input values, potentially credentials.
        details = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'settings'}: {e['msg']}"
                            for e in exc.errors(include_input=False))
        print(f"Invalid configuration: {details}")
        return 2
    configure_logging(settings.log_level)
    if args.parse_file:
        logger.info("Execution started: mode=offline-parse")
        try:
            return _offline(args, settings)
        except (ValueError, OSError, FetchError) as exc:
            logger.error("Offline parse failed (%s)", type(exc).__name__)
            return 1
        finally:
            logger.info("Execution finished: mode=offline-parse")
    options = RunOptions(args.track, args.date, args.race, args.listing_url, args.dry_run)
    today = datetime.now(ZoneInfo("America/New_York")).date()
    database = None
    storage = None
    if settings.scraper_enabled and not args.dry_run:
        try:
            settings.require_supabase()
            client = make_client(settings)
            database = Database(client)
            storage = Storage(client, settings.storage_bucket)
        except (ValueError, PersistenceError) as exc:
            logger.error("Execution setup failed: %s", exc)
            print_summary(RunSummary(mode=options.mode, failures=1))
            return 2
    with PacedHttp(settings) as http:
        summary = Pipeline(settings, http, database, storage).run(options, today=today)
    print_summary(summary)
    return 1 if summary.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
