"""What a line hands the voice: its `say` with the line's expression as the provider's own tag."""

from __future__ import annotations

import os
import re

from reelmachine.core.timeline import WordSpan

from .models import Line

# the tag each provider reads as a laugh; a provider missing here says the line plainly
_LAUGH = {"supertonic": "<laugh>", "cartesia": "[laughter]"}
_TAG = re.compile(r"^(?:<[a-z]+>|\[[a-z ]+\])$")


def laugh_tag(provider: str) -> str | None:
    if provider == "elevenlabs":  # only the v3 model reads audio tags; v2 would say "laughs" out loud
        return "[laughs]" if os.environ.get("REEL_ELEVENLABS_MODEL", "").startswith("eleven_v3") else None
    return _LAUGH.get(provider)


def spoken(line: Line, provider: str) -> str:
    """The text sent to the voice: `say`, with the expression's tag at its start or end when the provider has one."""
    tag = laugh_tag(provider) if line.expression == "laugh" else None
    if tag is None:
        return line.say
    return f"{tag} {line.say}" if line.expression_at == "start" else f"{line.say} {tag}"


def drop_tags(words: list[WordSpan]) -> list[WordSpan]:
    """The spoken words without the tags some voices time as words of their own."""
    return [w for w in words if not _TAG.match(w.text.strip())]
