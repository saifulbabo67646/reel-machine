"""The mapreel recipe: a script of map segments becomes a narrated satellite-map reel."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ...config import get_settings
from ...core.errors import InvalidInput, ProviderUnavailable
from ...core.manifest import CheckResult
from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec, StageSpec
from ...core.stage import StageContext
from ..doodle.narration import provider_for
from ..explainer3d.markup import parse_markup, spoken_text
from .models import MapReelInputs
from .places import atlas_for
from .regions import region_atlas_for
from . import lint
from .stages import (ComposeStage, GeoStage, ImagesStage, NarrationStage, PrepStage, RenderStage,
                     ScriptStage, SegmentsStage, check_music, estimated_windows, resolve_segments)

GUIDE = {
    "workflow": [
        "1. Pick a topic people argue about in the comments (brands, dishes, inventions, bans) and research "
        "every fact you will say; keep the source URL.",
        "2. Write the hook first: segment 1 opens on a surprise, a number, a challenge or 'you' — never "
        "'X has…'. Tease a payoff you deliver at the end.",
        "3. Resolve places with find_places (codes like 'ITA'); one country per 'country' segment, 1-8 items, "
        "listed in the order the narration names them.",
        "4. Use a 'highlight' segment for the hook or a reveal (several countries at once with a legend).",
        "5. probe and fix every failed check; start_job with mode 'storyboard', look at storyboard.jpg, "
        "then start_job with mode 'build'.",
    ],
    "tips": [
        "Items pop when the voice says their name: write the name in the narration exactly, or give `say` "
        "for how it is pronounced and `at` (seconds) to force a time.",
        "Keep a new visual every second: 3-5 items per country segment, segments of 3-5 seconds.",
        "`image` takes a local file or an https URL; anything unusable shows as a bold text badge.",
    ],
}

EXAMPLE = {
    "mode": "storyboard",
    "title": "Brands you didn't know were from these countries",
    "segments": [
        {"kind": "highlight", "countries": ["USA", "ITA", "FRA"], "legend": "Fashion superpowers",
         "narration": "Only *3 countries* dress the whole world. Number one will surprise you."},
        {"kind": "country", "country": "ITA", "narration": "Italy has Gucci, Prada and Versace.",
         "items": [{"name": "Gucci"}, {"name": "Prada"}, {"name": "Versace"}]},
    ],
}


class MapReelRecipe:
    spec = RecipeSpec(
        id="mapreel",
        title="Map reel",
        summary=(
            "A script of map segments becomes a narrated vertical reel on a satellite world map: "
            "the camera flies to each country, fills it and pops in what the voice names. "
            "`storyboard` renders one still per segment."
        ),
        input_model=MapReelInputs,
        version=1,
        stages=(
            StageSpec(stage=ScriptStage()),
            StageSpec(stage=GeoStage(), feeds="script"),
            StageSpec(stage=ImagesStage(), feeds="geo"),
            StageSpec(stage=NarrationStage(), feeds="images", uses=("narration",)),
            StageSpec(stage=SegmentsStage(), feeds="narration"),
            StageSpec(stage=PrepStage(), feeds="segments", uses=("renderer",)),
            StageSpec(stage=ComposeStage(), feeds="prep"),
            StageSpec(stage=RenderStage(), feeds="compose"),
        ),
        providers={
            "narration": ProviderReq(group="narration", default="fake",
                                     description="Text-to-speech with word timings"),
            "renderer": ProviderReq(group="renderers", default="hyperframes",
                                    description="Renders each segment (Node >= 22, HyperFrames, Chrome, ffprobe)"),
        },
        cost=CostNote(unit="tts_characters", estimate="narration length",
                      notes="the fake and macOS voices are free; cloud voices bill per character",
                      providers=("elevenlabs", "cartesia", "macos", "fake")),
        artifacts=("reel.mp4", "captions.srt", "reel.json", "timeline.json", "storyboard.jpg",
                   "script.resolved.json"),
        render_modes=("hyperframes",),
        guide=GUIDE,
        example=EXAMPLE,
    )

    def probe(self, inputs: MapReelInputs, ctx: ProbeContext) -> ProbeReport:
        checks: list[CheckResult] = []
        details: dict[str, Any] = {}
        try:
            check_music(inputs)
            settings = ctx.config or get_settings()
            # the admin-1 file (about 40 MB) is fetched only when a regions segment needs it
            segments = resolve_segments(inputs, atlas_for(settings), lambda: region_atlas_for(settings))
            windows = estimated_windows(inputs)
            details["segments"] = [{"id": seg.id, "codes": seg.codes, "estimatedMs": w.duration_ms}
                                   for seg, w in zip(segments, windows)]
            details["estimatedMs"] = windows[-1].end_ms
            checks.append(CheckResult(name="script", ok=True, detail=f"{len(segments)} segments"))
            found = lint.check(inputs, windows)
            details["lint"] = [asdict(f) for f in found]
            errors = [f.message for f in found if f.severity == "error"]
            checks.append(CheckResult(name="hook", ok=not errors,
                                      detail="; ".join(errors) if errors else f"{len(found)} suggestions"))
        except InvalidInput as exc:
            path = exc.details.get("path", "")
            checks.append(CheckResult(name="script", ok=False, detail=f"{path}: {exc.message}"))
            details["error"] = {"path": path, "message": exc.message, "hint": exc.hint}
        except ProviderUnavailable as exc:
            checks.append(CheckResult(name="map_data", ok=False,
                                      detail=f"{exc.message} {exc.hint or ''}".strip()))
        details["cost"] = {"ttsCharacters": sum(len(spoken_text(parse_markup(s.narration))) for s in inputs.segments),
                           "voice": inputs.voice}
        providers = ctx.providers
        renderer = providers.get("renderer") if providers else None
        problems = renderer.missing() if renderer is not None else ["no renderer bound"]
        checks.append(CheckResult(name="renderer", ok=not problems,
                                  detail="; ".join(problems) or getattr(renderer, "name", "")))
        bound = getattr(providers.get("narration") if providers else None, "name", "")
        checks.append(CheckResult(name="voice", ok=provider_for(inputs.voice) == bound,
                                  detail=f"requested {provider_for(inputs.voice)!r}, deployment serves {bound!r}"))
        return ProbeReport(ok=all(c.ok for c in checks), checks=checks, details=details)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> None:
        return None
