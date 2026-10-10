import httpx
import pymupdf
import pytest


@pytest.fixture(autouse=True)
def prohibit_live_http(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Tests must use mock HTTP; live networking is prohibited")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)


@pytest.fixture
def pdf_bytes():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((30, 30), "Synthetic test document")
        return doc.tobytes()
