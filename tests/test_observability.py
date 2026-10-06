"""Structured logs and secret redaction."""

from __future__ import annotations

import json
import logging

from reelmachine.core.errors import JobError
from reelmachine.observability import JsonFormatter, RedactFilter, redact, secret_values


def test_configured_secret_values_are_discovered(monkeypatch) -> None:
    monkeypatch.setenv("NADESHIKO_API_KEY", "nadeshiko-secret-value")
    monkeypatch.setenv("REEL_MCP_SIGNING_KEY", "signing-secret-value")
    monkeypatch.setenv("UNRELATED", "not-a-secret")
    values = secret_values()
    assert "nadeshiko-secret-value" in values
    assert "signing-secret-value" in values
    assert "not-a-secret" not in values


def test_redact_replaces_every_secret(monkeypatch) -> None:
    monkeypatch.setenv("SOME_API_KEY", "hunter2secret")
    text = redact("failed calling https://x?key=hunter2secret (hunter2secret)")
    assert "hunter2secret" not in text
    assert text.count("***") == 2


def test_log_filter_scrubs_records(monkeypatch) -> None:
    monkeypatch.setenv("SOME_API_KEY", "hunter2secret")
    record = logging.LogRecord(
        "reelmachine", logging.INFO, __file__, 1, "token=hunter2secret", (), None
    )
    assert RedactFilter().filter(record) is True
    assert "hunter2secret" not in record.getMessage()


def test_json_formatter_emits_structured_events() -> None:
    record = logging.LogRecord("reelmachine.engine", logging.INFO, __file__, 1, "stage cached", (), None)
    record.job = "j1"
    record.stage = "select"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "stage cached"
    assert payload["job"] == "j1" and payload["stage"] == "select"
    assert payload["level"] == "INFO"


def test_job_errors_are_redacted_before_they_are_stored(monkeypatch) -> None:
    from reelmachine.engine.executor import _redacted

    monkeypatch.setenv("SOME_API_KEY", "hunter2secret")
    error = _redacted(
        JobError(
            code="INTERNAL",
            message="call failed with hunter2secret",
            hint="refresh hunter2secret",
            details={"url": "https://x?token=hunter2secret"},
        )
    )
    blob = error.message + error.hint + json.dumps(error.details)
    assert "hunter2secret" not in blob
