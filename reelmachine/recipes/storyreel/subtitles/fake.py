"""A deterministic subtitle for engine tests: a small story spread across the film.

It gives the pipeline dialogue with emotional beats at known times, so a test can
assert that scenes, concepts, clip windows and captions all line up without a network
or a real subtitle database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import SubtitleFetch, SubtitleRequest, describe_row

#: 36 lines, a complete little story with a hook, a turn and a pay-off.
FAKE_STORY: tuple[str, ...] = (
    "Maya returned to the lighthouse after twelve years away.",
    "The keeper's house still smelled of salt and old wood.",
    "She told herself she had only come to sell the place.",
    "That night the light turned on by itself.",
    "Nobody had lived here since her father vanished.",
    "The stairs to the lamp room were wet with fresh footprints.",
    "Her hands would not stop shaking.",
    "At the top she found his coat, folded, as if he meant to return.",
    "In the pocket was a letter with her name on it.",
    "It said: do not turn the light off, no matter what you hear.",
    "The storm arrived exactly as the letter promised.",
    "Out on the rocks a boat was breaking apart.",
    "She saw a figure in the water, waving.",
    "The radio was dead and the phone had no signal.",
    "Only the light could guide anyone home.",
    "But the letter had warned her. She remembered every word.",
    "Maya put her hand on the switch and froze.",
    "Somewhere below, a door slammed shut.",
    "She was not alone in the house.",
    "Footsteps climbed the spiral stairs, slow and patient.",
    "The voice that spoke her name was her father's.",
    "He stood in the doorway, soaked, older, impossible.",
    "You turned it off, he said. You turned the light off.",
    "She had not. Not yet. Not ever.",
    "Then she understood what had kept him away.",
    "The light was not a beacon for ships.",
    "It was a cage, and something inside it had been waiting.",
    "Her father had been guarding it all these years.",
    "Now the guard was gone and the door was open.",
    "The sea below went silent, like a held breath.",
    "Maya looked at the light, then at the dark water.",
    "Every instinct told her to run.",
    "But the boat was still out there, and the people on it had names.",
    "She pulled the switch and the beam cut through the storm.",
    "In its light, the thing in the water finally showed its face.",
    "And Maya understood that some stories never end. They only change keepers.",
)


class FakeSubtitleProvider:
    """Writes FAKE_STORY as an SRT across the film's duration."""

    name = "fake"
    version = "v1"

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "A deterministic test story, spread across the film's duration",
            [],
        )

    def fetch(self, request: SubtitleRequest, *, dest_dir: Path) -> SubtitleFetch:
        duration = max(60_000, int(request.duration_ms or 300_000))
        count = len(FAKE_STORY)
        step = duration / count
        blocks: list[str] = []
        for index, line in enumerate(FAKE_STORY, start=1):
            start = int((index - 1) * step + step * 0.1)
            end = int(start + step * 0.8)
            blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{line}\n")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "fake-story.srt"
        dest.write_text("\n".join(blocks), encoding="utf-8")
        return SubtitleFetch(
            path=str(dest),
            language=request.primary_language() or "en",
            source=self.name,
            meta={"lines": count, "fixture": True},
        )


def _srt_time(ms: int) -> str:
    ms = max(0, int(ms))
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
