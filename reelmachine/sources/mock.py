"""Mock provider: a fully synthetic episode, for testing without any server.

Builds a deterministic fake episode (video + speech-like audio bursts) and the
matching fabricated segments, deliberately shifting the segments' timestamps by
a known amount so that a passing run *proves* the alignment step measured
something real rather than echoing the input.

Set `REEL_MOCK_OFFSET_MS` to change the simulated head offset (default 2500 ms,
i.e. your copy has 2.5 s more before the first line).
"""

from __future__ import annotations

import hashlib
import os
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .. import ffmpeg
from ..config import Settings, get_settings
from ..models import Segment, TextJa, Translation
from .base import EpisodeAsset, SourceProvider, UnresolvedEpisode

DEFAULT_DURATION_S = 150
SAMPLE_RATE = 48000

PLACEHOLDER_LINES = [
    ("彼女は俺の幼馴染だ。", "She's my childhood friend.", "Ella es mi amiga de la infancia."),
    ("そんなの、ずるいよ……。", "That's just not fair...", "Eso no es justo..."),
    ("絶対に諦めないから。", "I'm definitely not giving up.", "Definitivamente no me rendiré."),
    ("お前、本当にバカだな。", "You really are an idiot.", "De verdad eres un idiota."),
    ("これからも、ずっと一緒にいよう。", "Let's always stay together from now on.", "Sigamos juntos de ahora en adelante."),
    ("夢を叶えるって、そういうことだろ？", "That's what chasing a dream means, right?", "Eso es lo que significa cumplir un sueño, ¿no?"),
    ("泣いてなんかないよ。", "I'm not crying.", "No estoy llorando."),
    ("約束する、必ず戻ってくる。", "I promise, I'll definitely come back.", "Lo prometo, volveré sin falta."),
]


def _seed_for(media_public_id: str, episode: int) -> int:
    digest = hashlib.sha256(f"{media_public_id}:{episode}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


class MockProvider(SourceProvider):
    name = "mock"

    def __init__(self, settings: Settings | None = None):
        super().__init__(settings)
        self.duration_s = int(os.environ.get("REEL_MOCK_DURATION", DEFAULT_DURATION_S))
        self.source_offset_ms = int(os.environ.get("REEL_MOCK_OFFSET_MS", "2500"))
        self._tracks: dict[str, tuple[np.ndarray, list[tuple[int, int]]]] = {}

    def missing(self) -> list[str]:
        return []  # synthetic media; nothing to configure (ffmpeg is checked globally)

    # -------------------------------------------------------------- audio model
    def track(self, media_public_id: str, episode: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
        """Deterministic audio + the (start_ms, duration_ms) of each speech burst.

        Each burst is a run of syllables with a random rhythm, which is what
        gives its RMS envelope a distinctive shape.  Flat noise bursts would be
        statistically interchangeable and would make the alignment test
        meaningless.
        """
        key = f"{media_public_id}:{episode}"
        if key in self._tracks:
            return self._tracks[key]

        rng = np.random.default_rng(_seed_for(media_public_id, episode))
        total = int(self.duration_s * SAMPLE_RATE)
        audio = (rng.standard_normal(total) * 0.008).astype(np.float32)  # room tone floor

        kernel = np.ones(48, dtype=np.float32) / 48.0  # crude band limiting
        bursts: list[tuple[int, int]] = []
        cursor = 6000
        limit = int(self.duration_s * 1000) - 8000
        while cursor < limit:
            # A phrase: syllables of varying length separated by short gaps.
            phrase: list[np.ndarray] = []
            for _ in range(int(rng.integers(4, 12))):
                syllable_ms = int(rng.integers(55, 230))
                count = max(1, int(syllable_ms * SAMPLE_RATE / 1000))
                voice = rng.standard_normal(count).astype(np.float32)
                voice = np.convolve(voice, kernel, mode="same")
                # A pitch-ish carrier so syllables differ from one another.
                carrier = np.sin(
                    2 * np.pi * float(rng.uniform(90.0, 260.0)) * np.arange(count) / SAMPLE_RATE
                ).astype(np.float32)
                voice = voice * (0.35 + 0.65 * np.abs(carrier)) * float(rng.uniform(0.5, 1.0))
                phrase.append(voice)
                gap_ms = int(rng.integers(0, 90))
                if gap_ms:
                    phrase.append(np.zeros(int(gap_ms * SAMPLE_RATE / 1000), dtype=np.float32))
            utterance = np.concatenate(phrase) if phrase else np.zeros(0, dtype=np.float32)
            if utterance.size == 0:
                break

            start = int(cursor * SAMPLE_RATE / 1000)
            end = min(total, start + utterance.size)
            if end > start:
                audio[start:end] += utterance[: end - start] * 0.85
                bursts.append((cursor, int(round(utterance.size * 1000 / SAMPLE_RATE))))
            cursor += int(round(utterance.size * 1000 / SAMPLE_RATE)) + int(rng.integers(2200, 6000))

        np.clip(audio, -1.0, 1.0, out=audio)
        self._tracks[key] = (audio, bursts)
        return audio, bursts

    def _write_wav(self, path: Path, samples: np.ndarray) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        pcm = np.clip(samples, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype("<i2")
        with wave.open(str(path), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(SAMPLE_RATE)
            fh.writeframes(pcm16.tobytes())
        return path

    # ------------------------------------------------------------------- assets
    def episode_path(self, media_public_id: str, episode: int) -> Path:
        return self.settings.mock_dir / f"{media_public_id}_ep{episode:03d}.mkv"

    def resolve(self, media: Any = None, episode: int = 1, **_: Any) -> EpisodeAsset:
        media_public_id = getattr(media, "publicId", None) or "MOCKMEDIA001"
        path = self.episode_path(media_public_id, episode)
        if not path.is_file():
            self._build_episode(media_public_id, episode, path)
        return EpisodeAsset(
            media_public_id=media_public_id,
            episode=episode,
            url=str(path),
            duration_ms=self.duration_s * 1000,
            label=f"mock {media_public_id} ep{episode}",
            local_path=path,
            meta={"mock_offset_ms": self.source_offset_ms},
        )

    def _build_episode(self, media_public_id: str, episode: int, dest: Path) -> Path:
        audio, _ = self.track(media_public_id, episode)
        wav = dest.with_suffix(".wav")
        self._write_wav(wav, audio)
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=640x360:rate=30:duration={self.duration_s}",
            "-i",
            str(wav),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            str(SAMPLE_RATE),
            "-shortest",
            "-y",
            str(dest),
        ]
        ffmpeg.run(cmd)
        wav.unlink(missing_ok=True)
        return dest

    # ---------------------------------------------------------------- fake data
    def make_segments(self, media_public_id: str, episode: int, *, count: int = 3) -> list[Segment]:
        """Fabricate segments whose timestamps sit `source_offset_ms` *before* the
        real audio, exactly like Nadeshiko timestamps sitting before your copy's."""
        _, bursts = self.track(media_public_id, episode)
        if not bursts:
            raise UnresolvedEpisode("mock episode has no speech bursts")
        step = max(1, len(bursts) // max(1, count))
        chosen = bursts[::step][:count]
        segments: list[Segment] = []
        for index, (start_ms, duration_ms) in enumerate(chosen):
            line_ja, line_en, line_es = PLACEHOLDER_LINES[index % len(PLACEHOLDER_LINES)]
            public_id = hashlib.sha256(f"{media_public_id}{episode}{index}".encode()).hexdigest()[:12]
            src_start = max(0, start_ms - self.source_offset_ms)
            segments.append(
                Segment(
                    publicId=public_id,
                    position=index,
                    status="ACTIVE",
                    startTimeMs=src_start,
                    endTimeMs=src_start + duration_ms,
                    contentRating="SAFE",
                    episode=episode,
                    externalVideoId="MOCKVIDEOID",
                    mediaPublicId=media_public_id,
                    textJa=TextJa(content=line_ja, highlight=None, tokens=None),
                    textEn=Translation(content=line_en, isMachineTranslated=False, highlight=None),
                    textEs=Translation(content=line_es, isMachineTranslated=False, highlight=None),
                    urls={
                        "imageUrl": "file://mock/image.jpg",
                        "audioUrl": str(self.settings.mock_dir / "clips" / f"{public_id}.wav"),
                        "videoUrl": "file://mock/clip.mp4",
                    },
                )
            )
        return segments

    def reference_clips(
        self,
        segments: Any,
        *,
        client: Any = None,
        asset: EpisodeAsset | None = None,
    ) -> dict[str, Path]:
        """Slice the ground-truth audio for each synthetic segment."""
        segments = list(segments)
        if not segments:
            return {}
        media_public_id = segments[0].mediaPublicId
        episode = segments[0].episode
        audio, _ = self.track(media_public_id, episode)
        out: dict[str, Path] = {}
        for segment in segments:
            start = int((segment.startTimeMs + self.source_offset_ms) * SAMPLE_RATE / 1000)
            end = int((segment.endTimeMs + self.source_offset_ms) * SAMPLE_RATE / 1000)
            slice_ = audio[max(0, start) : min(audio.size, end)]
            if slice_.size == 0:
                continue
            path = self.settings.mock_dir / "clips" / f"{segment.publicId}.wav"
            out[segment.publicId] = self._write_wav(path, slice_)
        return out

    def expected_offset_ms(self) -> int:
        return self.source_offset_ms
