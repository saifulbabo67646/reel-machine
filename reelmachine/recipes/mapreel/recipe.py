"""The mapreel recipe: a script of map segments becomes a narrated satellite-map reel."""

from __future__ import annotations

from typing import Any

from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec
from ...core.stage import StageContext
from .models import MapReelInputs

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
        stages=(),
        providers={
            "narration": ProviderReq(group="narration", default="fake",
                                     description="Text-to-speech with word timings"),
            "renderer": ProviderReq(group="renderers", default="hyperframes",
                                    description="Renders each segment (Node >= 22, HyperFrames, Chrome, ffprobe)"),
        },
        cost=CostNote(unit="tts_characters", estimate="narration length",
                      notes="the fake voice is free; cloud voices bill per character",
                      providers=("elevenlabs", "cartesia", "fake")),
        artifacts=("reel.mp4", "captions.srt", "reel.json", "timeline.json", "storyboard.jpg",
                   "script.resolved.json"),
        render_modes=("hyperframes",),
        example=EXAMPLE,
    )

    def probe(self, inputs: MapReelInputs, ctx: ProbeContext) -> ProbeReport:
        return ProbeReport(ok=True)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> None:
        return None
