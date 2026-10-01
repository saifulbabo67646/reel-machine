"""Script → beats.

A structured script is used exactly as given. A topic goes through a deterministic
template provider — no generative step is required, and a deployment that wants one can
supply beats instead (or register its own provider the same way as any other).
"""

from __future__ import annotations

from .models import Beat

TEMPLATES = (
    ("hook", "Most people get {topic} wrong, and it costs them."),
    ("shape", "Here is how {topic} actually works, in one picture."),
    ("payoff", "Do this with {topic}, and the result compounds."),
)


class TemplateScriptProvider:
    """A deterministic three-beat script for any topic."""

    name = "template"

    def beats(self, topic: str, *, language: str = "en") -> list[Beat]:
        cleaned = (topic or "this idea").strip().rstrip(".")
        beats: list[Beat] = []
        for index, (beat_id, template) in enumerate(TEMPLATES, start=1):
            narration = template.format(topic=cleaned)
            keywords = self._keywords(cleaned)
            beats.append(
                Beat(id=f"beat-{index}", narration=narration, keywords=keywords)
            )
        return beats

    @staticmethod
    def _keywords(topic: str) -> list[str]:
        words = [word.strip(".,!?").capitalize() for word in topic.split() if len(word) > 2]
        return words[:3] or ["Idea"]
