"""Execute one job, and the bounded thread pool that runs jobs.

Everything a hosted product must be able to answer afterwards is assembled here: the
manifest, the artifacts, which caller spent what, and the timings of every stage.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import platform
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from ..config import Settings, resolve_output_config
from ..core.assets import AssetKind, AssetStore, digest_file
from ..core.errors import JobCancelled, to_job_error
from ..core.job import (
    ArtifactRef,
    Job,
    JobResult,
    JobState,
    Progress,
    now_ms,
)
from ..core.manifest import (
    AssetRecord,
    ConfigValue,
    Manifest,
    ProviderPin,
    QuotaSpend,
    StageRecord,
    TimelineRef,
)
from ..core.stage import CancellationToken, NullProgress, StageContext
from ..core.timeline import Timeline, timeline_digest
from ..observability import redact
from .providers import ProviderSet, resolve_recipe_providers
from .runner import StageRun, jsonable, run_stages

log = logging.getLogger("reelmachine")


# ------------------------------------------------------------------ environment pins


@lru_cache(maxsize=4)
def _ffmpeg_version(binary: str) -> str:
    import subprocess

    try:
        proc = subprocess.run(
            [binary, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10
        )
        return (proc.stdout or "").splitlines()[0][:120]
    except Exception:  # noqa: BLE001 - a missing ffmpeg is the doctor's business
        return ""


def environment_pins(settings: Settings) -> dict[str, str]:
    from .. import __version__

    return {
        "reelmachine": __version__,
        "python": platform.python_version(),
        "ffmpeg": _ffmpeg_version(settings.ffmpeg),
    }


def ledger_total(path: Path) -> int:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(int(value) for value in payload.values() if isinstance(value, (int, float)))


# ---------------------------------------------------------------------- progress


class JobProgress:
    """Progress that lands in the job record — throttled, so polling stays cheap."""

    def __init__(self, job: Job, store: Any, *, min_interval_s: float = 0.4) -> None:
        self.job = job
        self.store = store
        self._lock = threading.Lock()
        self._last_flush = 0.0
        self._min_interval = min_interval_s

    def stage(self, stage_id: str, index: int, total: int) -> None:
        with self._lock:
            self.job.progress = Progress(
                stage=stage_id,
                stage_index=index,
                stages_total=total,
                percent=round((index - 1) / max(1, total) * 100.0, 1),
            )
            self._flush(force=True)

    def percent(self, value: float) -> None:
        with self._lock:
            self.job.progress.percent = float(value)
            self._flush()

    def detail(self, text: str) -> None:
        with self._lock:
            self.job.progress.detail = str(text)[:400]
            self._flush()

    def heartbeat(self) -> None:
        with self._lock:
            self.job.heartbeat_ms = now_ms()
            self._flush(force=True)

    def finish(self, percent: float = 100.0) -> None:
        with self._lock:
            self.job.progress.percent = percent
            self.job.progress.detail = ""
            self._flush(force=True)

    def _flush(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_flush < self._min_interval:
            return
        self._last_flush = now
        try:
            self.store.save(self.job)
        except Exception:  # noqa: BLE001 - progress must never fail a job
            log.warning("could not persist progress for job %s", self.job.id)


# ------------------------------------------------------------------- job execution


@dataclass(slots=True)
class JobContext:
    """Everything `run_job` needs that is not the job itself."""

    store: Any
    settings: Settings
    registry: Any = None
    provider_overrides: dict[str, str] | None = None


def run_job(
    job: Job,
    recipe: Any,
    inputs: Any,
    *,
    store: Any,
    base_settings: Settings,
    registry: Any = None,
    provider_overrides: dict[str, str] | None = None,
    cancel: CancellationToken | None = None,
) -> Job:
    cancel = cancel or CancellationToken()
    workdir = store.workdir(job.id, caller=job.caller)
    workdir.mkdir(parents=True, exist_ok=True)

    job.state = JobState.RUNNING
    job.started_ms = now_ms()
    job.heartbeat_ms = job.started_ms
    store.save(job)
    progress = JobProgress(job, store)
    progress.heartbeat()

    settings, provenance = resolve_output_config(recipe.spec.id, base_settings, job.request.config)
    settings = dataclasses.replace(
        settings,
        workdir=workdir,
        outdir=workdir / "artifacts",
        ledger_file=store.root() / job.caller / "quota" / "nadeshiko.json",
    )

    providers = ProviderSet()
    outputs: dict[str, Any] = {}
    try:
        providers = resolve_recipe_providers(
            recipe.spec, settings, overrides=provider_overrides, registry=registry
        )
        cancel.raise_if_cancelled()

        ledger_path = settings.ledger_path
        quota_before = ledger_total(ledger_path)

        runs = run_stages(
            recipe,
            inputs,
            workdir=workdir / "run",
            assets_root=workdir / "assets",
            providers=providers,
            config=settings,
            progress=progress,
            cancel=cancel,
            cache_root=store.root() / job.caller / "cache" / "stages",
            job=job,
            log=log,
        )
        outputs.update({run.id: run.output for run in runs})

        manifest = _build_manifest(
            job,
            recipe,
            inputs,
            runs,
            outputs,
            settings=settings,
            provenance=provenance,
            providers=providers,
            ledger_path=ledger_path,
            quota_before=quota_before,
            workdir=workdir,
            cancel=cancel,
        )
        manifest_path = workdir / "manifest.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

        artifacts = _collect_artifacts(outputs, workdir)
        job.result = JobResult(
            artifacts=artifacts,
            manifest=str(manifest_path),
            render_mode=manifest.render_mode,
            timeline_digest=manifest.timeline.digest,
        )
        job.progress.stage = "done"
        job.state = JobState.SUCCEEDED
        progress.finish(100.0)
    except JobCancelled:
        job.result = _partial_result(outputs, workdir)
        job.state = JobState.CANCELLED
        job.error = _redacted(to_job_error(JobCancelled()))
    except BaseException as exc:  # noqa: BLE001 - every failure becomes a structured error
        log.exception("job %s failed", job.id)
        job.result = _partial_result(outputs, workdir)
        job.state = JobState.FAILED
        job.error = _redacted(to_job_error(exc))
    finally:
        providers.close()
        job.finished_ms = now_ms()
        job.heartbeat_ms = job.finished_ms
        store.save(job)
    return job


def _build_manifest(
    job: Job,
    recipe: Any,
    inputs: Any,
    runs: list[StageRun],
    outputs: dict[str, Any],
    *,
    settings: Settings,
    provenance: dict[str, dict[str, Any]],
    providers: ProviderSet,
    ledger_path: Path,
    quota_before: int,
    workdir: Path,
    cancel: CancellationToken,
) -> Manifest:
    manifest = Manifest(
        job_id=job.id,
        recipe=recipe.spec.id,
        recipe_version=recipe.spec.version,
        created_ms=now_ms(),
        caller=job.caller,
        inputs=jsonable(inputs) if not isinstance(inputs, dict) else dict(inputs),
        config={
            key: ConfigValue(value=entry.get("value"), source=str(entry.get("source", "default")))
            for key, entry in provenance.items()
        },
        environment=environment_pins(settings),
    )

    timeline: Timeline | None = None
    prep = outputs.get("prep")
    compose = outputs.get("compose")
    if prep is not None and getattr(prep, "timeline", None) is not None:
        timeline = prep.timeline
    elif compose is not None and getattr(compose, "timeline", None) is not None:
        timeline = compose.timeline
    render = outputs.get("render")
    manifest.render_mode = getattr(render, "render_mode", "") or (
        timeline.render_mode if timeline is not None else ""
    )
    if timeline is not None:
        manifest.timeline = TimelineRef(
            digest=timeline_digest(timeline),
            artifact="timeline.json",
            duration_ms=timeline.duration_ms,
            render_mode=timeline.render_mode,
        )

    store = AssetStore(workdir / "assets")
    for asset in store.all():
        manifest.add_asset(asset)

    for run in runs:
        manifest.stages.append(
            StageRecord(
                id=run.id,
                index=run.index,
                revision=run.revision,
                cached=run.cached,
                started_ms=run.started_ms,
                finished_ms=run.finished_ms,
                duration_ms=run.duration_ms,
                cache_key=run.cache_key,
                output_hash=run.output_hash,
            )
        )
    for role in recipe.spec.providers:
        manifest.pin(role, providers.pin(role))

    spent = max(0, ledger_total(ledger_path) - quota_before)
    if spent:
        manifest.quota["nadeshiko"] = QuotaSpend(
            provider="nadeshiko", unit="requests", units=spent, caller=job.caller
        )

    verify = getattr(recipe, "verify", None)
    if callable(verify):
        ctx = StageContext(
            job=job,
            workdir=workdir,
            assets=store,
            config=settings,
            providers=providers,
            progress=NullProgress(),
            cancel=cancel,
            log=log,
        )
        try:
            manifest.verification = verify(ctx, outputs)
        except Exception as exc:  # noqa: BLE001 - a failed check is a result, not a crash
            from ..core.manifest import CheckResult, VerificationReport

            manifest.verification = VerificationReport(
                ok=False,
                render_mode=manifest.render_mode,
                checks=[CheckResult(name="verify", ok=False, detail=str(exc)[:300])],
            )
    return manifest


def _redacted(error: Any) -> Any:
    """A caller-visible error must never carry a configured secret."""
    error.message = redact(str(error.message))
    error.hint = redact(str(error.hint))
    if isinstance(error.details, dict):
        error.details = {
            key: redact(value) if isinstance(value, str) else value
            for key, value in error.details.items()
        }
    return error


def _partial_result(outputs: dict[str, Any], workdir: Path) -> JobResult | None:
    """What a failed or cancelled job still produced — the plan and timeline so far."""
    if not outputs:
        return None
    try:
        artifacts = _collect_artifacts(outputs, workdir)
    except Exception:  # noqa: BLE001 - partial results are best-effort
        return None
    prep = outputs.get("prep")
    compose = outputs.get("compose")
    timeline = None
    if prep is not None and getattr(prep, "timeline", None) is not None:
        timeline = prep.timeline
    elif compose is not None and getattr(compose, "timeline", None) is not None:
        timeline = compose.timeline
    render = outputs.get("render")
    return JobResult(
        artifacts=artifacts,
        render_mode=getattr(render, "render_mode", "") or (timeline.render_mode if timeline else ""),
        timeline_digest=timeline_digest(timeline) if timeline is not None else "",
    )


def _artifact(name: str, path: Path, kind: AssetKind) -> ArtifactRef:
    path = Path(path)
    return ArtifactRef(
        name=name,
        kind=kind,
        path=str(path),
        sha256=digest_file(path) if path.is_file() else "",
        bytes=path.stat().st_size if path.is_file() else 0,
    )


def _collect_artifacts(outputs: dict[str, Any], workdir: Path) -> list[ArtifactRef]:
    artifacts: list[ArtifactRef] = []
    render = outputs.get("render")
    if render is not None:
        artifacts.append(_artifact("reel.mp4", Path(render.video), AssetKind.VIDEO))
        artifacts.append(_artifact("captions.ass", Path(render.ass), AssetKind.SUBTITLE))
        artifacts.append(_artifact("captions.srt", Path(render.srt), AssetKind.SUBTITLE))
        artifacts.append(_artifact("reel.json", Path(render.manifest), AssetKind.REPORT))

    artifact_dir = workdir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    compose = outputs.get("compose")
    if compose is not None and getattr(compose, "plan", None):
        path = artifact_dir / "plan.json"
        path.write_text(json.dumps(compose.plan, indent=2, ensure_ascii=False), encoding="utf-8")
        artifacts.append(_artifact("plan.json", path, AssetKind.PLAN))

    prep = outputs.get("prep")
    if prep is not None and getattr(prep, "timeline", None) is not None:
        path = artifact_dir / "timeline.json"
        path.write_text(prep.timeline.model_dump_json(indent=2), encoding="utf-8")
        artifacts.append(_artifact("timeline.json", path, AssetKind.TIMELINE))

    manifest_path = workdir / "manifest.json"
    if manifest_path.is_file():
        artifacts.append(_artifact("manifest.json", manifest_path, AssetKind.MANIFEST))
    return artifacts


# ----------------------------------------------------------------------- the runner


class JobRunner:
    """A bounded thread pool: local-first, with per-caller cancellation and limits."""

    def __init__(
        self,
        *,
        store: Any,
        base_settings: Settings,
        registry: Any = None,
        provider_overrides: dict[str, str] | None = None,
        max_workers: int = 2,
    ) -> None:
        self._store = store
        self._settings = base_settings
        self._registry = registry
        self._overrides = dict(provider_overrides or {})
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="reel-job")
        self._lock = threading.Lock()
        self._active: dict[str, tuple[str, CancellationToken, Future[Any] | None]] = {}

    def submit(self, job: Job, recipe: Any, inputs: Any) -> None:
        token = CancellationToken()
        with self._lock:
            self._active[job.id] = (job.caller, token, None)
        future = self._executor.submit(self._execute, job, recipe, inputs, token)
        with self._lock:
            self._active[job.id] = (job.caller, token, future)

    def _execute(self, job: Job, recipe: Any, inputs: Any, token: CancellationToken) -> None:
        try:
            run_job(
                job,
                recipe,
                inputs,
                store=self._store,
                base_settings=self._settings,
                registry=self._registry,
                provider_overrides=self._overrides,
                cancel=token,
            )
        finally:
            with self._lock:
                self._active.pop(job.id, None)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            entry = self._active.get(job_id)
        if entry is None:
            return False
        entry[1].cancel()
        return True

    def running_count(self, caller: str | None = None) -> int:
        with self._lock:
            entries = list(self._active.values())
        if caller is None:
            return len(entries)
        return sum(1 for owner, _, _ in entries if owner == caller)

    def wait_idle(self, timeout_s: float = 30.0) -> bool:
        """Block until nothing is running (used by tests and by shutdown)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.running_count() == 0:
                return True
            time.sleep(0.05)
        return self.running_count() == 0

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
