"""The nadeshiko-cut recipe: one pipeline, parameterised by corpus.

Anime and J-Drama are not two pipelines — they are this one pipeline with a corpus filter.
The same recipe also serves YouTube, and a mixed reel is the same code path again.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ...config import Settings
from ...core.manifest import CheckResult, VerificationReport
from ...core.recipe import (
    CostNote,
    ProbeContext,
    ProbeReport,
    ProviderReq,
    RecipeSpec,
    StageSpec,
)
from ...core.stage import NullProgress, StageContext, StaticProviders
from ...core.text import slugify
from .models import NadeshikoCutInputs
from .stages import (
    AcquireStage,
    ComposeStage,
    PrepStage,
    RenderStage,
    SelectStage,
    ffmpeg_available,
)


class NadeshikoCutRecipe:
    spec = RecipeSpec(
        id="nadeshiko-cut",
        title="Dialogue cut (anime / J-Drama)",
        summary=(
            "Search Nadeshiko for a word, align the matching scenes against your own copy "
            "of the episode, cut the lines and render a subtitled reel. `corpus` picks "
            "anime, J-Drama, both, or YouTube — one pipeline either way."
        ),
        input_model=NadeshikoCutInputs,
        version=2,
        stages=(
            StageSpec(stage=SelectStage(), uses=("corpus",)),
            StageSpec(stage=AcquireStage(), feeds="select", uses=("corpus", "source")),
            StageSpec(stage=ComposeStage(), feeds="acquire"),
            StageSpec(stage=PrepStage(), feeds="compose"),
            StageSpec(stage=RenderStage(), feeds="prep"),
        ),
        providers={
            "corpus": ProviderReq(
                group="corpora",
                default="nadeshiko",
                description="Nadeshiko search, segment context and reference audio",
            ),
            "source": ProviderReq(
                group="sources",
                default="",
                description="Where your own copy of the episode lives",
            ),
        },
        cost=CostNote(
            unit="nadeshiko_requests",
            estimate="1-6 per job",
            notes=(
                "search pages plus context lookups and episode resolution; alignment "
                "reference clips are free. A cached request costs nothing."
            ),
            providers=("nadeshiko",),
        ),
        artifacts=("reel.mp4", "captions.ass", "captions.srt", "plan.json", "manifest.json"),
        render_modes=("cut",),
        example={"word": "彼女", "corpus": "anime+jdrama", "count": 5, "aspect": "vertical"},
    )

    def probe(self, inputs: NadeshikoCutInputs, ctx: ProbeContext) -> ProbeReport:
        """Validate inputs and capabilities without spending quota or rendering."""
        settings: Settings = ctx.config
        checks: list[CheckResult] = []
        ffmpeg_ok = ffmpeg_available(settings)
        checks.append(
            CheckResult(
                name="ffmpeg",
                ok=ffmpeg_ok,
                detail=settings.ffmpeg if ffmpeg_ok else f"{settings.ffmpeg!r} not found on PATH",
            )
        )
        source = ctx.providers.get("source") if ctx.providers else None
        checks.append(
            CheckResult(
                name="source",
                ok=source is not None,
                detail=getattr(source, "name", "") or "none configured",
            )
        )
        corpus = ctx.providers.get("corpus") if ctx.providers else None
        checks.append(
            CheckResult(
                name="corpus",
                ok=corpus is not None,
                detail=type(corpus).__name__ if corpus is not None else "none configured",
            )
        )
        warnings: list[str] = []
        if not inputs.only:
            warnings.append(
                "without `only` this probe cannot check source availability without "
                "spending quota; pin episodes with only=['<mediaPublicId>:<episode>']"
            )
        return ProbeReport(
            ok=all(check.ok for check in checks),
            checks=checks,
            warnings=warnings,
            quota_free=True,
            details={"corpus": inputs.categories(), "mode": inputs.mode, "word": inputs.word},
        )

    def verify(self, ctx: StageContext, result: Any) -> VerificationReport | None:
        return None


def run_nadeshiko_cut(
    inputs: NadeshikoCutInputs | Mapping[str, Any],
    *,
    client: Any,
    provider: Any,
    settings: Settings,
    progress: Any = None,
    job_id: str | None = None,
    cache_root: Path | None = None,
    stop_when: Any = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Run the recipe through the stage runner; returns `(runs, outputs by stage id)`."""
    from ...core.job import Job, JobRequest
    from ...engine.runner import run_stages

    payload = (
        inputs if isinstance(inputs, NadeshikoCutInputs) else NadeshikoCutInputs.model_validate(inputs)
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    resolved_job_id = job_id or f"{slugify(payload.word)}-{stamp}"
    job = Job(
        id=resolved_job_id,
        recipe="nadeshiko-cut",
        request=JobRequest(recipe="nadeshiko-cut", inputs=payload.model_dump(mode="json")),
    )
    runs = run_stages(
        NadeshikoCutRecipe(),
        payload,
        workdir=settings.workdir / "runs" / resolved_job_id,
        assets_root=settings.workdir / "assets",
        providers=StaticProviders({"corpus": client, "source": provider}),
        config=settings,
        progress=progress or NullProgress(),
        cache_root=cache_root if cache_root is not None else settings.workdir / "cache" / "stages",
        job=job,
        stop_when=stop_when,
    )
    return runs, {run.id: run.output for run in runs}
