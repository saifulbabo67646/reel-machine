"""The eight MCP tools, and nothing else.

Every tool is written for a model: a description that says when to use it, one example,
structured errors that say what to fix, and a result that is data — never bytes, never a
traceback, never a secret.

`start_job` returns in milliseconds; callers poll `get_job`. No call ever waits on a
render.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from mcp.server.mcpserver.exceptions import ToolError

from ..core.errors import ReelError
from ..core.job import Job, JobRequest, JobState
from ..engine import Engine
from .artifacts import ArtifactSigner, artifact_file, file_url
from .auth import resolve_caller
from .tenants import TenantRegistry

log = logging.getLogger("reelmachine.mcp")

SCHEMA_VERSION = 1


def _error_payload(exc: ReelError) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "code": str(exc.code),
        "message": exc.message,
        "hint": exc.hint,
        "stage": exc.stage,
        "details": exc.details,
    }


def _guard(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def wrapper(self: "McpTools", *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(self, *args, **kwargs)
        except ReelError as exc:
            raise ToolError(json.dumps(_error_payload(exc), ensure_ascii=False)) from exc
        except ToolError:
            raise
        except BaseException as exc:  # noqa: BLE001 - the model must never see a traceback
            log.exception("tool %s failed", fn.__name__)
            raise ToolError(
                json.dumps(
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "code": "INTERNAL",
                        "message": "internal error",
                        "hint": "retry the call; if it keeps failing, report it with the job id",
                        "details": {"type": type(exc).__name__},
                    },
                    ensure_ascii=False,
                )
            ) from exc

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _job_payload(job: Job, *, artifacts: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "jobId": job.id,
        "recipe": job.recipe,
        "state": job.state.value,
        "caller": job.caller,
        "createdMs": job.created_ms,
        "startedMs": job.started_ms,
        "finishedMs": job.finished_ms,
        "durationMs": job.duration_ms,
        "progress": {
            "stage": job.progress.stage,
            "stageIndex": job.progress.stage_index,
            "stagesTotal": job.progress.stages_total,
            "percent": job.progress.percent,
            "detail": job.progress.detail,
        },
        "renderMode": job.result.render_mode if job.result else "",
        "error": job.error.model_dump(mode="json") if job.error else None,
    }
    if artifacts:
        payload["artifacts"] = [
            {
                "name": artifact.name,
                "kind": str(artifact.kind),
                "bytes": artifact.bytes,
                "sha256": artifact.sha256,
            }
            for artifact in (job.result.artifacts if job.result else [])
        ]
    return payload


@dataclass
class McpTools:
    engine: Engine
    tenants: TenantRegistry
    signer: ArtifactSigner | None = None
    transport: str = "stdio"
    base_url: str = ""

    # ------------------------------------------------------------------ plumbing
    def _caller(self, ctx: Any) -> str:
        headers = getattr(ctx, "headers", None)
        return resolve_caller(headers, self.tenants, transport=self.transport)

    def _base_url(self, ctx: Any) -> str:
        headers = getattr(ctx, "headers", None) or {}
        lowered = {str(key).lower(): str(value) for key, value in headers.items()}
        host = lowered.get("host", "")
        if host:
            scheme = "https" if lowered.get("x-forwarded-proto", "").lower() == "https" else "http"
            return f"{scheme}://{host}"
        return self.base_url

    def _artifact_metadata(self, job: Job, sha256: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if not job.result or not job.result.manifest or not Path(job.result.manifest).is_file():
            return {}, None
        try:
            manifest = json.loads(Path(job.result.manifest).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}, None
        for record in manifest.get("assets") or []:
            asset = record.get("asset") or {}
            if asset.get("sha256") == sha256:
                return asset.get("provenance") or {}, asset.get("licence")
        return {}, None

    # ---------------------------------------------------------------------- tools
    @_guard
    def list_recipes(self, ctx: Any) -> dict[str, Any]:
        """List every recipe this deployment can make."""
        self._caller(ctx)
        return {"schemaVersion": SCHEMA_VERSION, "recipes": self.engine.list_recipes()}

    @_guard
    def describe_recipe(self, ctx: Any, recipe_id: str) -> dict[str, Any]:
        """Describe one recipe: input schema, providers, cost and an example request."""
        self._caller(ctx)
        described = self.engine.describe(recipe_id)
        return {"schemaVersion": SCHEMA_VERSION, **described}

    @_guard
    def probe(self, ctx: Any, recipe_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Validate inputs and capabilities without spending quota or rendering."""
        caller = self._caller(ctx)
        report = self.engine.probe(recipe_id, inputs, caller=caller)
        return {"schemaVersion": SCHEMA_VERSION, **report.model_dump(mode="json")}

    @_guard
    def start_job(
        self,
        ctx: Any,
        recipe: str,
        inputs: dict[str, Any],
        idempotency_key: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a job and return its id immediately; poll it with get_job."""
        caller = self._caller(ctx)
        job = self.engine.submit(
            JobRequest(
                recipe=recipe,
                inputs=inputs,
                idempotency_key=idempotency_key,
                config=dict(config or {}),
            ),
            caller=caller,
        )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "jobId": job.id,
            "state": job.state.value,
            "recipe": job.recipe,
        }

    @_guard
    def get_job(self, ctx: Any, job_id: str) -> dict[str, Any]:
        """State, progress, artifacts and any structured error for one job."""
        caller = self._caller(ctx)
        return _job_payload(self.engine.get(job_id, caller=caller))

    @_guard
    def list_jobs(
        self,
        ctx: Any,
        state: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List this caller's jobs, newest first."""
        caller = self._caller(ctx)
        parsed: JobState | None = None
        if state:
            try:
                parsed = JobState(state.lower())
            except ValueError as exc:
                from ..core.errors import InvalidInput

                raise InvalidInput(
                    f"unknown state {state!r}",
                    hint="use queued, running, succeeded, failed or cancelled",
                ) from exc
        jobs = self.engine.list(caller=caller, state=parsed, limit=max(1, min(int(limit), 200)))
        return {
            "schemaVersion": SCHEMA_VERSION,
            "jobs": [summary.model_dump(mode="json") for summary in jobs],
        }

    @_guard
    def cancel_job(self, ctx: Any, job_id: str) -> dict[str, Any]:
        """Cancel a queued or running job; a finished job is returned unchanged."""
        caller = self._caller(ctx)
        job = self.engine.cancel(job_id, caller=caller)
        return _job_payload(job, artifacts=False)

    @_guard
    def get_artifact(self, ctx: Any, job_id: str, name: str) -> dict[str, Any]:
        """A signed, expiring URL for one artifact — never the bytes."""
        caller = self._caller(ctx)
        path = artifact_file(self.engine, job_id, name, caller=caller)
        reference = self.engine.artifact(job_id, name, caller=caller)
        url, expires = "", 0
        if self.signer is not None and self._base_url(ctx):
            url, expires = self.signer.url(self._base_url(ctx), caller, job_id, name)
        if not url:
            url, expires = file_url(path), 0
        provenance, licence = self._artifact_metadata(self.engine.get(job_id, caller=caller), reference.sha256)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "jobId": job_id,
            "name": name,
            "kind": str(reference.kind),
            "bytes": reference.bytes,
            "sha256": reference.sha256,
            "url": url,
            "expiresAt": expires,
            "provenance": provenance,
            "licence": licence,
        }
