"""Structured logging: one JSON object per line, on standard error.

Every process logs the same shape, so logs can be shipped and searched without
parsing prose. Fields passed as `extra=` become keys of the object.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

# What a LogRecord carries by itself. Anything else on a record was passed as `extra=`.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        # default=str: an ID or a timestamp in `extra` must never make logging itself fail.
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Send every log record, including uvicorn's, to standard error as JSON. Idempotent."""
    root = logging.getLogger()
    if not any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        server = logging.getLogger(name)
        server.handlers.clear()
        server.propagate = True
    # One line per request to the model API is noise at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
