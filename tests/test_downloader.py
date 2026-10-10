import gzip
from datetime import date

import httpx
import pytest

from equibase_scraper.config import Settings
from equibase_scraper.downloader import (
    Downloader,
    DownloadError,
    PreviousDownload,
    pdf_hash,
    save_local,
)
from equibase_scraper.http import AccessBlocked, FetchError, PacedHttp, RequestLimit
from equibase_scraper.models import DiscoveredRace, Track

URL = "https://tvg.equibase.com/static/chart/pdf/CD092726USA1.pdf"
RACE = DiscoveredRace(track=Track(code="CD"), race_date=date(2026, 9, 27),
                      race_number=1, chart_url=URL, pdf_url=URL,
                      listing_url=URL.replace("1.pdf", ".html"))


def mock_http(handler, **settings):
    return PacedHttp(Settings(scraper_enabled=True, **settings),
                     transport=httpx.MockTransport(handler), sleep=lambda _: None)


def test_valid_pdf_hash_and_duplicate_request(pdf_bytes):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, content=pdf_bytes)

    with mock_http(handler) as http:
        downloader = Downloader(http)
        result = downloader.download(RACE)
        assert result.content_hash == pdf_hash(pdf_bytes)
        assert downloader.download(RACE) == result
        assert len(calls) == 1


def test_compressed_response_is_decoded_exactly_once(pdf_bytes):
    def handler(request):
        return httpx.Response(200, content=gzip.compress(pdf_bytes),
                              headers={"Content-Encoding": "gzip"})

    with mock_http(handler) as http:
        assert Downloader(http).download(RACE).data == pdf_bytes


@pytest.mark.parametrize("body", [b"<html>Error</html>", b"%PDF-corrupt"])
def test_invalid_pdf(body):
    with mock_http(lambda _: httpx.Response(200, content=body)) as http:
        with pytest.raises(DownloadError):
            Downloader(http).download(RACE)


@pytest.mark.parametrize("status,calls", [(404, 1), (500, 4)])
def test_http_errors(status, calls):
    with mock_http(lambda _: httpx.Response(status)) as http:
        with pytest.raises(FetchError):
            Downloader(http).download(RACE)
        assert http.requests == calls


def test_timeout_exhaustion():
    def handler(req):
        raise httpx.ReadTimeout("test", request=req)

    with mock_http(handler, max_retries=2) as http:
        with pytest.raises(FetchError, match="ReadTimeout"):
            Downloader(http).download(RACE)
        assert http.requests == 3


def test_rate_limit_respects_retry_after(pdf_bytes):
    waits = []
    responses = iter([httpx.Response(429, headers={"Retry-After": "8"}),
                      httpx.Response(200, content=pdf_bytes)])
    with mock_http(lambda _: next(responses)) as http:
        http.sleep = waits.append
        assert Downloader(http).download(RACE).data == pdf_bytes
    assert 8 in waits


@pytest.mark.parametrize("headers", [{}, {"Retry-After": "36000"}, {"Retry-After": "NaN"}])
def test_rate_limit_stops_host(headers):
    with mock_http(lambda _: httpx.Response(429, headers=headers)) as http:
        with pytest.raises(AccessBlocked):
            Downloader(http).download(RACE)
        with pytest.raises(AccessBlocked):
            http.get(URL)
        assert http.requests == 1


def test_access_challenge_and_unapproved_redirect_stop():
    with mock_http(lambda _: httpx.Response(403)) as http:
        with pytest.raises(AccessBlocked):
            http.get(URL)
    redirect = lambda _: httpx.Response(302, headers={"Location": "https://evil.test/"})  # noqa: E731
    with mock_http(redirect) as http:
        with pytest.raises(ValueError, match="approved Equibase"):
            http.get(URL)
    with mock_http(lambda _: httpx.Response(200, text="Verify you are human",
                                          headers={"Content-Type": "text/html"})) as http:
        with pytest.raises(AccessBlocked):
            http.get(URL)


def test_conditional_unchanged():
    def handler(req):
        assert req.headers["If-None-Match"] == '"version-one"'
        return httpx.Response(304)

    with mock_http(handler) as http:
        result = Downloader(http).download(RACE, PreviousDownload(URL, "a" * 64, '"version-one"'))
    assert result.unchanged and result.data is None


def test_same_hash_detected_without_validators(pdf_bytes):
    with mock_http(lambda _: httpx.Response(200, content=pdf_bytes)) as http:
        result = Downloader(http).download(RACE, PreviousDownload(URL, pdf_hash(pdf_bytes)))
    assert result.unchanged


def test_request_budget_and_size_limit(pdf_bytes):
    with mock_http(lambda _: httpx.Response(500), max_requests_per_run=1) as http:
        with pytest.raises(RequestLimit):
            http.get(URL)
    with mock_http(lambda _: httpx.Response(200, content=b"x" * 2048),
                   max_pdf_bytes=1024) as http:
        with pytest.raises(FetchError, match="MAX_PDF_BYTES"):
            http.get(URL)


def test_atomic_local_file(tmp_path, pdf_bytes):
    path = save_local(tmp_path, "race.pdf", pdf_bytes)
    assert path.read_bytes() == pdf_bytes
    assert save_local(tmp_path, "race.pdf", pdf_bytes) == path
    assert not list(tmp_path.glob("*.part"))
