"""Structured logging and secret redaction.

Two rules, enforced here so no caller has to remember them: a log line is data
(`REEL_LOG_FORMAT=json`) rather than prose, and a configured secret never reaches a log, a
job record, a manifest or a tool result.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

REDACTED = "***"

#: Environment names that look like secrets. Values are redacted wherever they appear.
SECRET_HINTS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "SIGNING_KEY")


def secret_values(settings: Any = None) -> list[str]:
    """Every configured secret value, longest first (so substrings redact cleanly)."""
    values: set[str] = set()
    for key, value in os.environ.items():
        if any(hint in key.upper() for hint in SECRET_HINTS) and value and len(value) >= 6:
            values.add(value)
    if settings is not None:
        for attribute in ("api_key", "tmdb_api_key"):
            value = getattr(settings, attribute, "")
            if value:
                values.add(str(value))
    return sorted(values, key=len, reverse=True)


def redact(text: str, secrets: list[str] | None = None) -> str:
    secrets = secrets if secrets is not None else secret_values()
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


class RedactFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self.secrets = list(secrets if secrets is not None else secret_values())

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg), self.secrets)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(str(arg), self.secrets) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact(str(value), self.secrets) for key, value in record.args.items()}
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self.secrets)
        return True


class RedactingFormatter(logging.Formatter):
    """Wraps another formatter and redacts the final line — including tracebacks."""

    def __init__(self, inner: logging.Formatter, secrets: list[str] | None = None) -> None:
        super().__init__()
        self.inner = inner
        self.secrets = list(secrets if secrets is not None else secret_values())

    def format(self, record: logging.LogRecord) -> str:
        return redact(self.inner.format(record), self.secrets)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in ("job", "stage", "caller", "provider", "recipe"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)[-800:]
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    level: str | None = None,
    fmt: str | None = None,
    stream: Any = None,
) -> logging.Logger:
    """Configure the engine's logger once; text by default, JSON on request.

    Redaction is attached to both the logger (so every handler sees redacted records)
    and the formatter (so a traceback is redacted too).
    """
    level = (level or os.environ.get("REEL_LOG_LEVEL", "INFO")).upper()
    fmt = (fmt or os.environ.get("REEL_LOG_FORMAT", "text")).lower()
    secrets = secret_values()
    inner: logging.Formatter = (
        JsonFormatter() if fmt == "json" else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(RedactingFormatter(inner, secrets))
    logger = logging.getLogger("reelmachine")
    logger.handlers[:] = [handler]
    logger.filters[:] = [RedactFilter(secrets)]
    logger.setLevel(level)
    logger.propagate = False
    return logger
