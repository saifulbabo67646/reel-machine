"""Jobs: plain serializable data, durable and addressable.

A `JobRequest` is what a caller submits; a `Job` is the durable record of what happened.
The engine executes jobs; the CLI and the MCP server only ever produce/consume these
records.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .assets import AssetKind
from .errors import JobError


def now_ms() -> int:
    return int(time.time() * 1000)


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = ""
    stage_index: int = 0
    stages_total: int = 0
    percent: float = 0.0
    detail: str = ""


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: AssetKind = AssetKind.OTHER
    path: str  # absolute path on this host; served by path or via a signed URL
    sha256: str = ""
    bytes: int = 0
    mime: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    licence: dict[str, Any] | None = None


class JobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: list[ArtifactRef] = Field(default_factory=list)
    manifest: str = ""
    render_mode: str = ""
    timeline_digest: str = ""


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    recipe: str
    recipe_version: int = 1
    caller: str = "local"
    state: JobState = JobState.QUEUED
    created_ms: int = Field(default_factory=now_ms)
    started_ms: int = 0
    finished_ms: int = 0
    heartbeat_ms: int = 0
    request: JobRequest
    progress: Progress = Field(default_factory=Progress)
    result: JobResult | None = None
    error: JobError | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED)

    @property
    def duration_ms(self) -> int:
        if not self.started_ms:
            return 0
        end = self.finished_ms or now_ms()
        return end - self.started_ms


class JobSummary(BaseModel):
    """Trimmed view for list endpoints."""

    model_config = ConfigDict(extra="forbid")

    id: str
    recipe: str
    state: JobState
    caller: str = ""
    created_ms: int = 0
    finished_ms: int = 0
    progress: Progress = Field(default_factory=Progress)
    error_code: str = ""

    @classmethod
    def of(cls, job: Job) -> "JobSummary":
        return cls(
            id=job.id,
            recipe=job.recipe,
            state=job.state,
            caller=job.caller,
            created_ms=job.created_ms,
            finished_ms=job.finished_ms,
            progress=job.progress,
            error_code=job.error.code.value if job.error else "",
        )
