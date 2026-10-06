"""Local filesystem job store — the default, and deliberately the only real one."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from ..core.job import Job, JobState, now_ms

_JOB_FILE = "job.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class LocalJobStore:
    name = "local"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # -- paths -----------------------------------------------------------------
    def root(self) -> Path:
        return self._root

    def _caller_dir(self, caller: str) -> Path:
        return self._root / _safe(caller)

    def workdir(self, job_id: str, *, caller: str) -> Path:
        return self._caller_dir(caller) / _safe(job_id)

    def _job_path(self, job_id: str, *, caller: str) -> Path:
        return self.workdir(job_id, caller=caller) / _JOB_FILE

    # -- records ---------------------------------------------------------------
    def create(self, job: Job) -> None:
        path = self._job_path(job.id, caller=job.caller)
        if path.exists():
            raise FileExistsError(f"job {job.id} already exists for caller {job.caller!r}")
        self.save(job)

    def save(self, job: Job) -> None:
        _atomic_write(
            self._job_path(job.id, caller=job.caller),
            job.model_dump_json(indent=2),
        )

    def get(self, job_id: str, *, caller: str) -> Job | None:
        path = self._job_path(job_id, caller=caller)
        if not path.is_file():
            return None
        return Job.model_validate_json(path.read_text(encoding="utf-8"))

    def list(
        self,
        *,
        caller: str,
        state: JobState | None = None,
        limit: int = 50,
    ) -> list[Job]:
        caller_dir = self._caller_dir(caller)
        if not caller_dir.is_dir():
            return []
        jobs: list[Job] = []
        for path in caller_dir.glob(f"*/{_JOB_FILE}"):
            try:
                job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if state is not None and job.state != state:
                continue
            jobs.append(job)
        jobs.sort(key=lambda job: job.created_ms, reverse=True)
        return jobs[:limit]

    def every_caller(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return [
            path.name
            for path in sorted(self._root.iterdir())
            if path.is_dir() and not path.name.startswith("_")
        ]

    # -- idempotency -----------------------------------------------------------
    def _idempotency_path(self, key: str, *, caller: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self._caller_dir(caller) / "_idempotency" / f"{digest}.json"

    def find_by_idempotency(self, key: str, *, caller: str) -> str | None:
        path = self._idempotency_path(key, caller=caller)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if payload.get("key") != key:
            return None
        job_id = payload.get("job_id")
        return str(job_id) if job_id else None

    def register_idempotency(self, key: str, job_id: str, *, caller: str) -> None:
        _atomic_write(
            self._idempotency_path(key, caller=caller),
            json.dumps({"key": key, "job_id": job_id}, ensure_ascii=False),
        )

    # -- maintenance -----------------------------------------------------------
    def disk_usage_bytes(self, caller: str) -> int:
        caller_dir = self._caller_dir(caller)
        if not caller_dir.is_dir():
            return 0
        total = 0
        for path in caller_dir.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def mark_interrupted(self, *, stale_after_ms: int = 10 * 60 * 1000) -> list[str]:
        """Fail jobs whose process died mid-run (a heartbeat that stopped).

        The stage cache means re-submitting the same idempotency key resumes instead of
        repeating the work.
        """
        recovered: list[str] = []
        cutoff = now_ms() - stale_after_ms
        for caller in self.every_caller():
            for job in self.list(caller=caller, state=JobState.RUNNING, limit=10_000):
                if job.heartbeat_ms and job.heartbeat_ms < cutoff:
                    job.state = JobState.FAILED
                    job.finished_ms = now_ms()
                    from ..core.errors import ErrorCode, JobError

                    job.error = JobError(
                        code=ErrorCode.INTERRUPTED,
                        message="the process running this job stopped",
                        hint="re-submit the same idempotency key to resume from its cached stages",
                    )
                    self.save(job)
                    recovered.append(job.id)
        return recovered

    def delete(self, job_id: str, *, caller: str) -> None:
        shutil.rmtree(self.workdir(job_id, caller=caller), ignore_errors=True)


def _safe(token: str) -> str:
    cleaned = str(token).strip().replace("/", "_").replace("..", "_")
    return cleaned or "_"
