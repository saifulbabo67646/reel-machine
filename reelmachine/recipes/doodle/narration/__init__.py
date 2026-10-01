"""Narration providers (TTS with timings).

The protocol lives with the recipe that needs it (ADR-0011). The deterministic fake
ships always; the cloud adapters are real implementations whose payload parsing is unit
tested without a network.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ....core.assets import Licence
from ....core.timeline import TimedSpeech
from pydantic import BaseModel, ConfigDict


class Voice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""
    licence: Licence | None = None
    notes: str = ""


@runtime_checkable
class NarrationProvider(Protocol):
    name: str

    def voices(self) -> list[Voice] | None: ...

    def synthesize(self, text: str, *, voice: str, language: str = "en") -> TimedSpeech: ...

    def missing(self) -> list[str]: ...

    def describe(self) -> dict: ...


def provider_for(name: str) -> str:
    """`elevenlabs:Rachel` → `elevenlabs`; `fake:default` → `fake`."""
    return (name or "").split(":", 1)[0].strip().lower() or "fake"


def voice_id(name: str) -> str:
    _, _, identifier = (name or "").partition(":")
    return identifier.strip() or "default"
