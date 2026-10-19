"""The techreel recipe: a funny AI/tech explainer as a vertical reel of narrated motion graphics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...config import get_settings
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
from ...core.stage import StageContext
from ..doodle.narration import provider_for
from . import assets, lint
from .guide import GUIDE
from .models import TechReelInputs
from .schedule import estimate
from .stages import (
    AssetsStage,
    ComposeStage,
    NarrationStage,
    PreviewStage,
    RenderStage,
    ScriptStage,
    bound_provider,
    lint_findings,
)
from .verify import verify_techreel

# the Hindi example (an original new-tool reel about a fictional open-source app); the guide lists all three
EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
EXAMPLE: dict[str, Any] = json.loads((EXAMPLES_DIR / "hi-pocket-llm.json").read_text(encoding="utf-8"))


def _lint_counts(found: list[dict[str, Any]]) -> str:
    """'2 errors (not blocking), 3 warnings': a non-blocking lint's findings by severity."""
    errors = sum(f["severity"] == "error" for f in found)
    others = len(found) - errors
    parts = [f"{errors} error{'s' * (errors != 1)} (not blocking)"] if errors else []
    parts.append(f"{others} warning{'s' * (others != 1)}")
    return ", ".join(parts)


class TechReelRecipe:
    spec = RecipeSpec(
        id="techreel",
        title="Tech & AI explainer reel",
        summary=(
            "Turns a researched AI or tech topic (a new tool, a news item or a concept) into a funny, "
            "fast vertical reel: a conversational voice-over over dense motion graphics (UI mock-ups, counters, "
            "stamps, emoji gags, kinetic headings and word-by-word captions). "
            "`storyboard` renders a sheet of stills, `full` the reel."
        ),
        input_model=TechReelInputs,
        version=1,
        stages=(
            StageSpec(stage=ScriptStage()),
            StageSpec(stage=AssetsStage(), feeds="script"),
            StageSpec(stage=NarrationStage(), feeds="assets", uses=("narration",)),
            StageSpec(stage=ComposeStage(), feeds="narration"),
            StageSpec(stage=PreviewStage(), feeds="compose", uses=("renderer",)),
            StageSpec(stage=RenderStage(), feeds="preview", uses=("renderer",)),
        ),
        providers={
            "narration": ProviderReq(group="narration", default="fake",
                                     description="Text-to-speech with word timings; Supertonic recommended"),
            "renderer": ProviderReq(group="renderers", default="hyperframes",
                                    description="Renders the reel (Node >= 22, HyperFrames, Chrome, ffprobe)"),
        },
        cost=CostNote(unit="tts_characters", estimate="the length of every line's `say`",
                      notes="the fake, macOS and local Supertonic voices are free; cloud voices bill per character",
                      providers=("supertonic", "elevenlabs", "cartesia", "macos", "fake")),
        artifacts=("reel.mp4", "captions.srt", "reel.json", "storyboard.jpg", "script.resolved.json"),
        render_modes=("hyperframes",),
        example=EXAMPLE,
        guide=GUIDE,
    )

    def probe(self, inputs: TechReelInputs, ctx: ProbeContext) -> ProbeReport:
        """Checks the lint, the drawings and image sources (fetching nothing), the renderer and the voice, and
        estimates the length. Writes nothing."""
        checks: list[CheckResult] = []
        details: dict[str, Any] = {}
        bound = getattr(bound_provider(ctx, "narration"), "name", "")
        config = ctx.config or get_settings()
        found = lint_findings(inputs, bound or None, allow_local=config.local_files)
        details["lint"] = found
        errors = [f"{f['path']}: {f['message']}" for f in found if f["severity"] == "error"]
        blocking = bool(errors) and lint.blocks(inputs)
        checks.append(CheckResult(name="lint", ok=not blocking,
                                  detail="; ".join(errors) if blocking else _lint_counts(found)))
        try:
            assets.check_svgs(inputs)
            assets.check_sources(inputs, allow_local=config.local_files)
            count = sum(1 for _ in assets.image_sources(inputs))
            checks.append(CheckResult(name="assets", ok=True,
                                      detail=f"{count} image{'s' * (count != 1)} to fetch" if count else "no images"))
        except InvalidInput as exc:
            path = exc.details.get("path", "")
            checks.append(CheckResult(name="assets", ok=False, detail=f"{path}: {exc.message}"))
            details["assets"] = {"path": path, "message": exc.message, "hint": exc.hint}
        plan = estimate(inputs)
        details["estimatedMs"] = plan.duration_ms
        details["beats"] = [{"index": i, "estimatedMs": b.end_ms - b.start_ms} for i, b in enumerate(plan.beats)]
        details["cost"] = {"ttsCharacters": sum(len(line.say) for beat in inputs.beats for line in beat.lines)}
        renderer = bound_provider(ctx, "renderer")
        problems = renderer.missing() if renderer is not None else ["no renderer bound"]
        checks.append(CheckResult(name="renderer", ok=not problems,
                                  detail="; ".join(problems) or getattr(renderer, "name", "")))
        wanted = provider_for(inputs.voice)
        checks.append(CheckResult(name="voice", ok=wanted == bound,
                                  detail=f"{inputs.voice!r} is served by {bound!r}" if wanted == bound
                                  else f"{inputs.voice!r} needs {wanted!r}; this deployment serves {bound!r}"))
        return ProbeReport(ok=all(c.ok for c in checks), checks=checks, details=details)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> VerificationReport:
        return verify_techreel(ctx, result)
