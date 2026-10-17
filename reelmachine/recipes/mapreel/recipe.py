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
from .guide import GUIDE
from . import lint
from .stages import (ComposeStage, GeoStage, ImagesStage, NarrationStage, PrepStage, RenderStage,
                     ScriptStage, SegmentsStage, check_music, estimated_windows, heard_names, resolve_segments)

WIKI = "https://en.wikipedia.org/wiki/"
NOODLES = ("https://www.koreatimes.co.kr/amp/southkorea/society/20251009/"
           "koreans-per-capita-ramyeon-consumption-ranks-2nd-after-vietnam")

EXAMPLE = {
    "mode": "storyboard",
    "title": "Foods you'd never eat",
    "segments": [
        {"kind": "highlight", "countries": ["FRA", "JPN", "ITA"], "legend": "Foods you'd never eat",
         "narration": "Three foods you'd never eat. Number one is banned for sale."},
        {"kind": "country", "country": "FRA",
         "narration": "France eats snails, and about 4,000 tonnes of frog legs a year.",
         "items": [{"name": "Snails", "emoji": "🐌", "source": WIKI + "Escargot"},
                   {"name": "Frog legs", "emoji": "🐸", "source": WIKI + "Frog_legs"}]},
        {"kind": "versus", "countries": ["VNM", "KOR", "JPN"],
         "stats": [{"value": "81", "label": "instant noodles per person, 2024", "source": NOODLES},
                   {"value": "79", "label": "instant noodles per person, 2024", "source": NOODLES},
                   {"value": "47", "label": "instant noodles per person, 2024", "source": NOODLES}],
         "narration": "Who eats the most instant noodles? Vietnam, 81. South Korea, 79. Japan, 47."},
        {"kind": "regions", "country": "JPN",
         "narration": "Even in Japan: Osaka eats takoyaki, octopus balls. Hokkaido grills mutton, jingisukan.",
         "regions": [{"region": "JP-27", "say": "Osaka",
                      "items": [{"name": "Takoyaki", "emoji": "🧆", "source": WIKI + "Takoyaki"},
                                {"name": "Octopus", "emoji": "🐙", "source": WIKI + "Takoyaki"}]},
                     {"region": "JP-01", "say": "Hokkaido",
                      "items": [{"name": "Mutton", "emoji": "🐑", "source": WIKI + "Jingisukan"},
                                {"name": "Jingisukan", "emoji": "🍖", "source": WIKI + "Jingisukan"}]}]},
        {"kind": "rank", "rank": 1, "country": "ITA", "source": WIKI + "Casu_martzu",
         "narration": "And number one has live maggots in it. Sardinia's casu martzu, so dangerous its sale is banned.",
         "items": [{"name": "Live maggots", "say": "maggots", "emoji": "🐛"}, {"name": "Sardinia", "emoji": "🏝️"},
                   {"name": "Casu martzu", "emoji": "🧀"}, {"name": "Dangerous", "emoji": "⚠️"},
                   {"name": "Sale banned", "say": "sale is banned", "emoji": "🚫"}]},
        {"kind": "highlight", "countries": ["FRA", "JPN", "ITA"],
         "narration": "Which one would you actually try? Tell me in the comments."},
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
            atlas = atlas_for(settings)
            segments = resolve_segments(inputs, atlas, lambda: region_atlas_for(settings))
            windows = estimated_windows(inputs)
            details["segments"] = [{"id": seg.id, "codes": seg.codes, "estimatedMs": w.duration_ms}
                                   for seg, w in zip(segments, windows)]
            details["estimatedMs"] = windows[-1].end_ms
            checks.append(CheckResult(name="script", ok=True, detail=f"{len(segments)} segments"))
            found = lint.check(inputs, windows, names=heard_names(inputs, segments, atlas))
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
