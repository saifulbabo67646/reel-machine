"""A deterministic brain for engine tests and offline runs.

It writes five concepts and a section list straight from the transcript's own clock —
no network, no model, same answer every time — so the pipeline around it (clip windows,
voiceover timing, render) can be tested for real.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import (
    MicroClipSpec,
    ResolvedMedia,
    SceneChunk,
    SeoPackage,
    StoryConcept,
    StoryPackage,
    StoryReelInputs,
    StorySection,
)
from ..selection import section_budget
from . import describe_row

_ANCHORS = (0.10, 0.30, 0.50, 0.70, 0.88)
_KINDS = ("emotional", "shock", "suspense", "turning-point", "life-lesson")
_PREFIXES = ("", "But then, ", "Nobody knew that ", "And at that moment, ", "What no one saw coming: ")


class FakeStoryBrain:
    name = "fake"
    version = "v1"

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "A deterministic brain: five concepts and a section list, straight from the transcript",
            [],
        )

    def concepts(
        self,
        *,
        scenes: list[SceneChunk],
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        on_detail: Any = None,
    ) -> list[StoryConcept]:
        chosen = _anchors(scenes)
        concepts: list[StoryConcept] = []
        for index, scene in enumerate(chosen, start=1):
            excerpt = _excerpt(scene.text, 8)
            concepts.append(
                StoryConcept(
                    id=f"c{index}",
                    name=f"{excerpt.rstrip('.')}" if excerpt else f"Story {index}",
                    angle=_KINDS[(index - 1) % len(_KINDS)],
                    why_viral=f"test concept anchored at {scene.id}",
                    emotional_score=5 + (index % 5),
                    viral_score=4 + ((index + 1) % 5),
                    retention_score=6 + ((index + 2) % 4),
                    characters=_names(scene.text),
                    summary=excerpt,
                    audience_reaction="test reaction",
                )
            )
        return concepts

    def script(
        self,
        *,
        scenes: list[SceneChunk],
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        concept: StoryConcept,
        on_detail: Any = None,
    ) -> StoryPackage:
        if not scenes:
            return StoryPackage(sections=[], seo=None)
        anchor = _anchor_index(concept, scenes)
        count = max(5, min(section_budget(inputs.target_ms), len(scenes)))
        step = max(1, len(scenes) // count)
        picks = [scenes[(anchor + index * step) % len(scenes)] for index in range(count)]
        sections: list[StorySection] = []
        for index, scene in enumerate(picks, start=1):
            words = scene.text.split()
            spoken = " ".join(words[:12])
            prefix = _PREFIXES[index % len(_PREFIXES)]
            voiceover = f"{prefix}{spoken}".strip()
            if index % 5 == 3:
                voiceover += " [Pause]"
            end = min(scene.end_ms, scene.start_ms + 4_000)
            sections.append(
                StorySection(
                    id=f"sec-{index:03d}",
                    voiceover=voiceover,
                    clip=MicroClipSpec(
                        scene_ref=scene.id,
                        source_start_ms=scene.start_ms,
                        source_end_ms=max(scene.start_ms + 1_200, end),
                        scene=_excerpt(scene.text, 6),
                        zoom=("none", "in", "out")[index % 3],
                        text_overlay=_excerpt(scene.text, 2).upper() if index % 4 == 0 else "",
                        transition="cut",
                    ),
                )
            )
        return StoryPackage(
            concept_id=concept.id,
            concept_name=concept.name,
            viral_reason=concept.why_viral,
            sections=sections,
            seo=SeoPackage(
                title=f"{media.title} — a story you will not forget",
                description=f"The story of {media.title}, told the way it deserves.",
                hashtags=["#story", "#movie", "#shorts"],
                hook="You will not believe how this ends.",
                cta="Follow for more stories.",
            ),
        )


def _anchors(scenes: list[SceneChunk]) -> list[SceneChunk]:
    if not scenes:
        return []
    return [scenes[min(len(scenes) - 1, int(len(scenes) * anchor))] for anchor in _ANCHORS]


def _anchor_index(concept: StoryConcept, scenes: list[SceneChunk]) -> int:
    digits = "".join(ch for ch in concept.id if ch.isdigit())
    position = int(digits) - 1 if digits else 0
    fraction = _ANCHORS[min(max(position, 0), len(_ANCHORS) - 1)]
    return min(len(scenes) - 1, int(len(scenes) * fraction))


def _excerpt(text: str, words: int) -> str:
    parts = text.split()
    return " ".join(parts[:words]).strip()


def _names(text: str) -> list[str]:
    seen: list[str] = []
    for match in re.findall(r"\b[A-Z][a-z]{2,}\b", text or ""):
        if match not in seen:
            seen.append(match)
    return seen[:3]
