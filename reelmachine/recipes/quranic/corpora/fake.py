"""A deterministic Quran corpus for tests and dry runs.

It synthesises one short tone burst per word, so the audio has real, known word
boundaries: the highlight timings it returns are exact, not proportional, which is what
makes the quranic tests meaningful without a network.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np

from ....config import Settings
from ....core.assets import Licence, Provenance
from ....core.timeline import WordSpan
from ..models import AyahMaterial, QuranSelection, QuranicInputs

SAMPLE_RATE = 48000
WORD_MS = 420
GAP_MS = 80

FAKE_AYAHS: dict[int, list[tuple[str, str]]] = {
    1: [
        ("بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ", "In the name of Allah, the Entirely Merciful"),
        ("الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ", "All praise is due to Allah, Lord of the worlds"),
        ("الرَّحْمَٰنِ الرَّحِيمِ", "The Entirely Merciful, the Especially Merciful"),
    ],
    112: [
        ("قُلْ هُوَ اللَّهُ أَحَدٌ", "Say, He is Allah, the One"),
        ("اللَّهُ الصَّمَدُ", "Allah, the Eternal Refuge"),
    ],
}


class FakeQuranCorpus:
    name = "quran-fake"

    def __init__(self, settings: Settings | None = None, *, base_url: str = "fake://quran") -> None:
        self.settings = settings
        self.base_url = base_url

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "corpora",
            "description": "Deterministic fabricated verses with exact word timings",
            "missing": [],
        }

    def _ayah(self, surah: int, ayah: int, text: str, translation: str) -> AyahMaterial:
        words = text.split()
        spans: list[WordSpan] = []
        cursor = 0
        for word in words:
            spans.append(WordSpan(text=word, start_ms=cursor, end_ms=cursor + WORD_MS))
            cursor += WORD_MS + GAP_MS
        return AyahMaterial(
            surah=surah,
            ayah=ayah,
            text=text,
            words=words,
            translation=translation,
            audio_url=f"{self.base_url}/{surah}:{ayah}",
            word_timings=spans,
        )

    def fetch(self, inputs: QuranicInputs) -> QuranSelection:
        start, end = inputs.ayah_range()
        verses = FAKE_AYAHS.get(inputs.surah)
        if verses is None:
            verses = [
                (f"آيَةٌ {number}", f"Verse {number}") for number in range(1, (end or start) + 1)
            ]
        ayahs = [
            self._ayah(inputs.surah, index, text, translation)
            for index, (text, translation) in enumerate(verses, start=1)
            if start <= index <= end
        ]
        return QuranSelection(
            inputs=inputs,
            ayahs=ayahs,
            reciter_name="fake-reciter",
            translation_name="fake-translation",
            provenance=Provenance(provider=self.name, provider_version="1", source=self.base_url),
            licence=Licence(name="CC0-1.0", attribution="deterministic test corpus"),
            timings_source="provider",
        )

    def fetch_audio(self, url: str, dest: Path) -> Path:
        """Write the exact tone track the timings describe."""
        try:
            surah_text, ayah_text = url.rsplit("/", 1)[1].split(":")
            surah, ayah = int(surah_text), int(ayah_text)
        except (IndexError, ValueError) as exc:
            raise ValueError(f"the fake corpus cannot serve {url!r}") from exc
        verses = FAKE_AYAHS.get(surah)
        text = verses[ayah - 1][0] if verses and ayah <= len(verses) else f"آيَةٌ {ayah}"
        words = text.split()

        samples: list[np.ndarray] = []
        for position, _word in enumerate(words):
            frequency = 220.0 + 30.0 * ((surah + ayah + position) % 7)
            samples.append(self._tone(frequency, WORD_MS))
            samples.append(np.zeros(int(SAMPLE_RATE * GAP_MS / 1000), dtype=np.float32))
        track = np.concatenate(samples) if samples else np.zeros(1, dtype=np.float32)

        dest.parent.mkdir(parents=True, exist_ok=True)
        pcm = np.clip(track, -1.0, 1.0)
        with wave.open(str(dest), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes((pcm * 32767).astype("<i2").tobytes())
        return dest

    @staticmethod
    def _tone(frequency: float, duration_ms: int) -> np.ndarray:
        count = max(1, int(SAMPLE_RATE * duration_ms / 1000))
        time = np.arange(count, dtype=np.float32) / SAMPLE_RATE
        envelope = np.minimum(1.0, np.minimum(time * 40.0, (duration_ms / 1000 - time) * 40.0))
        envelope = np.clip(envelope, 0.0, 1.0)
        return (0.28 * envelope * np.sin(2 * np.pi * frequency * time)).astype(np.float32)

    def close(self) -> None:
        return None
