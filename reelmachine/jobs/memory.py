"""In-memory job store for tests and for planning without a disk."""

from __future__ import annotations

from pathlib import Path

from ..core.job import Job, JobState


class InMemoryJobStore:
    name = "memory"

    def __init__(self, root: Path | None = None) -> None:
        self._jobs: dict[tuple[str, str], Job] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._root = Path(root) if root else Path("memory-jobs")

    def root(self) -> Path:
        return self._root

    def workdir(self, job_id: str, *, caller: str) -> Path:
        return self._root / caller / job_id

    def create(self, job: Job) -> None:
        key = (job.caller, job.id)
        if key in self._jobs:
            raise FileExistsError(f"job {job.id} already exists for caller {job.caller!r}")
        self.save(job)

    def save(self, job: Job) -> None:
        self._jobs[(job.caller, job.id)] = job.model_copy(deep=True)

    def get(self, job_id: str, *, caller: str) -> Job | None:
        job = self._jobs.get((caller, job_id))
        return job.model_copy(deep=True) if job is not None else None

    def list(
        self,
        *,
        caller: str,
        state: JobState | None = None,
        limit: int = 50,
    ) -> list[Job]:
        jobs = [job for (owner, _), job in self._jobs.items() if owner == caller]
        if state is not None:
            jobs = [job for job in jobs if job.state == state]
        jobs.sort(key=lambda job: job.created_ms, reverse=True)
        return [job.model_copy(deep=True) for job in jobs[:limit]]

    def find_by_idempotency(self, key: str, *, caller: str) -> str | None:
        return self._idempotency.get((caller, key))

    def register_idempotency(self, key: str, job_id: str, *, caller: str) -> None:
        self._idempotency[(caller, key)] = job_id

    def mark_interrupted(self, *, stale_after_ms: int = 10 * 60 * 1000) -> list[str]:  # pragma: no cover
        return []
