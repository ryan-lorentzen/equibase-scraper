"""Private, immutable content-hashed chart objects with interruption recovery."""

from storage3.exceptions import StorageApiError
from supabase import Client

from equibase_scraper.database import PersistenceError
from equibase_scraper.downloader import pdf_hash, validate_pdf


class Storage:
    def __init__(self, client: Client, bucket: str) -> None:
        try:
            info = client.storage.get_bucket(bucket)
            if info.public is not False:
                raise PersistenceError("STORAGE_BUCKET must be private")
            self.bucket = client.storage.from_(bucket)
        except PersistenceError:
            raise
        except Exception as exc:
            raise PersistenceError(
                f"Private bucket validation failed ({type(exc).__name__})"
            ) from None

    def exists(self, path: str) -> bool:
        try:
            return self.bucket.exists(path)
        except Exception as exc:
            raise PersistenceError(f"Storage object check failed ({type(exc).__name__})") from None

    def upload(self, path: str, data: bytes, digest: str) -> None:
        validate_pdf(data)
        if pdf_hash(data) != digest or not path.endswith(f"-{digest}.pdf"):
            raise PersistenceError("Storage hash/path does not match verified PDF")
        try:
            self.bucket.upload(path, data, {"content-type": "application/pdf", "upsert": "false"})
        except StorageApiError as exc:
            if str(exc.status) == "409" or exc.code in {"Duplicate", "ResourceAlreadyExists"}:
                # A prior run may have uploaded successfully before the DB transaction failed.
                try:
                    existing = self.bucket.download(path)
                except Exception as download_exc:
                    raise PersistenceError(
                        f"Existing object verification failed ({type(download_exc).__name__})"
                    ) from None
                if pdf_hash(existing) != digest:
                    raise PersistenceError(
                        "Existing storage object has a mismatched hash"
                    ) from None
                return
            raise PersistenceError(f"PDF upload failed (HTTP {exc.status})") from None
        except Exception as exc:
            raise PersistenceError(f"PDF upload failed ({type(exc).__name__})") from None
