"""The explainer3d recipe: a scene script becomes a narrated 3D reel, shot by shot."""

from __future__ import annotations

from typing import Any

from ...core.errors import InvalidInput
from ...core.manifest import CheckResult, VerificationReport
from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec, StageSpec
from ...core.stage import StageContext
from ..doodle.narration import provider_for
from .models import Explainer3DInputs
from .stages import AssetsStage, ComposeStage, NarrationStage, PrepStage, RenderStage, ScriptStage, ShotsStage
from .compile import look_timing
from .markup import parse_markup, spoken_text
from .timing import estimate_speech_ms, fit_windows, shot_windows
from .validate import resolve_script
from .verify import verify_explainer3d
from .guide import build_guide
from .lint import retention_warnings


class Explainer3DRecipe:
    spec = RecipeSpec(
        id="explainer3d",
        title="3D explainer",
        summary=(
            "A scene script — cast, shots, moves, camera — becomes a narrated vertical reel "
            "rendered in 3D (three.js via HyperFrames). Objects carry over between shots and "
            "a camera can continue across a cut. `storyboard` renders one still per shot."
        ),
        input_model=Explainer3DInputs,
        version=1,
        stages=(
            StageSpec(stage=ScriptStage()),
            StageSpec(stage=AssetsStage(), feeds="script"),
            StageSpec(stage=NarrationStage(), feeds="assets", uses=("narration",)),
            StageSpec(stage=ShotsStage(), feeds="narration"),
            StageSpec(stage=PrepStage(), feeds="shots", uses=("renderer",)),
            StageSpec(stage=ComposeStage(), feeds="prep"),
            StageSpec(stage=RenderStage(), feeds="compose"),
        ),
        providers={
            "narration": ProviderReq(group="narration", default="fake",
                                     description="Text-to-speech with word timings"),
            "renderer": ProviderReq(group="renderers", default="hyperframes",
                                    description="Renders each shot (Node >= 22, HyperFrames, Chrome, ffprobe)"),
        },
        cost=CostNote(unit="tts_characters", estimate="narration length",
                      notes="the fake voice is free; cloud voices bill per character",
                      providers=("elevenlabs", "cartesia", "fake")),
        guide=build_guide(),
        artifacts=("reel.mp4", "captions.srt", "reel.json", "timeline.json", "storyboard.jpg",
                   "script.resolved.json"),
        render_modes=("hyperframes",),
        example={
            "mode": "storyboard",
            "title": "The Secret Bait That Attracts Big Carp",
            "cast": {"bowl": {"asset": {"shape": "bowl", "height": 0.2, "material": "stone"}, "at": "stage.center"},
                     "potato": {"asset": {"shape": "ball", "height": 0.1, "colour": "#d9b44a"}}},
            "shots": [{"narration": "First, a steamed potato goes into the bowl.", "label": "Step 1",
                       "camera": {"move": "orbit", "target": "bowl", "from": 10, "to": 40},
                       "actions": [{"move": "fly_in", "actor": "potato"},
                                   {"move": "drop_into", "actor": "potato", "into": "bowl"}]}],
        },
    )

    def probe(self, inputs: Explainer3DInputs, ctx: ProbeContext) -> ProbeReport:
        checks: list[CheckResult] = []
        details: dict[str, Any] = {}
        warnings: list[str] = []
        try:
            shots = resolve_script(inputs)
            checks.append(CheckResult(name="script", ok=True, detail=f"{len(shots)} shots"))
            windows = shot_windows(
                [estimate_speech_ms(spoken_text(parse_markup(s.narration)), inputs.language) for s in shots],
                durations=[s.duration_ms for s in shots], **look_timing(inputs.look))
            natural = windows[-1].end_ms
            if inputs.length:
                windows = fit_windows(windows, inputs.length * 1000)
            details["shots"] = [{"id": s.id, "estimatedMs": w.duration_ms} for s, w in zip(shots, windows)]
            details["estimatedMs"] = windows[-1].end_ms
            warnings = retention_warnings(inputs, shots, windows)
            if inputs.length:
                details["length"] = {"targetMs": inputs.length * 1000, "narrationMs": natural,
                                     "estimatedMs": windows[-1].end_ms}
        except InvalidInput as exc:
            path = exc.details.get("path", "")
            checks.append(CheckResult(name="script", ok=False, detail=f"{path}: {exc.message}"))
            details["error"] = {"path": path, "message": exc.message, "hint": exc.hint}
        details["cost"] = {"ttsCharacters": sum(len(spoken_text(parse_markup(s.narration))) for s in inputs.shots),
                           "voice": inputs.voice}

        providers = ctx.providers
        renderer = providers.get("renderer") if providers else None
        problems = renderer.missing() if renderer is not None else ["no renderer bound"]
        checks.append(CheckResult(name="renderer", ok=not problems,
                                  detail="; ".join(problems) or getattr(renderer, "name", "")))
        voice = providers.get("narration") if providers else None
        bound = getattr(voice, "name", "")
        checks.append(CheckResult(name="voice", ok=provider_for(inputs.voice) == bound,
                                  detail=f"requested {provider_for(inputs.voice)!r}, deployment serves {bound!r}"))
        return ProbeReport(ok=all(c.ok for c in checks), checks=checks, details=details, warnings=warnings)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> VerificationReport | None:
        return verify_explainer3d(ctx, result)
