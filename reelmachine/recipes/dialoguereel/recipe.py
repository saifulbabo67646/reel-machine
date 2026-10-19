"""The dialoguereel recipe: two characters act out a short conversation as a vertical reel."""

from __future__ import annotations

from typing import Any

from ...config import get_settings
from ...core.errors import InvalidInput
from ...core.manifest import CheckResult, VerificationReport
from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec, StageSpec
from ...core.stage import StageContext
from . import cast as casts
from .guide import GUIDE
from .models import DialogueReelInputs
from .ruby import parse_ruby, plain_text
from .schedule import estimate
from .stages import (CastStage, ComposeStage, NarrationStage, PreviewStage, RenderStage, ScriptStage, check_voices,
                     lint_blocks, lint_findings)
from .verify import verify_dialoguereel

# the reference dialogue (spec §8): the kid offers to help, Mom hands out three chores, the kid agrees
EXAMPLE: dict[str, Any] = {
    "mode": "storyboard",
    "title": "Helping Mom at home",
    "level": "N5",
    "scene": "🧺",
    "cast": {"characters": [
        {"id": "kid", "name": "Cupcake", "voice": "fake:default", "side": "left",
         "source": {"kind": "builtin", "name": "cupcake"}},
        {"id": "mom", "name": "Mug", "voice": "fake:default", "side": "right",
         "source": {"kind": "builtin", "name": "mug"}},
    ]},
    "quiz": True,
    "lines": [
        {"speaker": "kid", "text": "おかあさん。", "reading": "Okaasan.", "meaning": "Mom"},
        {"speaker": "kid", "text": "てつだいましょうか。", "reading": "Tetsudai mashou ka.", "meaning": "Shall I help?",
         "emotion": "happy", "slow": True},
        {"speaker": "kid", "text": "なにをしますか？", "reading": "Nani o shimasu ka?", "meaning": "What should I do?",
         "emotion": "thinking"},
        {"speaker": "mom", "text": "せんたくをしてくれる？", "reading": "Sentaku o shite kureru?",
         "meaning": "Can you do the laundry?",
         "keys": [{"text": "せんたく", "reading": "sentaku", "meaning": "laundry"}]},
        {"speaker": "mom", "text": "それとさらをあらって。", "reading": "Sore to sara o aratte.",
         "meaning": "And wash the dishes.",
         "keys": [{"text": "あらう", "reading": "arau", "meaning": "to wash"}]},
        {"speaker": "mom", "text": "ごみもだしてね。", "reading": "Gomi mo dashite ne.",
         "meaning": "Take out the trash too, okay?",
         "keys": [{"text": "だして", "reading": "dashite", "meaning": "take out"}]},
        {"speaker": "kid", "text": "いまからします。", "reading": "Ima kara shimasu.", "meaning": "I'll do it now.",
         "emotion": "happy", "keys": [{"text": "いま", "reading": "ima", "meaning": "now"}]},
    ],
}


class DialogueReelRecipe:
    spec = RecipeSpec(
        id="dialoguereel",
        title="Dialogue reel",
        summary=(
            "Two cute characters act out a short language-learning conversation as a vertical reel: "
            "each line is spoken in its character's voice, shown with its reading and meaning. "
            "`cast` renders a still of the two characters, `storyboard` a sheet of stills, `full` the reel."
        ),
        input_model=DialogueReelInputs,
        version=1,
        stages=(
            StageSpec(stage=ScriptStage()),
            StageSpec(stage=CastStage(), feeds="script"),
            StageSpec(stage=NarrationStage(), feeds="cast", uses=("narration",)),
            StageSpec(stage=ComposeStage(), feeds="narration"),
            StageSpec(stage=PreviewStage(), feeds="compose", uses=("renderer",)),
            StageSpec(stage=RenderStage(), feeds="preview", uses=("renderer",)),
        ),
        providers={
            "narration": ProviderReq(group="narration", default="fake",
                                     description="Text-to-speech with word timings, one voice per character"),
            "renderer": ProviderReq(group="renderers", default="hyperframes",
                                    description="Renders the reel (Node >= 22, HyperFrames, Chrome, ffprobe)"),
        },
        cost=CostNote(unit="tts_characters", estimate="the dialogue's length",
                      notes="the fake and macOS voices are free; cloud voices bill per character. "
                      "Each generated image character draws 4 poses: 4 OpenAI image calls, once per cast",
                      providers=("elevenlabs", "cartesia", "supertonic", "macos", "openai", "fake")),
        artifacts=("reel.mp4", "captions.srt", "reel.json", "storyboard.jpg", "cast.png", "script.resolved.json", "post.md",
                   "cast-<id>-<pose>.png"),
        render_modes=("hyperframes",),
        example=EXAMPLE,
        guide=GUIDE,
    )

    def probe(self, inputs: DialogueReelInputs, ctx: ProbeContext) -> ProbeReport:
        """Checks the script, the lint, the renderer and the voices. Writes nothing (not even an inline cast)."""
        checks: list[CheckResult] = []
        details: dict[str, Any] = {}
        spec = None
        config = ctx.config or get_settings()
        details["lint"] = []
        root = ctx.caller_root or config.cache_dir
        try:
            if isinstance(inputs.cast, str):
                spec = casts.load(root, inputs.cast)
            else:
                spec = inputs.cast
            details["cast"] = casts.cast_id(spec)
            resolved = casts.resolve(spec)
            checks.append(CheckResult(name="script", ok=True,
                                      detail=f"{len(inputs.lines)} lines, cast {resolved.id}"))
            images = [c for c in resolved.characters if c.kind == "image"]
            if images:  # counts what is missing on disk; fetches and generates nothing
                calls = casts.poses_on_disk(root, resolved)
                generators = {c.provider for c in images if c.provider}
                provider = "openai" if "openai" in generators else next(iter(generators), "supplied")
                details["castImages"] = {"provider": provider, "calls": calls, "cached": calls == 0}
                missing_key = provider == "openai" and calls > 0 and not config.openai_api_key
                checks.append(CheckResult(
                    name="images", ok=not missing_key,
                    detail="REEL_OPENAI_API_KEY is not set, so the OpenAI poses cannot be drawn" if missing_key
                    else f"{calls} image calls needed ({provider})" if calls else "every pose is on disk or supplied"))
            found = lint_findings(inputs, {c.id for c in resolved.characters})
            details["lint"] = found
            errors = [f"{f['path']}: {f['message']}" for f in found if f["severity"] == "error"]
            blocking = errors and lint_blocks(inputs)
            checks.append(CheckResult(name="lint", ok=not blocking,
                                      detail="; ".join(errors) if blocking else f"{len(found)} suggestions"))
        except InvalidInput as exc:
            path = exc.details.get("path", "")
            checks.append(CheckResult(name="script", ok=False, detail=f"{path}: {exc.message}"))
            details["error"] = {"path": path, "message": exc.message, "hint": exc.hint}
        plan = estimate(inputs)
        details["estimatedMs"] = plan.duration_ms
        details["lines"] = [{"index": i, "estimatedMs": w.speech_ms} for i, w in enumerate(plan.lines)]
        details["cost"] = {"ttsCharacters": sum(len(plain_text(parse_ruby(line.text)[0])) for line in inputs.lines)}
        providers = ctx.providers
        renderer = providers.get("renderer") if providers else None
        problems = renderer.missing() if renderer is not None else ["no renderer bound"]
        checks.append(CheckResult(name="renderer", ok=not problems,
                                  detail="; ".join(problems) or getattr(renderer, "name", "")))
        if spec is not None:
            bound = getattr(providers.get("narration") if providers else None, "name", "")
            wrong = check_voices(spec, bound)
            checks.append(CheckResult(name="voice", ok=not wrong,
                                      detail=f"{'; '.join(wrong)}; this deployment serves {bound!r}" if wrong
                                      else f"every voice is served by {bound!r}"))
        return ProbeReport(ok=all(c.ok for c in checks), checks=checks, details=details)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> VerificationReport:
        return verify_dialoguereel(ctx, result)
