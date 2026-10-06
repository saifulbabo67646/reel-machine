"""The engine: how a recipe is executed, and the API both clients use.

This package owns stage execution and job orchestration. It must not import CLI or MCP
concerns.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from ..config import Settings, get_settings
from ..core.errors import (
    ConcurrencyLimit,
    DiskLimit,
    InvalidInput,
    NotFound,
    ProviderUnavailable,
    QuotaExceededError,
)
from ..core.job import ArtifactRef, CallerPolicy, Job, JobRequest, JobState, JobSummary
from ..core.recipe import ProbeContext, ProbeReport, instantiate_recipe
from ..core.registry import Registry
from ..jobs.local import LocalJobStore
from .executor import JobRunner, ledger_total
from .providers import ProviderSet, provider_health, resolve_recipe_providers
from .runner import StageRun, config_subset, run_stages

__all__ = [
    "Engine",
    "StageRun",
    "config_subset",
    "provider_health",
    "resolve_recipe_providers",
    "run_stages",
]


def _new_job_id() -> str:
    return uuid.uuid4().hex[:12]


class Engine:
    """The library both the CLI and the MCP server are clients of."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: Any = None,
        registry: Registry | None = None,
        provider_overrides: dict[str, str] | None = None,
        policy_for: Callable[[str], CallerPolicy] | None = None,
        max_workers: int = 2,
        recover: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or Registry()
        self.provider_overrides = dict(provider_overrides or {})
        self.store = store if store is not None else LocalJobStore(self.settings.workdir / "jobs")
        self._policy_for = policy_for or (lambda caller: CallerPolicy())
        self._runner = JobRunner(
            store=self.store,
            base_settings=self.settings,
            registry=self.registry,
            provider_overrides=self.provider_overrides,
            max_workers=max_workers,
        )
        if recover:
            mark = getattr(self.store, "mark_interrupted", None)
            if callable(mark):
                mark()

    # ---------------------------------------------------------------- catalogue

    def list_recipes(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for result in self.registry.load_all("recipes").values():
            if not result.ok:
                continue
            recipe = instantiate_recipe(result.value)
            spec = recipe.spec
            rows.append(
                {
                    "id": spec.id,
                    "title": spec.title,
                    "summary": spec.summary,
                    "renderModes": list(spec.render_modes),
                    "artifacts": list(spec.artifacts),
                    "cost": {
                        "unit": spec.cost.unit,
                        "estimate": spec.cost.estimate,
                        "notes": spec.cost.notes,
                        "providers": list(spec.cost.providers),
                    },
                    "schemaVersion": spec.schema_version,
                }
            )
        rows.sort(key=lambda row: row["id"])
        return rows

    def get_recipe(self, recipe_id: str) -> Any:
        try:
            return instantiate_recipe(self.registry.load("recipes", recipe_id))
        except ProviderUnavailable as exc:
            raise NotFound(
                f"no recipe {recipe_id!r}",
                hint="call list_recipes to see what is installed",
                details={"recipe": recipe_id},
            ) from exc

    def describe(self, recipe_id: str) -> dict[str, Any]:
        recipe = self.get_recipe(recipe_id)
        spec = recipe.spec
        health = {
            (row["group"], row["name"]): row for row in provider_health(self.registry, self.settings)
        }
        providers: dict[str, Any] = {}
        for role, req in spec.providers.items():
            default = req.default or getattr(self.settings, "source", "")
            row = health.get((req.group, default), {})
            providers[role] = {
                "group": req.group,
                "default": default,
                "required": req.required,
                "description": req.description,
                "status": row.get("status", "ok") if row else "unknown",
                "detail": row.get("detail", "") if row else "",
            }
        return {
            "id": spec.id,
            "title": spec.title,
            "summary": spec.summary,
            "schemaVersion": spec.schema_version,
            "inputSchema": spec.schema(),
            "example": dict(spec.example),
            "providers": providers,
            "cost": {
                "unit": spec.cost.unit,
                "estimate": spec.cost.estimate,
                "notes": spec.cost.notes,
                "providers": list(spec.cost.providers),
            },
            "renderModes": list(spec.render_modes),
            "artifacts": list(spec.artifacts),
        }

    def _inputs_for(self, recipe: Any, raw: dict[str, Any]) -> Any:
        try:
            return recipe.spec.input_model.model_validate(raw)
        except ValidationError as exc:
            raise InvalidInput(
                "the job inputs do not match the recipe's schema",
                hint=f"call describe_recipe({recipe.spec.id!r}) for the schema and an example",
                details={"errors": exc.errors(include_url=False, include_input=False)[:8]},
            ) from exc

    # ------------------------------------------------------------------ probing

    def probe(self, recipe_id: str, inputs: dict[str, Any], *, caller: str = "local") -> ProbeReport:
        recipe = self.get_recipe(recipe_id)
        model = self._inputs_for(recipe, inputs)
        providers = resolve_recipe_providers(
            recipe.spec, self.settings, overrides=self.provider_overrides, registry=self.registry
        )
        try:
            ctx = ProbeContext(config=self.settings, providers=providers)
            return recipe.probe(model, ctx)
        finally:
            providers.close()

    # -------------------------------------------------------------------- jobs

    def submit(self, request: JobRequest, *, caller: str = "local") -> Job:
        recipe = self.get_recipe(request.recipe)
        inputs = self._inputs_for(recipe, request.inputs)
        policy = self._policy_for(caller)
        if policy.allowed_recipes is not None and recipe.spec.id not in policy.allowed_recipes:
            raise InvalidInput(
                f"recipe {recipe.spec.id!r} is not enabled for this caller",
                hint="ask the deployment to allow it",
                details={"recipe": recipe.spec.id, "caller": caller},
            )
        if request.idempotency_key:
            existing_id = self.store.find_by_idempotency(request.idempotency_key, caller=caller)
            if existing_id:
                existing = self.store.get(existing_id, caller=caller)
                if existing is not None:
                    return existing
        self._enforce_limits(policy, caller)
        job = Job(
            id=_new_job_id(),
            recipe=recipe.spec.id,
            recipe_version=recipe.spec.version,
            caller=caller,
            request=request,
        )
        self.store.create(job)
        if request.idempotency_key:
            self.store.register_idempotency(request.idempotency_key, job.id, caller=caller)
        self._runner.submit(job, recipe, inputs)
        return job

    def _enforce_limits(self, policy: CallerPolicy, caller: str) -> None:
        if self._runner.running_count(caller) >= policy.max_concurrent_jobs:
            raise ConcurrencyLimit(
                f"caller {caller!r} already has {policy.max_concurrent_jobs} job(s) running",
                hint="wait for one to finish, or raise the caller's limit",
                details={"caller": caller, "limit": policy.max_concurrent_jobs},
            )
        if policy.max_disk_mb:
            used = self._disk_usage(caller)
            limit = policy.max_disk_mb * 1024 * 1024
            if used >= limit:
                raise DiskLimit(
                    f"caller {caller!r} is using {used / 1e6:.0f} MB of its {policy.max_disk_mb} MB budget",
                    hint="delete old jobs or raise the caller's disk limit",
                    details={"caller": caller, "usedBytes": used, "limitBytes": limit},
                )
        for provider, limit in policy.quotas.items():
            path = self.store.root() / caller / "quota" / f"{provider}.json"
            if ledger_total(path) >= limit:
                raise QuotaExceededError(
                    f"the {provider} quota for caller {caller!r} is exhausted",
                    hint="wait for the quota to reset or raise the caller's limit",
                    details={"caller": caller, "provider": provider, "limit": limit},
                )

    def _disk_usage(self, caller: str) -> int:
        usage = getattr(self.store, "disk_usage_bytes", None)
        if callable(usage):
            return int(usage(caller))
        root = self.store.root() / caller
        if not root.is_dir():
            return 0
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())

    def get(self, job_id: str, *, caller: str = "local") -> Job:
        job = self.store.get(job_id, caller=caller)
        if job is None:
            raise NotFound(
                f"no job {job_id!r} for this caller",
                hint="list_jobs shows the jobs this caller owns",
                details={"jobId": job_id},
            )
        return job

    def list(
        self,
        *,
        caller: str = "local",
        state: JobState | None = None,
        limit: int = 50,
    ) -> list[JobSummary]:
        return [JobSummary.of(job) for job in self.store.list(caller=caller, state=state, limit=limit)]

    def cancel(self, job_id: str, *, caller: str = "local") -> Job:
        job = self.get(job_id, caller=caller)
        if job.is_terminal:
            return job
        if not self._runner.cancel(job_id):
            # queued but not started, or a leftover record from a dead process
            job.state = JobState.CANCELLED
            job.finished_ms = max(job.finished_ms, job.created_ms)
            self.store.save(job)
        return self.get(job_id, caller=caller)

    def wait(
        self,
        job_id: str,
        *,
        caller: str = "local",
        on_progress: Callable[[Job], None] | None = None,
        timeout_s: float | None = None,
        poll_s: float = 0.2,
    ) -> Job:
        deadline = time.monotonic() + timeout_s if timeout_s else None
        while True:
            job = self.get(job_id, caller=caller)
            if on_progress is not None:
                on_progress(job)
            if job.is_terminal:
                return job
            if deadline is not None and time.monotonic() >= deadline:
                return job
            time.sleep(poll_s)

    def artifact(self, job_id: str, name: str, *, caller: str = "local") -> ArtifactRef:
        job = self.get(job_id, caller=caller)
        for artifact in (job.result.artifacts if job.result else []):
            if artifact.name == name:
                return artifact
        raise NotFound(
            f"job {job_id!r} has no artifact {name!r}",
            hint="get_job lists the artifacts a job produced",
            details={"jobId": job_id, "artifact": name},
        )

    # ------------------------------------------------------------------ lifecycle

    def wait_idle(self, timeout_s: float = 30.0) -> bool:
        return self._runner.wait_idle(timeout_s)

    def close(self) -> None:
        self._runner.shutdown()
