"""The story brain: a provider group that turns a transcript into creative work.

`llm` does it with an OpenAI-compatible chat model; `fake` is the deterministic test
double. A calling agent can bypass both — a caller-supplied concept list or story
package is used exactly as given — but when the engine runs on its own, this group is
what finds the viral angles and writes the storytelling script.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..models import ResolvedMedia, SceneChunk, StoryConcept, StoryPackage, StoryReelInputs


@runtime_checkable
class StoryBrain(Protocol):
    name: str

    def missing(self) -> list[str]: ...

    def describe(self) -> dict[str, Any]: ...

    def concepts(
        self,
        *,
        scenes: list[SceneChunk],
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        on_detail: Any = None,
    ) -> list[StoryConcept]: ...

    def script(
        self,
        *,
        scenes: list[SceneChunk],
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        concept: StoryConcept,
        on_detail: Any = None,
    ) -> StoryPackage: ...


def describe_row(name: str, description: str, missing: list[str], **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name,
        "group": "story",
        "description": description,
        "missing": list(missing),
    }
    row.update(extra)
    return row
