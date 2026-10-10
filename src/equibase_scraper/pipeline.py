"""Sequential collection with truthful counters and recoverable chart attempts."""

import logging
import time
from dataclasses import dataclass
from datetime import date

from equibase_scraper.config import Settings
from equibase_scraper.database import Database, PersistenceError
from equibase_scraper.discovery import Discovery
from equibase_scraper.downloader import Downloader, save_local, storage_path
from equibase_scraper.http import AccessBlocked, FetchError, PacedHttp, RequestLimit
from equibase_scraper.models import DiscoveredRace, RunSummary
from equibase_scraper.parser import ParseError, parse_chart
from equibase_scraper.storage import Storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunOptions:
    track: str | None = None
    race_date: date | None = None
    race_number: int | None = None
    listing_url: str | None = None
    dry_run: bool = False

    @property
    def mode(self) -> str:
        mode = "single-card" if self.race_date else ("recent-track" if self.track else "all-tracks")
        return f"dry-run:{mode}" if self.dry_run else mode


class Pipeline:
    def __init__(
        self, settings: Settings, http: PacedHttp,
        database: Database | None = None, storage: Storage | None = None,
    ) -> None:
        self.settings = settings
        self.http = http
        self.discovery = Discovery(http)
        self.downloader = Downloader(http)
        self.database = database
        self.storage = storage

    def process(self, source: DiscoveredRace, summary: RunSummary) -> None:
        if self.database is None or self.storage is None:
            raise PersistenceError("Persistent collection requires database and private storage")
        downloaded = None
        parsed = None
        uploaded_path = None
        phase = "database_failed"
        try:
            existing = self.database.existing(source)
            previous = self.database.previous(existing)
            # Do not trust conditional HTTP if the original object has disappeared.
            phase = "upload_failed"
            if previous and not self.storage.exists(existing["storage_path"]):
                previous = None
            phase = "failed"
            downloaded = self.downloader.download(source, previous)
            if downloaded.data is not None:
                summary.charts_downloaded += 1
            if downloaded.unchanged and previous:
                phase = "database_failed"
                self.database.checked(existing, downloaded)
                summary.charts_skipped += 1
                logger.info("Unchanged chart skipped: %s", downloaded.source_url)
                return
            if downloaded.data is None:
                raise FetchError("Cannot process a chart without verified PDF content")
            # Verify single-race boundaries before naming any local or stored original.
            # A viewer returning a full card must never become 'race-1-<hash>.pdf'.
            parsed = parse_chart(downloaded.data, source)
            for warning in parsed.warnings:
                logger.warning("%s: %s", downloaded.source_url, warning)
            if parsed.complete:
                summary.charts_parsed += 1
            path = storage_path(source, downloaded.content_hash)
            save_local(self.settings.data_dir, path, downloaded.data)
            # Archive valid original bytes even if their layout is unsupported.
            phase = "upload_failed"
            self.storage.upload(path, downloaded.data, downloaded.content_hash)
            uploaded_path = path
            if not parsed.complete:
                phase = "incomplete"
                raise ParseError("Incomplete or unsupported primary results; valid data preserved")
            phase = "database_failed"
            outcome = self.database.save(parsed, downloaded, path)
            if outcome == "saved":
                summary.charts_saved += 1
            else:
                summary.charts_skipped += 1
            logger.info("Chart %s: %s", outcome, downloaded.source_url)
        except (FetchError, ParseError, PersistenceError, OSError, ValueError) as exc:
            summary.failures += 1
            message = str(exc) if isinstance(exc, (FetchError, ParseError, PersistenceError)) else (
                f"Processing failed ({type(exc).__name__})"
            )
            logger.error("%s: %s", source.pdf_url or source.chart_url, message)
            try:
                self.database.failed(source, phase, message, downloaded, uploaded_path, parsed)
            except PersistenceError as recording_error:
                logger.error("%s: %s", source.chart_url, recording_error)
            if isinstance(exc, (AccessBlocked, RequestLimit, PersistenceError)):
                # An access restriction or persistence outage stops further collection.
                raise

    def run(self, options: RunOptions, *, today: date) -> RunSummary:
        started = time.monotonic()
        summary = RunSummary(mode=options.mode)
        logger.info("Execution started: mode=%s", options.mode)
        try:
            if not self.settings.scraper_enabled:
                logger.info("Remote scraping disabled: SCRAPER_ENABLED=false")
                return summary
            if not options.dry_run and (self.database is None or self.storage is None):
                raise PersistenceError("Persistent collection is not configured")
            cards = self.discovery.cards(
                today=today, track_code=options.track, race_date=options.race_date,
                listing_url=options.listing_url,
            )
            if options.race_date and not cards:
                raise FetchError("Specified card is not linked in the index; supply --listing-url")
            attempted = 0
            for card in cards:
                if attempted >= self.settings.max_charts_per_run:
                    summary.limit_reached = True
                    break
                try:
                    races = self.discovery.races(card)
                except (AccessBlocked, RequestLimit):
                    raise
                except FetchError as exc:
                    summary.failures += 1
                    logger.error("Listing discovery failed: %s: %s", card.listing_url, exc)
                    continue
                if options.race_number:
                    races = [r for r in races if r.race_number == options.race_number]
                    if not races:
                        summary.failures += 1
                        logger.error("Requested race is not present: %s", card.listing_url)
                summary.charts_discovered += len(races)
                for source in races:
                    if attempted >= self.settings.max_charts_per_run:
                        summary.limit_reached = True
                        break
                    attempted += 1
                    if options.dry_run:
                        logger.info("Would process track=%s date=%s race=%s URL=%s",
                                    source.track.code, source.race_date, source.race_number,
                                    source.pdf_url or source.chart_url)
                    else:
                        self.process(source, summary)
        except (FetchError, PersistenceError, ValueError) as exc:
            # process() already counted its own failures.
            if not summary.failures:
                summary.failures += 1
            if isinstance(exc, RequestLimit):
                summary.limit_reached = True
            logger.error("Execution stopped: %s", exc)
        finally:
            summary.tracks_checked = len(self.discovery.tracks_checked)
            summary.dates_checked = len(self.discovery.dates_checked)
            summary.requests = self.http.requests
            summary.runtime_seconds = round(time.monotonic() - started, 3)
            logger.info("Execution finished: %s", summary.model_dump_json())
        return summary
