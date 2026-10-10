"""One sequential request budget for discovery, redirects, retries, and downloads."""

import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from equibase_scraper.config import Settings, validate_source_url

logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


class AccessBlocked(FetchError):
    pass


class RequestLimit(FetchError):
    pass


class PacedHttp:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.sleep = sleep
        self.clock = clock
        self.requests = 0
        self.last_request: float | None = None
        self.blocked_hosts: set[str] = set()
        self.client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def __enter__(self) -> "PacedHttp":
        return self

    def __exit__(self, *args: object) -> None:
        self.client.close()

    def _pace(self, url: str) -> None:
        if not self.settings.scraper_enabled:
            raise FetchError("Remote scraping disabled: SCRAPER_ENABLED=false")
        validate_source_url(url)
        if urlsplit(url).hostname in self.blocked_hosts:
            raise AccessBlocked(f"Stopped using blocked host: {url}")
        if self.requests >= self.settings.max_requests_per_run:
            raise RequestLimit(f"MAX_REQUESTS_PER_RUN reached before: {url}")
        if self.last_request is not None:
            remaining = self.settings.request_delay_seconds - (self.clock() - self.last_request)
            if remaining > 0:
                self.sleep(remaining)
        self.last_request = self.clock()
        self.requests += 1

    def _block(self, url: str, message: str) -> None:
        self.blocked_hosts.add(str(urlsplit(url).hostname))
        raise AccessBlocked(f"{message}: {url}")

    def _retry_wait(self, response: httpx.Response | None, attempt: int) -> float:
        delay = max(self.settings.request_delay_seconds, 2**attempt)
        if response is not None and response.headers.get("Retry-After"):
            raw = response.headers["Retry-After"]
            try:
                seconds = float(raw)
            except ValueError:
                try:
                    dt = parsedate_to_datetime(raw)
                    seconds = (dt - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError):
                    seconds = self.settings.max_retry_wait_seconds + 1
            if not math.isfinite(seconds):
                seconds = self.settings.max_retry_wait_seconds + 1
            delay = max(delay, seconds)
        return delay

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        redirects = 0
        attempt = 0
        while True:
            self._pace(url)
            response = None
            try:
                # Stream reads are bounded even if Content-Length is absent or dishonest.
                with self.client.stream("GET", url, headers=headers) as stream:
                    chunks = bytearray()
                    for chunk in stream.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > self.settings.max_pdf_bytes:
                            raise FetchError(f"Response exceeds MAX_PDF_BYTES: {url}")
                    decoded_headers = httpx.Headers(stream.headers)
                    # iter_bytes() already decoded compression. Keeping Content-Encoding
                    # would make the reconstructed response decompress the same bytes twice.
                    decoded_headers.pop("Content-Encoding", None)
                    decoded_headers.pop("Content-Length", None)
                    response = httpx.Response(
                        stream.status_code,
                        headers=decoded_headers,
                        content=bytes(chunks),
                        request=stream.request,
                    )
            except httpx.RequestError as exc:
                if attempt >= self.settings.max_retries:
                    raise FetchError(f"Connection failed ({type(exc).__name__}): {url}") from None
            if response is not None:
                logger.debug("Source response: HTTP %s %s", response.status_code, url)
                if response.status_code in {401, 403}:
                    self._block(url, f"Access denied (HTTP {response.status_code})")
                if response.status_code == 304:
                    return response
                if response.is_redirect:
                    redirects += 1
                    if redirects > 5 or not response.headers.get("Location"):
                        raise FetchError(f"Invalid or excessive redirects: {url}")
                    url = validate_source_url(urljoin(url, response.headers["Location"]))
                    continue
                if response.status_code not in {429, 500, 502, 503, 504}:
                    if response.is_error:
                        raise FetchError(f"HTTP {response.status_code}: {url}")
                    if "html" in response.headers.get("Content-Type", "").lower():
                        self.check_html(response.text, url)
                    return response
                if attempt >= self.settings.max_retries:
                    if response.status_code == 429:
                        self._block(url, "Rate limit retry budget exhausted")
                    raise FetchError(f"HTTP {response.status_code}; retries exhausted: {url}")
                if response.status_code == 429 and not response.headers.get("Retry-After"):
                    self._block(url, "Rate limited without Retry-After; stopping")
            delay = self._retry_wait(response, attempt)
            if delay > self.settings.max_retry_wait_seconds:
                self._block(url, "Retry-After exceeds configured maximum wait; stopping")
            logger.warning("Retrying source after %.1f seconds: %s", delay, url)
            self.sleep(delay)
            attempt += 1

    def check_html(self, html: str, url: str) -> None:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True).lower()
        if any(
            phrase in text
            for phrase in (
                "verify you are human", "access denied", "captcha", "checking your browser",
                "just a moment", "enable javascript and cookies to continue",
            )
        ):
            self._block(url, "Access challenge detected; stopping")
