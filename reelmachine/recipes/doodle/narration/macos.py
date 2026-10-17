"""The macOS system voice (`say`), for local test renders with a real human-sounding voice.

`say` reports no word timings, so each clause (text up to a , . ! ? ; or :) is spoken on its
own: clause edges are then exact, and words inside a clause share its sound by syllable
count. Close enough for captions and pops; production uses a cloud voice with real timings.
Free, offline, macOS only.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np

from ....config import Settings
from ....core.assets import Licence, Provenance
from ....core.errors import ProviderUnavailable, VoiceUnavailable
from ....core.timeline import SpeechSegment, TimedSpeech, WordSpan
from . import Voice, voice_id

SAMPLE_RATE = 48000
COMMA_MS = 140
STOP_MS = 320
DEFAULT_VOICES = {"en": "Samantha"}
SILENCE = 0.02   # of full scale: quieter samples at a clause's edges are trimmed


def clauses(text: str) -> list[list[str]]:
    """Words grouped into clauses; each clause ends at a word carrying , . ! ? ; or :."""
    groups: list[list[str]] = [[]]
    for token in re.split(r"\s+", text.strip()):
        if not token:
            continue
        groups[-1].append(token)
        if token.endswith((",", ".", "!", "?", ";", ":", "。")):
            groups.append([])
    return [group for group in groups if group]


def syllables(word: str) -> int:
    """A rough count (vowel groups; digits count one each), never below one."""
    letters = re.sub(r"[^a-z0-9]", "", word.lower())
    count = len(re.findall(r"[aeiouy]+", letters)) + sum(ch.isdigit() for ch in letters)
    if letters.endswith("e") and count > 1 and not letters.endswith(("le", "ee")):
        count -= 1
    return max(1, count)


def spread(words: list[str], start_ms: int, end_ms: int) -> list[WordSpan]:
    """Share [start, end) between the words by syllables, plus a little per word."""
    weights = [syllables(word) + 0.5 for word in words]
    total, span = sum(weights), max(len(words), end_ms - start_ms)
    spans, cursor = [], float(start_ms)
    for word, weight in zip(words, weights):
        width = span * weight / total
        spans.append(WordSpan(text=word, start_ms=int(round(cursor)), end_ms=max(int(round(cursor)) + 1, int(round(cursor + width)))))
        cursor += width
    return spans


def trim(samples: np.ndarray) -> np.ndarray:
    loud = np.flatnonzero(np.abs(samples) > SILENCE)
    if not loud.size:
        return samples[:0]
    pad = int(SAMPLE_RATE * 0.02)
    return samples[max(0, loud[0] - pad): loud[-1] + pad]


class MacOSNarration:
    name = "macos"

    def __init__(self, settings: Settings | None = None, *, workdir: Path | None = None) -> None:
        self.settings = settings
        self.workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="reel-narration-"))

    def missing(self) -> list[str]:
        return [] if shutil.which("say") else ["the macOS `say` command (this voice runs on macOS only)"]

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "narration",
            "description": "macOS system voice for local tests; word timings are estimated per clause",
            "missing": self.missing(),
        }

    def voices(self) -> list[Voice]:
        if not shutil.which("say"):
            return []
        listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=False).stdout
        found = []
        for line in listing.splitlines():
            match = re.match(r"^(.+?)\s{2,}([a-z]{2}_[A-Z]{2})\s", line)
            if match:
                found.append(Voice(id=f"macos:{match.group(1).strip()}", name=f"{match.group(1).strip()} ({match.group(2)})",
                                   licence=_licence(), notes="local test voice"))
        return found

    def synthesize(self, text: str, *, voice: str = "macos:default", language: str = "en") -> TimedSpeech:
        if not shutil.which("say"):
            raise ProviderUnavailable("the macOS voice needs the `say` command", hint="run on macOS, or set REEL_TTS to another voice")
        name = voice_id(voice)
        if name == "default":
            name = DEFAULT_VOICES.get(language.split("-")[0], DEFAULT_VOICES["en"])
        key = hashlib.sha256(f"{name}|{language}|{text}".encode()).hexdigest()[:12]
        scratch = Path(tempfile.mkdtemp(prefix="say-", dir=self.workdir))
        parts: list[np.ndarray] = []
        words: list[WordSpan] = []
        cursor = 0
        for index, group in enumerate(clauses(text)):
            said = _say(" ".join(group), name, scratch / f"{index}.wav")
            start = cursor
            cursor += int(round(len(said) * 1000 / SAMPLE_RATE))
            words.extend(spread(group, start, cursor))
            parts.append(said)
            last = group[-1]
            pause = STOP_MS if last.endswith((".", "!", "?", "。")) else COMMA_MS
            parts.append(np.zeros(int(SAMPLE_RATE * pause / 1000), dtype=np.float32))
            cursor += pause
        shutil.rmtree(scratch, ignore_errors=True)
        track = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
        duration = int(round(len(track) * 1000 / SAMPLE_RATE))
        path = self.workdir / f"narration-{key}.wav"
        _write_wav(path, track)
        return TimedSpeech(
            path=path,
            duration_ms=duration,
            segments=[SpeechSegment(text=text, start_ms=0, end_ms=duration, words=words, meta={"beatId": None})],
            voice=f"macos:{name}",
            provenance=Provenance(provider=self.name, provider_version="1", source=f"say -v {name}"),
            licence=_licence(),
        )


def _licence() -> Licence:
    return Licence(name="macOS system voice (local tests only)", attribution="Apple macOS `say`")


def _say(text: str, voice: str, out: Path) -> np.ndarray:
    result = subprocess.run(
        ["say", "-v", voice, "-o", str(out), "--file-format=WAVE", f"--data-format=LEI16@{SAMPLE_RATE}", text],
        capture_output=True, text=True, check=False)
    if result.returncode != 0 or not out.is_file():
        raise VoiceUnavailable(f"macOS `say` could not use the voice {voice!r}: {result.stderr.strip() or 'no audio'}",
                               hint="list voices with `say -v '?'` and use 'macos:<name>'")
    with wave.open(str(out), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
        channels = handle.getnchannels()
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return trim(samples)


def _write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((pcm * 32767).astype("<i2").tobytes())
