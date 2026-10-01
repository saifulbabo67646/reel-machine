"""A deterministic narration voice for tests and dry runs.

One tone burst per word, so the timings it reports are exact and the reveal schedule the
recipe builds from them is real. No network, no licence, no GPU.
"""

from __future__ import annotations

import hashlib
import re
import wave
from pathlib import Path
from typing import Any

import numpy as np

from ....config import Settings
from ....core.assets import Licence, Provenance
from ....core.timeline import SpeechSegment, TimedSpeech, WordSpan
from . import Voice, voice_id

SAMPLE_RATE = 48000
WORD_MS = 260
COMMA_MS = 140
STOP_MS = 320


class FakeNarration:
    name = "fake"

    def __init__(self, settings: Settings | None = None, *, workdir: Path | None = None) -> None:
        self.settings = settings
        self.workdir = Path(workdir) if workdir else None

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "narration",
            "description": "Deterministic tone voice with exact word timings",
            "missing": [],
        }

    def voices(self) -> list[Voice]:
        return [
            Voice(
                id="fake:default",
                name="Deterministic tone voice",
                licence=Licence(name="CC0-1.0", attribution="reel-machine fake voice"),
            )
        ]

    @staticmethod
    def _seed(text: str) -> int:
        return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)

    def synthesize(self, text: str, *, voice: str = "fake:default", language: str = "en") -> TimedSpeech:
        tokens = [token for token in re.split(r"\s+", text.strip()) if token]
        seed = self._seed(f"{voice_id(voice)}|{language}|{text}")
        base_frequency = 180.0 + (seed % 60)

        samples: list[np.ndarray] = []
        words: list[WordSpan] = []
        cursor_ms = 0
        for index, token in enumerate(tokens):
            clean = re.sub(r"[^\w\u00c0-\u024f\u0600-\u06ff]+$", "", token) or token
            duration = WORD_MS + 18 * (len(clean) % 5)
            frequency = base_frequency + 12.0 * ((seed + index) % 5)
            samples.append(_tone(frequency, duration))
            words.append(WordSpan(text=token, start_ms=cursor_ms, end_ms=cursor_ms + duration))
            cursor_ms += duration
            pause = STOP_MS if token.endswith((".", "!", "?", "\u3002")) else (COMMA_MS if token.endswith((",", ";", ":")) else 60)
            samples.append(np.zeros(int(SAMPLE_RATE * pause / 1000), dtype=np.float32))
            cursor_ms += pause

        track = np.concatenate(samples) if samples else np.zeros(1, dtype=np.float32)
        path = (self.workdir or Path.cwd()) / f"narration-{seed % 10_000_000:07d}.wav"
        _write_wav(path, track)

        segment = SpeechSegment(
            text=text,
            start_ms=0,
            end_ms=cursor_ms,
            words=words,
            meta={"beatId": None},
        )
        return TimedSpeech(
            path=path,
            duration_ms=cursor_ms,
            segments=[segment],
            voice=voice or "fake:default",
            provenance=Provenance(provider=self.name, provider_version="1", source="synthetic"),
            licence=Licence(name="CC0-1.0", attribution="reelmachine fake voice"),
        )


def _tone(frequency: float, duration_ms: int) -> np.ndarray:
    count = max(1, int(SAMPLE_RATE * duration_ms / 1000))
    time = np.arange(count, dtype=np.float32) / SAMPLE_RATE
    envelope = np.minimum(1.0, np.minimum(time * 60.0, (duration_ms / 1000 - time) * 60.0))
    envelope = np.clip(envelope, 0.0, 1.0)
    return (0.3 * envelope * np.sin(2 * np.pi * frequency * time)).astype(np.float32)


def _write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((pcm * 32767).astype("<i2").tobytes())
