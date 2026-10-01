"""Job records and structured error mapping."""

from __future__ import annotations

from reelmachine.core.errors import (
    ErrorCode,
    JobError,
    ProviderUnavailable,
    VoiceUnavailable,
    to_job_error,
)
from reelmachine.core.job import Job, JobRequest, JobState, JobSummary, Progress


def test_job_round_trips() -> None:
    job = Job(
        id="j1",
        recipe="doodle",
        caller="alice",
        request=JobRequest(recipe="doodle", inputs={"topic": "x"}),
        progress=Progress(stage="narration", stage_index=1, stages_total=5, percent=20.0),
    )
    assert Job.model_validate_json(job.model_dump_json()) == job
    assert not job.is_terminal


def test_summary_is_trimmed_and_names_the_caller() -> None:
    job = Job(
        id="j1",
        recipe="quranic",
        caller="bob",
        state=JobState.FAILED,
        request=JobRequest(recipe="quranic"),
        error=JobError(code=ErrorCode.QUOTA_EXCEEDED, message="no"),
    )
    summary = JobSummary.of(job)
    assert summary.caller == "bob"
    assert summary.error_code == "QUOTA_EXCEEDED"
    assert "request" not in summary.model_dump()


def test_engine_errors_map_to_their_codes() -> None:
    error = to_job_error(VoiceUnavailable("voice gone", hint="configure a voice"))
    assert error.code is ErrorCode.VOICE_UNAVAILABLE
    assert error.hint == "configure a voice"

    error = to_job_error(ProviderUnavailable("no provider"))
    assert error.code is ErrorCode.PROVIDER_UNAVAILABLE


def test_unknown_exceptions_do_not_leak_internals() -> None:
    error = to_job_error(ValueError("secret-path /etc/passwd"))
    assert error.code is ErrorCode.INTERNAL
    assert error.message == "internal error"
    assert "passwd" not in error.message
    assert error.details["type"] == "ValueError"
