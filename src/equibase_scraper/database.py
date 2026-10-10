"""Supabase reads plus server-side transactional writes, with sanitized failures."""

from datetime import UTC, datetime
from typing import Any

from supabase import Client, create_client

from equibase_scraper.config import Settings
from equibase_scraper.downloader import DownloadResult, PreviousDownload
from equibase_scraper.models import DiscoveredRace, ParsedRace
from equibase_scraper.parser import PARSER_VERSION


class PersistenceError(RuntimeError):
    pass


def make_client(settings: Settings) -> Client:
    settings.require_supabase()
    try:
        return create_client(settings.supabase_url, settings.supabase_secret_key.get_secret_value())
    except Exception as exc:
        raise PersistenceError(
            f"Supabase client configuration failed ({type(exc).__name__})"
        ) from None


class Database:
    def __init__(self, client: Client) -> None:
        self.client = client

    def existing(self, race: DiscoveredRace) -> dict[str, Any] | None:
        try:
            result = (
                self.client.table("charts")
                .select("*,races!inner(race_date,race_number,tracks!inner(code))")
                .eq("races.tracks.code", race.track.code)
                .eq("races.race_date", race.race_date.isoformat())
                .eq("races.race_number", race.race_number).limit(1).execute()
            )
            return result.data[0] if result.data else None
        except Exception as exc:
            raise PersistenceError(f"Chart lookup failed ({type(exc).__name__})") from None

    @staticmethod
    def previous(row: dict[str, Any] | None) -> PreviousDownload | None:
        if (
            not row or row.get("parsing_status") != "parsed"
            or row.get("parser_version") != PARSER_VERSION or not row.get("content_hash")
        ):
            return None
        return PreviousDownload(row["source_url"], row["content_hash"],
                                row.get("etag"), row.get("last_modified"))

    def save(self, race: ParsedRace, downloaded: DownloadResult, path: str) -> str:
        if not race.complete:
            raise PersistenceError("Incomplete parsing output cannot replace valid results")
        payload = race.model_dump(mode="json")
        payload.update(source_url=downloaded.source_url, content_hash=downloaded.content_hash,
                       storage_path=path, etag=downloaded.etag,
                       last_modified=downloaded.last_modified, parser_version=PARSER_VERSION)
        try:
            result = self.client.rpc("save_chart", {"p_payload": payload}).execute()
            outcome = result.data.get("outcome") if isinstance(result.data, dict) else None
            if outcome not in {"saved", "unchanged"}:
                raise PersistenceError("Transactional save returned no confirmed outcome")
            return outcome
        except PersistenceError:
            raise
        except Exception as exc:
            raise PersistenceError(f"Transactional save failed ({type(exc).__name__})") from None

    def checked(self, row: dict[str, Any], downloaded: DownloadResult) -> None:
        try:
            result = (
                self.client.table("charts").update({
                    "last_checked_at": datetime.now(UTC).isoformat(),
                    "etag": downloaded.etag, "last_modified": downloaded.last_modified,
                }).eq("id", row["id"]).eq("content_hash", downloaded.content_hash).execute()
            )
            if not result.data:
                raise PersistenceError("Unchanged chart check was not confirmed by the database")
        except PersistenceError:
            raise
        except Exception as exc:
            raise PersistenceError(f"Chart check update failed ({type(exc).__name__})") from None

    def failed(
        self, source: DiscoveredRace, status: str, error: str,
        downloaded: DownloadResult | None = None, path: str | None = None,
        parsed: ParsedRace | None = None,
    ) -> None:
        if status not in {"failed", "incomplete", "upload_failed", "database_failed"}:
            raise ValueError("Invalid failure status")
        payload = {
            "track": source.track.model_dump(mode="json"),
            "race_date": source.race_date.isoformat(), "race_number": source.race_number,
            "source_url": downloaded.source_url if downloaded else source.chart_url,
            "status": status, "error": error[:2000],
            "content_hash": downloaded.content_hash if downloaded else None,
            "storage_path": path, "raw_text": parsed.raw_text if parsed else None,
            "warnings": parsed.warnings if parsed else [],
        }
        try:
            self.client.rpc("record_chart_attempt", {"p_payload": payload}).execute()
        except Exception as exc:
            raise PersistenceError(
                f"Failed-attempt recording failed ({type(exc).__name__})"
            ) from None
