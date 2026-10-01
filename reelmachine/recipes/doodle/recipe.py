"""The doodle recipe: hand-drawn explainer reels in stroke or program mode.

One recipe, two production modes. The mode is data: it reaches the manifest, and the
verifier checks that the tracks were produced by the renderer the manifest names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.errors import VoiceUnavailable
from ...core.manifest import CheckResult, VerificationReport
from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec, StageSpec
from ...core.stage import StageContext
from ...core.style import StyleNotFound, get_style
from .models import DoodleInputs
from .narration import provider_for
from .scenes.program import ProgramRenderer
from .scenes.stroke import StrokeRenderer
from .stages import (
    ComposeStage,
    NarrationStage,
    PrepStage,
    RenderStage,
    ScenesStage,
    ScriptStage,
)
from .verify import verify_doodle


class DoodleRecipe:
    spec = RecipeSpec(
        id="doodle",
        title="Hand-drawn explainer",
        summary=(
            "A topic or a structured script becomes a narrated explainer reel. `stroke` "
            "inks line art stroke by stroke on a continuous canvas; `program` animates "
            "deterministic knowledge graphics. Vertical 9:16 is the default."
        ),
        input_model=DoodleInputs,
        version=1,
        stages=(
            StageSpec(stage=ScriptStage()),
            StageSpec(stage=NarrationStage(), feeds="script", uses=("narration",)),
            StageSpec(stage=ScenesStage(), feeds="narration"),
            StageSpec(
                stage=PrepStage(),
                feeds="scenes",
                uses=("renderer_stroke", "renderer_program"),
            ),
            StageSpec(stage=ComposeStage(), feeds="prep"),
            StageSpec(stage=RenderStage(), feeds="compose"),
        ),
        providers={
            "narration": ProviderReq(
                group="narration",
                default="fake",
                description="Text-to-speech with real word or line timings",
            ),
            "renderer_stroke": ProviderReq(
                group="renderers",
                default="stroke",
                description="Whiteboard inking (classic image algorithms; no browser, no GPU)",
            ),
            "renderer_program": ProviderReq(
                group="renderers",
                default="program",
                description="Deterministic knowledge graphics (Pillow)",
            ),
        },
        cost=CostNote(
            unit="tts_characters",
            estimate="narration length",
            notes="the fake voice is free; cloud voices bill per character and are per-caller metered",
            providers=("elevenlabs", "cartesia", "fake"),
        ),
        artifacts=("reel.mp4", "captions.ass", "captions.srt", "timeline.json", "manifest.json"),
        render_modes=("stroke", "program"),
        example={
            "topic": "compound interest",
            "mode": "stroke",
            "voice": "fake:default",
            "aspect": "vertical",
        },
    )

    def probe(self, inputs: DoodleInputs, ctx: ProbeContext) -> ProbeReport:
        checks: list[CheckResult] = []
        has_source = bool(inputs.script) or bool(inputs.topic)
        checks.append(
            CheckResult(
                name="script",
                ok=has_source,
                detail="supplied beats" if inputs.script else ("topic" if inputs.topic else "neither"),
            )
        )
        try:
            style = get_style(inputs.style)
            checks.append(CheckResult(name="style", ok=True, detail=style.id))
        except StyleNotFound as exc:
            checks.append(CheckResult(name="style", ok=False, detail=str(exc)))

        for index, path in enumerate(inputs.line_art, start=1):
            resolved = Path(path).expanduser()
            checks.append(
                CheckResult(
                    name=f"line_art[{index}]",
                    ok=resolved.is_file(),
                    detail=str(resolved),
                )
            )

        provider = ctx.providers.get("narration") if ctx.providers else None
        provider_name = getattr(provider, "name", "")
        requested = provider_for(inputs.voice)
        voice_ok = requested == provider_name or inputs.allow_visible_fallback
        checks.append(
            CheckResult(
                name="voice",
                ok=voice_ok,
                detail=(
                    f"{inputs.voice} via {provider_name}"
                    if requested == provider_name
                    else f"{requested!r} is not the bound provider {provider_name!r}"
                ),
            )
        )
        if provider is not None and not provider.missing():
            listing = provider.voices()
            if listing is not None and requested == provider_name:
                ids = {voice.id for voice in listing}
                names = {voice.name.lower() for voice in listing if voice.name}
                wanted = inputs.voice.split(":", 1)[1].lower()
                if wanted and wanted not in ids and wanted not in names:
                    checks.append(
                        CheckResult(
                            name="voice_exists",
                            ok=False,
                            detail=f"{wanted!r} is not one of: {', '.join(sorted(ids)[:6])}",
                        )
                    )

        renderer = StrokeRenderer() if inputs.mode == "stroke" else ProgramRenderer()
        problems = renderer.missing()
        checks.append(
            CheckResult(
                name=f"renderer:{inputs.mode}",
                ok=not problems,
                detail="; ".join(problems) or "available",
            )
        )
        return ProbeReport(
            ok=all(check.ok for check in checks),
            checks=checks,
            quota_free=True,
            details={"mode": inputs.mode, "aspect": inputs.aspect, "beats": len(inputs.script or [])},
        )

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> VerificationReport:
        return verify_doodle(ctx, result)
