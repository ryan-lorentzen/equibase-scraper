from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pglast
import pytest
from pdf_factory import chart_pdf
from storage3.exceptions import StorageApiError
from test_parser import SOURCE

from equibase_scraper.database import Database, PersistenceError
from equibase_scraper.downloader import DownloadResult, pdf_hash, storage_path
from equibase_scraper.parser import parse_chart
from equibase_scraper.storage import Storage


def sample():
    data = chart_pdf()
    return parse_chart(data, SOURCE), DownloadResult(SOURCE.chart_url, pdf_hash(data), data, False)


def client_mock():
    client = MagicMock()
    client.storage.get_bucket.return_value = SimpleNamespace(public=False)
    return client


@pytest.mark.parametrize("outcome", ["saved", "unchanged"])
def test_atomic_save_and_existing_upsert_outcome(outcome):
    client = client_mock()
    client.rpc.return_value.execute.return_value.data = {"outcome": outcome}
    race, download = sample()
    path = storage_path(SOURCE, download.content_hash)
    assert Database(client).save(race, download, path) == outcome
    assert client.rpc.call_count == 1
    name, parameters = client.rpc.call_args.args
    assert name == "save_chart"
    assert len(parameters["p_payload"]["entries"]) == 2
    client.table.assert_not_called()


def test_incomplete_output_never_sent_to_save():
    client = client_mock()
    race, download = sample()
    race.complete = False
    with pytest.raises(PersistenceError, match="Incomplete"):
        Database(client).save(race, download, "path")
    client.rpc.assert_not_called()


def test_failed_attempt_separate_from_valid_snapshot():
    client = client_mock()
    Database(client).failed(SOURCE, "incomplete", "unsupported layout")
    assert client.rpc.call_args.args[0] == "record_chart_attempt"
    payload = client.rpc.call_args.args[1]["p_payload"]
    assert payload["content_hash"] is None
    assert payload["status"] == "incomplete"
    client.table.assert_not_called()


def test_interrupted_database_save_not_success_and_no_secret_leak():
    client = client_mock()
    client.rpc.return_value.execute.side_effect = RuntimeError("secret-value-in-sdk-message")
    race, download = sample()
    with pytest.raises(PersistenceError) as error:
        Database(client).save(race, download, "path")
    assert "secret-value" not in str(error.value)


def test_modified_chart_payload_contains_new_hash():
    client = client_mock()
    client.rpc.return_value.execute.return_value.data = {"outcome": "saved"}
    race, download = sample()
    Database(client).save(race, download, storage_path(SOURCE, download.content_hash))
    assert client.rpc.call_args.args[1]["p_payload"]["content_hash"] == download.content_hash


def test_upload_private_and_duplicate_recovery(pdf_bytes):
    client = client_mock()
    storage = Storage(client, "race-charts")
    digest = pdf_hash(pdf_bytes)
    path = storage_path(SOURCE, digest)
    storage.upload(path, pdf_bytes, digest)
    bucket = client.storage.from_.return_value
    assert bucket.upload.call_args.args[2]["upsert"] == "false"
    bucket.upload.side_effect = StorageApiError("exists", "Duplicate", 409)
    bucket.download.return_value = pdf_bytes
    storage.upload(path, pdf_bytes, digest)
    bucket.download.return_value = b"corrupted object"
    with pytest.raises(PersistenceError, match="mismatched hash"):
        storage.upload(path, pdf_bytes, digest)


def test_upload_failure_and_public_bucket_rejected(pdf_bytes):
    client = client_mock()
    client.storage.get_bucket.return_value = SimpleNamespace(public=True)
    with pytest.raises(PersistenceError, match="private"):
        Storage(client, "race-charts")
    client.storage.get_bucket.return_value = SimpleNamespace(public=False)
    client.storage.from_.return_value.upload.side_effect = StorageApiError("secret", "error", 500)
    digest = pdf_hash(pdf_bytes)
    with pytest.raises(PersistenceError, match="upload failed"):
        Storage(client, "race-charts").upload(storage_path(SOURCE, digest), pdf_bytes, digest)


def test_migration_sql_and_plpgsql_syntax():
    migration = Path("database/migrations/001_initial_schema.sql").read_text()
    assert pglast.parse_sql(migration)
    assert pglast.parse_plpgsql(migration)
