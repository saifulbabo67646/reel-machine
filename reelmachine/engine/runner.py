"""Run a recipe's stages: cache by content hash, persist outputs, report progress.

The runner is deliberately small. Durability, jobs and quotas are layered on top of it;
what it owns is the stage contract — a stage reads the previous stage's payload and
writes a serializable output, and a stage that is cacheable by content hash is skipped
when its output already exists.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.assets import AssetStore
from ..core.job import Job, JobRequest, now_ms
from ..core.recipe import Recipe, instantiate_recipe
from ..core.stage import (
    NeverCancel,
    NullProgress,
    NullQuota,
    StageCache,
    StageContext,
    StaticProviders,
)

#: Settings that change what a stage produces; they are part of every cache key so a
#: configuration change can never serve a stale output.
CONFIG_KEYS = (
    "aspect",
    "crf",
    "preset",
    "cut_mode",
    "cut_pad_ms",
    "target_ms",
    "match_mode",
    "context_enabled",
    "context_take",
    "min_clip_ms",
    "max_clip_ms",
    "pre_roll_ms",
    "post_roll_ms",
    "font_ja",
    "font_en",
    "font_card",
    "dictionary_offline",
)


def config_subset(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    return {key: getattr(config, key) for key in CONFIG_KEYS if hasattr(config, key)}


def payload_field(source: Any, key: str) -> Any:
    """One field of a cache payload — `"topic"`, or a dotted path like
    `"inputs.language"`.

    Stage outputs carry the request they were computed for (`inputs`), which is
    refreshed on every cache hit; a cache key can therefore be narrowed to the
    *request* fields a stage actually reads, without copying them onto every
    intermediate output (a copy goes stale the moment a later job changes the
    input it was copied from).
    """
    value: Any = source
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


@dataclass(slots=True)
class StageRun:
    id: str
    index: int
    revision: int = 1
    cached: bool = False
    started_ms: int = 0
    finished_ms: int = 0
    cache_key: str = ""
    output_hash: str = ""
    output: Any = None

    @property
    def duration_ms(self) -> int:
        return max(0, self.finished_ms - self.started_ms)


def jsonable(output: Any) -> Any:
    if hasattr(output, "model_dump"):
        return output.model_dump(mode="json")
    return output


def _restamp_inputs(output: Any, stage_payload: Any) -> Any:
    """A cached output's `inputs` are refreshed from the request in hand.

    A stage's cache key may deliberately ignore fields that cannot change that stage's own
    result (the background list cannot change an ayah selection; the render mode cannot
    change the beats). But every output carries the request it was computed for, and later
    stages read `payload.inputs` — so a stale copy would silently drive them: a stroke job
    rendered as program, a three-clip background rendered as one.
    """
    if not hasattr(output, "inputs") or not hasattr(output, "model_copy"):
        return output
    fresh = getattr(stage_payload, "inputs", None)
    if fresh is None:
        fresh = type(output.inputs).model_validate(stage_payload)
    if fresh == output.inputs:
        return output
    return output.model_copy(update={"inputs": fresh})


def run_stages(
    recipe: Any,
    payload: Any,
    *,
    workdir: Path,
    assets_root: Path,
    providers: Any = None,
    config: Any = None,
    progress: Any = None,
    cancel: Any = None,
    quota: Any = None,
    cache_root: Path | None = None,
    job: Job | None = None,
    log: logging.Logger | None = None,
    cache: bool = True,
    stop_when: Any = None,
) -> list[StageRun]:
    """Run `recipe`'s stages in order.

    `stop_when(run, outputs)` is checked after every stage and stops the pipeline when it
    returns True — the client's seam for "nothing aligned, do not render".
    """
    recipe = instantiate_recipe(recipe)
    spec = recipe.spec
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    assets = AssetStore(assets_root)
    cache_store = StageCache(Path(cache_root)) if (cache and cache_root) else None
    if job is None:
        job = Job(
            id=workdir.name or spec.id,
            recipe=spec.id,
            request=JobRequest(recipe=spec.id, inputs=jsonable(payload)),
        )
    providers = providers if providers is not None else StaticProviders()
    progress = progress if progress is not None else NullProgress()
    cancel = cancel if cancel is not None else NeverCancel()
    quota = quota if quota is not None else NullQuota()
    log = log if log is not None else logging.getLogger("reelmachine")

    outputs: dict[str, Any] = {}
    runs: list[StageRun] = []
    total = len(spec.stages)
    for index, stage_spec in enumerate(spec.stages, start=1):
        stage = stage_spec.stage
        stage_id = stage.id
        progress.stage(stage_id, index, total)
        stage_dir = workdir / "stages" / f"{index:02d}-{stage_id}"
        stage_dir.mkdir(parents=True, exist_ok=True)

        stage_payload = payload if stage_spec.feeds is None else outputs.get(stage_spec.feeds)
        if stage_payload is None:
            raise ValueError(
                f"stage {stage_id!r} feeds from {stage_spec.feeds!r}, which produced no output"
            )

        run = StageRun(
            id=stage_id,
            index=index,
            revision=int(getattr(stage, "revision", 1)),
            started_ms=now_ms(),
        )

        cache_key = ""
        if cache_store is not None and getattr(stage, "cacheable", False):
            source = jsonable(stage_payload)
            fields = getattr(stage, "cache_fields", None)
            if fields and isinstance(source, dict):
                cache_payload: Any = {key: payload_field(source, key) for key in fields}
            else:
                cache_payload = source
            pins = {role: providers.pin(role) for role in tuple(stage_spec.uses)}
            cache_key = StageCache.make_key(
                stage_id,
                run.revision,
                cache_payload,
                config_subset=config_subset(config),
                provider_pins=pins,
            )
            cached = cache_store.load(cache_key)
            if cached is not None:
                model = getattr(stage, "output_model", None)
                run.output = model.model_validate(cached) if model is not None else cached
                run.output = _restamp_inputs(run.output, stage_payload)
                # Halting is a property of the request, not of the cached artifact: a
                # stage whose stop condition depends on the inputs (storyreel's
                # `mode="transcript"`) must stop the pipeline even when its output was
                # produced by an earlier job that ran past it.
                halt_for = getattr(stage, "halt_for", None)
                if callable(halt_for) and hasattr(run.output, "halt_pipeline"):
                    run.output.halt_pipeline = bool(halt_for(stage_payload))
                run.cached = True
                run.cache_key = cache_key
                run.finished_ms = now_ms()
                log.info("stage %s: cache hit", stage_id)
                outputs[stage_id] = run.output
                runs.append(run)
                if getattr(run.output, "halt_pipeline", False):
                    break
                if stop_when is not None and stop_when(run, outputs):
                    break
                continue

        cancel.raise_if_cancelled()
        ctx = StageContext(
            job=job,
            workdir=stage_dir,
            assets=assets,
            config=config,
            providers=providers,
            progress=progress,
            cancel=cancel,
            quota=quota,
            log=log,
            extra={"stage": stage_id},
        )
        output = stage.run(ctx, stage_payload)
        run.output = output
        run.finished_ms = now_ms()
        run.cache_key = cache_key
        dumped = jsonable(output)
        (stage_dir / "output.json").write_text(
            json.dumps(dumped, ensure_ascii=False, default=str), encoding="utf-8"
        )
        if cache_store is not None and cache_key:
            run.output_hash = cache_store.save(cache_key, dumped)
        outputs[stage_id] = output
        runs.append(run)
        if getattr(output, "halt_pipeline", False):
            break
        if stop_when is not None and stop_when(run, outputs):
            break
    return runs
