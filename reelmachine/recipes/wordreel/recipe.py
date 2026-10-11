"""The wordreel recipe: an English word becomes a learning reel.

An agent picks a word or a phrase — or asks the recipe what this library can teach —
and the recipe finds real lines of dialogue that use it, cuts each one out of the
footage it came from, and joins them into one vertical reel: the word pinned at the
top, its meaning under it, and every line captioned with the target coloured where it
is actually spoken.

Two properties are worth stating plainly, because they are the reason this is a
recipe and not a scraper:

* **The corpus is the caller's.** Examples are read from subtitles on disk (sidecar
  files, or the tracks inside the video). Nothing is fetched from a third party, so
  there is no quota, no account, and no bot challenge to solve.
* **Search is free and cutting is separate.** `scan` lists every matching line with
  its file and its timestamps without decoding a frame; `prep` is where video is
  touched. A caller can therefore check the material before paying for a render.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ...config import Settings
from ...core.errors import InvalidInput
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
from .models import WordReelInputs
from .stages import PrepStage, RenderStage, ScanStage, SelectStage, ffmpeg_available
from .verify import verify_wordreel


class WordReelRecipe:
    spec = RecipeSpec(
        id="wordreel",
        title="English word / phrase learning reel",
        summary=(
            "A word or phrase becomes a short learning reel: real lines of dialogue that "
            "use it, cut from your own media by searching its subtitles, joined with the "
            "word pinned on screen and the target highlighted as it is spoken. "
            "mode='suggest' proposes words the library can actually teach; mode='scan' "
            "lists the matching lines before anything is cut."
        ),
        input_model=WordReelInputs,
        version=1,
        stages=(
            StageSpec(stage=SelectStage(), uses=("corpus",)),
            StageSpec(stage=ScanStage(), feeds="select", uses=("corpus",)),
            StageSpec(stage=PrepStage(), feeds="scan"),
            StageSpec(stage=RenderStage(), feeds="prep"),
        ),
        providers={
            "corpus": ProviderReq(
                group="clips",
                default="library",
                description="Searchable dialogue: your media and the subtitles beside it",
            ),
        },
        cost=CostNote(
            unit="local_reads",
            estimate="free — no network, no quota",
            notes=(
                "searching reads subtitle text and costs nothing; a build decodes each "
                "example once and re-encodes the reel once"
            ),
            providers=("library",),
        ),
        artifacts=(
            "reel.mp4",
            "captions.ass",
            "captions.srt",
            "reel.json",
            "plan.json",
            "timeline.json",
        ),
        render_modes=("wordreel",),
        example={
            "word": "give up",
            "mode": "build",
            "count": 6,
            "gloss": "to stop trying",
            "aspect": "vertical",
        },
    )

    # ------------------------------------------------------------------- probe
    def probe(self, inputs: WordReelInputs, ctx: ProbeContext) -> ProbeReport:
        """Validate inputs and capabilities without reading media or rendering."""
        settings: Settings = ctx.config
        checks: list[CheckResult] = []
        warnings: list[str] = []

        ffmpeg_ok = ffmpeg_available(settings)
        checks.append(
            CheckResult(
                name="ffmpeg",
                ok=ffmpeg_ok,
                detail=settings.ffmpeg if ffmpeg_ok else f"{settings.ffmpeg!r} not found on PATH",
            )
        )

        corpus = ctx.providers.get("corpus") if ctx.providers else None
        if corpus is None:
            checks.append(CheckResult(name="corpus", ok=False, detail="no clip source bound"))
        else:
            problems = list(getattr(corpus, "missing", lambda: [])())
            directories = list(inputs.library) or list(settings.wordreel_library)
            present = [entry for entry in directories if Path(entry).expanduser().is_dir()]
            supplied = [entry for entry in inputs.subtitles if Path(entry).expanduser().is_file()]
            checks.append(
                CheckResult(
                    name="corpus",
                    ok=not problems or bool(present) or bool(supplied),
                    detail=(
                        f"{getattr(corpus, 'name', '?')}: "
                        f"{len(present)} director(ies), {len(supplied)} subtitle file(s)"
                    ),
                )
            )
            if not present and not supplied:
                warnings.append(
                    "no library directory exists and no subtitle file was supplied; "
                    "set REEL_WORDREEL_LIBRARY or pass `library` / `subtitles`"
                )
            missing = [entry for entry in directories if not Path(entry).expanduser().is_dir()]
            if missing:
                warnings.append(f"ignored, not a directory: {', '.join(missing[:4])}")

        word = inputs.normalised_word()
        if not word and inputs.mode != "suggest":
            checks.append(
                CheckResult(
                    name="word",
                    ok=False,
                    detail="mode needs a word; use mode='suggest' to have one proposed",
                )
            )
        elif word:
            try:
                from .matching import WordMatcher

                WordMatcher(word, mode=inputs.match_mode)
                checks.append(CheckResult(name="word", ok=True, detail=word))
            except ValueError as exc:
                checks.append(CheckResult(name="word", ok=False, detail=str(exc)))

        if inputs.count < 1:
            warnings.append("count < 1: the reel would be empty")

        return ProbeReport(
            ok=all(check.ok for check in checks),
            checks=checks,
            warnings=warnings,
            quota_free=True,
            details={
                "mode": inputs.mode,
                "word": word,
                "matchMode": inputs.match_mode,
                "count": inputs.count,
                "corpus": getattr(corpus, "name", "") if corpus is not None else "",
            },
        )

    # ------------------------------------------------------------------ verify
    def verify(self, ctx: StageContext, outputs: dict[str, Any]) -> VerificationReport:
        return verify_wordreel(ctx, outputs)


def run_wordreel(
    inputs: WordReelInputs | Mapping[str, Any],
    *,
    corpus: Any,
    settings: Settings,
    progress: Any = None,
    job_id: str | None = None,
    cache_root: Path | None = None,
    stop_when: Any = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Run the recipe through the stage runner; returns `(runs, outputs by stage id)`."""
    from ...core.job import Job, JobRequest
    from ...engine.runner import run_stages

    payload = inputs if isinstance(inputs, WordReelInputs) else WordReelInputs.model_validate(inputs)
    if not payload.normalised_word() and payload.mode != "suggest":
        raise InvalidInput(
            "no word given",
            hint="pass `word`, or mode='suggest' to have the corpus propose one",
        )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    resolved_job_id = job_id or f"{slugify(payload.normalised_word() or 'suggest')}-{stamp}"
    job = Job(
        id=resolved_job_id,
        recipe="wordreel",
        request=JobRequest(recipe="wordreel", inputs=payload.model_dump(mode="json")),
    )
    runs = run_stages(
        WordReelRecipe(),
        payload,
        workdir=settings.workdir / "runs" / resolved_job_id,
        assets_root=settings.workdir / "assets",
        providers=StaticProviders({"corpus": corpus}),
        config=settings,
        progress=progress or NullProgress(),
        cache_root=cache_root if cache_root is not None else settings.workdir / "cache" / "stages",
        job=job,
        stop_when=stop_when,
    )
    return runs, {run.id: run.output for run in runs}
