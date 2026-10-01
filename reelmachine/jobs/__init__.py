"""Job storage.

A job is durable, addressable data: `jobs/<caller>/<job_id>/job.json`, written atomically,
with its work directory beside it. The interface is the seam a different backend would
implement; the default is the local filesystem, and there is deliberately no database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..core.job import Job, JobState


@runtime_checkable
class JobStore(Protocol):
    def create(self, job: Job) -> None: ...

    def save(self, job: Job) -> None: ...

    def get(self, job_id: str, *, caller: str) -> Job | None: ...

    def list(
        self,
        *,
        caller: str,
        state: JobState | None = None,
        limit: int = 50,
    ) -> list[Job]: ...

    def find_by_idempotency(self, key: str, *, caller: str) -> str | None: ...

    def register_idempotency(self, key: str, job_id: str, *, caller: str) -> None: ...

    def workdir(self, job_id: str, *, caller: str) -> Path: ...

    def root(self) -> Path: ...


__all__ = ["JobStore"]
