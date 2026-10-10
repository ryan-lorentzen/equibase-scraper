"""Readable JSON logs suitable for local use and GitHub Actions."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    # SDK debug logs may contain request headers. Do not expose them at DEBUG.
    for name in ("httpx", "httpcore", "supabase", "postgrest", "storage3"):
        logging.getLogger(name).setLevel(logging.WARNING)
