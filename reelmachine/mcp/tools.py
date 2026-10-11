"""The MCP tools, and nothing else.

Every tool is written for a model: a description that says when to use it, one example,
structured errors that say what to fix, and a result that is data — never bytes, never a
traceback, never a secret.

`start_job` returns in milliseconds; callers poll `get_job`. No call ever waits on a
render. The two discovery tools (`suggest_words`, `find_examples`) are the exception
that proves it: they answer immediately because searching subtitles is cheap and
read-only, and an agent has to decide *what* to teach before it can submit anything.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import impersonate
from ..browser import clearance_state, preferred_name
from ..core.errors import ReelError
from ..core.job import Job, JobRequest, JobState
from ..engine import Engine
from ..engine.providers import provider_health
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
        # imported when a tool runs, not when the module loads: the `mcp` package is
        # an optional extra, and `import reelmachine.mcp.server` must work without it
        from mcp.server.mcpserver.exceptions import ToolError

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
    def browser_status(self, ctx: Any) -> dict[str, Any]:
        """Report the browser backends, the download driver, and the gates already cleared.

        Read-only and instant: it answers "can this deployment fetch a Cloudflare-gated
        page or clip, and with what" by inspecting what is installed, never by launching
        a browser or fetching anything — a solve takes seconds, which no tool call here is
        allowed to do.
        """
        self._caller(ctx)
        settings = self.engine.settings
        rows = [row for row in provider_health(settings=settings) if row["group"] == "browser"]
        return {
            "schemaVersion": SCHEMA_VERSION,
            "selected": preferred_name(settings) or "none",
            "configured": settings.browser,
            "backends": [
                {"name": row["name"], "status": row["status"], "detail": row["detail"]}
                for row in rows
            ],
            "downloads": impersonate.describe(settings),
            "profile": str(settings.browser_profile),
            "headless": bool(settings.browser_headless),
            "clearedHosts": clearance_state(settings.browser_profile),
            "hint": (
                "Two different gates, two different tools: a page behind an interactive "
                "challenge needs a browser backend (patchright is preferred — it clicks "
                "inside Turnstile's cross-origin frame; the profile keeps its clearance), "
                "while a file host that judges the TLS fingerprint needs `downloads` "
                "(curl-cffi impersonating a browser; no cookies required). If a fetch "
                "reports the interstitial, warm the session headed "
                "(REEL_BROWSER_HEADLESS=false) or switch with REEL_BROWSER."
            ),
        }

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

    # ------------------------------------------------- teaching-word discovery
    #
    # These two answer a question rather than start a job. Searching subtitles is
    # cheap and read-only, and an agent choosing *what to teach* needs the answer
    # before it can write a request — so a job (with its store, quota and artifact
    # plumbing) would be the wrong shape, and a poll loop would be pure latency.

    def _clip_source(self, recipe_id: str) -> Any:
        from ..core.errors import InvalidInput

        providers = self.engine.providers_for(recipe_id)
        try:
            source = providers.get("corpus")
        except KeyError as exc:
            raise InvalidInput(
                f"recipe {recipe_id!r} has no clip corpus",
                hint="these tools work with a recipe whose corpus role is a clip source (wordreel)",
                details={"recipe": recipe_id},
            ) from exc
        describe = getattr(source, "describe", None)
        return source if callable(describe) else None

    @_guard
    def suggest_words(
        self,
        ctx: Any,
        recipe: str = "wordreel",
        inputs: dict[str, Any] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        """Words a recipe's corpus can actually teach, best first."""
        from ..recipes.wordreel.clips import ClipRequest
        from ..recipes.wordreel.models import WordReelInputs

        self._caller(ctx)
        source = self._clip_source(recipe)
        model = WordReelInputs.model_validate({**(inputs or {}), "word": "", "mode": "suggest"})
        request = ClipRequest(
            word="",
            match_mode=model.match_mode,
            languages=model.request_languages(),
            library=list(model.library),
            subtitles=list(model.subtitles),
        )
        words = list(source.suggest(request, limit=max(1, min(int(limit), 100))))
        return {
            "schemaVersion": SCHEMA_VERSION,
            "recipe": recipe,
            "corpus": getattr(source, "name", ""),
            "words": words,
            "hint": (
                "pick one, give it a `gloss` (its meaning) and start_job with it — "
                "or call find_examples first to see the lines it would use"
            ),
        }

    @_guard
    def find_examples(
        self,
        ctx: Any,
        word: str,
        recipe: str = "wordreel",
        inputs: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Lines of dialogue that use a word or phrase, with where each one is spoken."""
        from ..recipes.wordreel.clips import ClipRequest
        from ..recipes.wordreel.models import WordReelInputs

        self._caller(ctx)
        source = self._clip_source(recipe)
        model = WordReelInputs.model_validate({**(inputs or {}), "word": word, "mode": "scan"})
        request = ClipRequest(
            word=model.normalised_word(),
            match_mode=model.match_mode,
            allow_inflected=model.allow_inflected,
            languages=model.request_languages(),
            library=list(model.library),
            subtitles=list(model.subtitles),
            min_clip_ms=model.min_clip_ms or 0 or 2_000,
            max_clip_ms=model.max_clip_ms or 0 or 12_000,
            per_source=max(1, model.per_source),
        )
        found = list(source.find(request, limit=max(1, min(int(limit), 200))))
        with_footage = [item for item in found if item.media_path]
        return {
            "schemaVersion": SCHEMA_VERSION,
            "recipe": recipe,
            "corpus": getattr(source, "name", ""),
            "word": model.normalised_word(),
            "matchMode": model.match_mode,
            "examples": [item.to_dict() for item in found],
            "counts": {
                "matched": len(found),
                "withFootage": len(with_footage),
                "sources": len({item.media_label for item in found}),
            },
            "hint": (
                "start_job with this word to build the reel; lines without footage can "
                "be scanned but not cut"
                if with_footage
                else "none of these lines has local footage behind it — a build needs media"
            ),
        }

    @_guard
    def find_models(self, ctx: Any, query: str, limit: int = 12) -> dict[str, Any]:
        """CC0 3D models for explainer3d casts, searched by name and tags."""
        from ..recipes.explainer3d.assets import find_models
        from ..recipes.explainer3d.stages import asset_cache

        self._caller(ctx)
        found = find_models(query, asset_cache(self.engine.settings), limit=max(1, min(int(limit), 50)))
        return {
            "schemaVersion": SCHEMA_VERSION,
            "query": query,
            "models": found,
            "hint": (
                'use one in an explainer3d cast as {"asset": {"model": "<source>", "size": 0.3}} — '
                "size is the longest side in metres (or give height); add \"container\": true "
                "for bowls and buckets things are dropped into"
                if found else "nothing matched; try a simpler word (bowl, rock, fish, bread)"
            ),
        }
