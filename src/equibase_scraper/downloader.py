"""PDF verification, conditional fetching, hashing, and atomic local files."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf

from equibase_scraper.discovery import published_pdf_links
from equibase_scraper.http import FetchError, PacedHttp
from equibase_scraper.models import DiscoveredRace


class DownloadError(FetchError):
    pass


def validate_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF-"):
        raise DownloadError("Content is not a PDF (missing PDF signature)")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass or doc.page_count == 0 or doc.is_repaired:
                raise DownloadError("Encrypted, empty, or damaged PDF rejected")
            for page in doc:
                page.get_text("words")
    except DownloadError:
        raise
    except Exception:
        raise DownloadError("PDF structure cannot be read") from None


def pdf_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def storage_path(race: DiscoveredRace, digest: str) -> str:
    return (
        f"{race.race_date:%Y/%m/%d}/{race.track.code}/"
        f"race-{race.race_number}-{digest}.pdf"
    )


def save_local(root: Path, relative: str, data: bytes) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and pdf_hash(target.read_bytes()) == pdf_hash(data):
        return target
    temp: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".part", delete=False) as file:
            temp = file.name
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, target)
    finally:
        if temp and Path(temp).exists():
            Path(temp).unlink()
    return target


@dataclass(frozen=True)
class PreviousDownload:
    source_url: str
    content_hash: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class DownloadResult:
    source_url: str
    content_hash: str
    data: bytes | None
    unchanged: bool
    etag: str | None = None
    last_modified: str | None = None


class Downloader:
    def __init__(self, http: PacedHttp) -> None:
        self.http = http
        self.cache: dict[str, DownloadResult] = {}

    def download(
        self, race: DiscoveredRace, previous: PreviousDownload | None = None,
    ) -> DownloadResult:
        url = race.pdf_url or race.chart_url
        if url in self.cache:
            return self.cache[url]
        headers = {}
        if previous and previous.source_url == url:
            if previous.etag:
                headers["If-None-Match"] = previous.etag
            elif previous.last_modified:
                headers["If-Modified-Since"] = previous.last_modified
        response = self.http.get(url, headers=headers)
        if response.status_code == 304:
            if not previous or not headers:
                raise DownloadError("Unexpected HTTP 304 without a known persisted chart")
            return DownloadResult(url, previous.content_hash, None, True,
                                  previous.etag, previous.last_modified)
        if not response.content.startswith(b"%PDF-"):
            self.http.check_html(response.text, str(response.url))
            if race.pdf_url or urlsplit(url).path.lower().endswith(".pdf"):
                raise DownloadError(f"Published PDF URL returned non-PDF content: {url}")
            links = published_pdf_links(response.text, str(response.url))
            if len(links) != 1:
                raise DownloadError(f"Chart viewer has no unique published PDF target: {url}")
            response = self.http.get(links[0])
        validate_pdf(response.content)
        digest = pdf_hash(response.content)
        result = DownloadResult(
            str(response.url), digest, response.content,
            bool(previous and previous.content_hash == digest),
            response.headers.get("ETag"), response.headers.get("Last-Modified"),
        )
        self.cache[url] = result
        return result
