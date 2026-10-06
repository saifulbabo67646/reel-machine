"""Structured, actionable errors for the engine.

Every failure a caller can see is a `JobError`: a stable code, a message that says what
went wrong, and a hint that says what to do about it. A stack trace is never a tool result.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    CONCURRENCY_LIMIT = "CONCURRENCY_LIMIT"
    DISK_LIMIT = "DISK_LIMIT"
    VOICE_UNAVAILABLE = "VOICE_UNAVAILABLE"
    SOURCE_UNRESOLVED = "SOURCE_UNRESOLVED"
    ALIGNMENT_FAILED = "ALIGNMENT_FAILED"
    RENDER_FAILED = "RENDER_FAILED"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"
    INTERNAL = "INTERNAL"


class JobError(BaseModel):
    """The caller-visible shape of a failure."""

    code: ErrorCode = ErrorCode.INTERNAL
    message: str = ""
    hint: str = ""
    stage: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ReelError(RuntimeError):
    """Base class for engine failures that map onto a `JobError`."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        hint: str = "",
        stage: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.stage = stage
        self.details: dict[str, Any] = dict(details or {})

    def to_job_error(self) -> JobError:
        return JobError(
            code=type(self).code,
            message=self.message,
            hint=self.hint,
            stage=self.stage,
            details=self.details,
        )


class InvalidInput(ReelError):
    code = ErrorCode.INVALID_INPUT


class NotFound(ReelError):
    code = ErrorCode.NOT_FOUND


class Unauthorized(ReelError):
    code = ErrorCode.UNAUTHORIZED


class ProviderUnavailable(ReelError):
    code = ErrorCode.PROVIDER_UNAVAILABLE


class QuotaExceededError(ReelError):
    code = ErrorCode.QUOTA_EXCEEDED


class ConcurrencyLimit(ReelError):
    code = ErrorCode.CONCURRENCY_LIMIT


class DiskLimit(ReelError):
    code = ErrorCode.DISK_LIMIT


class VoiceUnavailable(ReelError):
    code = ErrorCode.VOICE_UNAVAILABLE


class SourceUnresolved(ReelError):
    code = ErrorCode.SOURCE_UNRESOLVED


class AlignmentFailed(ReelError):
    code = ErrorCode.ALIGNMENT_FAILED


class RenderFailed(ReelError):
    code = ErrorCode.RENDER_FAILED


class PreflightFailed(ReelError):
    code = ErrorCode.PREFLIGHT_FAILED


class VerificationFailed(ReelError):
    code = ErrorCode.VERIFICATION_FAILED


class JobInterrupted(ReelError):
    code = ErrorCode.INTERRUPTED


class JobCancelled(ReelError):
    code = ErrorCode.CANCELLED

    def __init__(self, message: str = "job cancelled") -> None:
        super().__init__(message, hint="the job was cancelled by its caller")


def to_job_error(exc: BaseException) -> JobError:
    """Map any exception onto a caller-visible, actionable error.

    Known engine and provider errors keep their message; anything unknown becomes an
    INTERNAL error whose message does not leak internals (the full detail is logged
    server-side by the runner).
    """
    if isinstance(exc, ReelError):
        return exc.to_job_error()

    name = type(exc).__name__
    try:  # provider errors are mapped without importing them at module scope
        from .. import ffmpeg
        from ..nadeshiko import NadeshikoError, QuotaExceeded as NadeshikoQuotaExceeded
        from ..sources.base import SourceError

        if isinstance(exc, NadeshikoQuotaExceeded):
            return JobError(
                code=ErrorCode.QUOTA_EXCEEDED,
                message=str(exc),
                hint="wait for the monthly quota to reset, or use a different API key",
                details={"type": name},
            )
        if isinstance(exc, NadeshikoError):
            return JobError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message=str(exc),
                hint="check the API key, its READ_MEDIA scope and the network",
                details={"type": name, "status": getattr(exc, "status", None)},
            )
        if isinstance(exc, SourceError):
            return JobError(
                code=ErrorCode.SOURCE_UNRESOLVED,
                message=str(exc),
                hint="run `reel probe` to check the source configuration",
                details={"type": name},
            )
        if isinstance(exc, ffmpeg.FFmpegError):
            return JobError(
                code=ErrorCode.RENDER_FAILED,
                message="ffmpeg could not produce the output",
                hint="check that ffmpeg is on PATH and the inputs are readable",
                details={"type": name, "stderr": getattr(exc, "stderr", "")[-500:]},
            )
    except Exception:  # noqa: BLE001 - mapping must never mask the original failure
        pass

    return JobError(
        code=ErrorCode.INTERNAL,
        message="internal error",
        hint="retry the job; if it keeps failing, report it with the job id",
        details={"type": name},
    )
