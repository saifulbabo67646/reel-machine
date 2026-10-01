"""Cloud narration providers: ElevenLabs and Cartesia.

Both are plain HTTP (httpx is already a dependency), so they need no extra — only a key.
A missing or unlicensed voice fails loudly with `VOICE_UNAVAILABLE`; a lower-quality
substitute is never chosen silently (the recipe may fall back to the fake only when the
caller explicitly allows visible degradation).

The vendor payload parsers are module-level so they can be unit tested against recorded
shapes with no network.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx

from ....config import Settings
from ....core.assets import Licence, Provenance
from ....core.errors import ProviderUnavailable, VoiceUnavailable
from ....core.timeline import SpeechSegment, TimedSpeech, WordSpan
from . import Voice, voice_id

CARTESIA_BASE = "https://api.cartesia.ai"
ELEVENLABS_BASE = "https://api.elevenlabs.io"


def words_from_characters(
    characters: list[str],
    starts: list[float],
    ends: list[float],
) -> list[WordSpan]:
    """Group character-level alignment into word spans (ElevenLabs' shape)."""
    spans: list[WordSpan] = []
    current = ""
    start_ms = 0
    end_ms = 0
    for character, start, end in zip(characters, starts, ends):
        if character.isspace():
            if current:
                spans.append(WordSpan(text=current, start_ms=start_ms, end_ms=max(start_ms + 1, end_ms)))
                current = ""
            continue
        if not current:
            start_ms = int(round(start * 1000))
        current += character
        end_ms = int(round(end * 1000))
    if current:
        spans.append(WordSpan(text=current, start_ms=start_ms, end_ms=max(start_ms + 1, end_ms)))
    return spans


def parse_elevenlabs(payload: dict[str, Any]) -> tuple[bytes, list[WordSpan]]:
    audio = base64.b64decode(payload.get("audio_base64") or "")
    alignment = payload.get("normalized_alignment") or payload.get("alignment") or {}
    spans = words_from_characters(
        list(alignment.get("characters") or []),
        [float(value) for value in alignment.get("character_start_times_seconds") or []],
        [float(value) for value in alignment.get("character_end_times_seconds") or []],
    )
    return audio, spans


def _timestamp_ms(entry: dict[str, Any], primary: str, fallback: str) -> int:
    """`start_ms`/`end_ms` are milliseconds; `start`/`end` are seconds."""
    if entry.get(primary) is not None:
        return int(round(float(entry[primary])))
    return int(round(float(entry.get(fallback, 0)) * 1000))


def parse_cartesia(payload: dict[str, Any]) -> tuple[bytes, list[WordSpan]]:
    audio = base64.b64decode(payload.get("audio") or payload.get("audio_base64") or "")
    spans = [
        WordSpan(
            text=str(entry.get("word") or ""),
            start_ms=_timestamp_ms(entry, "start_ms", "start"),
            end_ms=_timestamp_ms(entry, "end_ms", "end"),
        )
        for entry in payload.get("word_timestamps") or []
    ]
    return audio, spans


class CloudNarration:
    """Base for the vendor adapters; one implementation per vendor, one key each."""

    name = "cloud"
    vendor = ""
    env_key = ""
    base_url = ""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        base_url: str | None = None,
        workdir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = (base_url or self.base_url).rstrip("/")
        self.api_key = os.environ.get(self.env_key, "") if self.env_key else ""
        self._client = client
        self.workdir = Path(workdir) if workdir else None
        self._voices: list[Voice] | None = None

    # -- plumbing ---------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=120.0, follow_redirects=True)
        return self._client

    def missing(self) -> list[str]:
        return [f"{self.env_key} is not set (needed for the {self.vendor} voice)"] if not self.api_key else []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "narration",
            "description": f"{self.vendor} text-to-speech with word timings",
            "missing": self.missing(),
        }

    def voices(self) -> list[Voice] | None:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _synthesize(self, text: str, voice: str, language: str) -> tuple[bytes, list[WordSpan], str]:
        raise NotImplementedError

    # -- the protocol -----------------------------------------------------------
    def synthesize(self, text: str, *, voice: str = "", language: str = "en") -> TimedSpeech:
        if not self.api_key:
            raise VoiceUnavailable(
                f"the {self.vendor} voice {voice!r} needs an API key",
                hint=f"set {self.env_key}, or choose an available voice",
                details={"vendor": self.vendor, "voice": voice},
            )
        requested = voice_id(voice)
        known = self.voices()
        if known:
            ids = {entry.id for entry in known}
            names = {entry.name.lower(): entry.id for entry in known if entry.name}
            if requested not in ids and requested.lower() not in names:
                raise VoiceUnavailable(
                    f"voice {requested!r} does not exist for the {self.vendor} provider",
                    hint=f"choose one of: {', '.join(sorted(ids)[:8])}",
                    details={"vendor": self.vendor, "voice": requested},
                )
        audio, spans, resolved = self._synthesize(text, requested, language)
        if not audio:
            raise ProviderUnavailable(
                f"the {self.vendor} provider returned no audio",
                hint="retry; if it persists, check the account's quota",
            )
        suffix = ".mp3"
        path = (self.workdir or Path.cwd()) / f"narration-{self.vendor}-{abs(hash(text)) % 10_000_000:07d}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        duration_ms = max((span.end_ms for span in spans), default=0)
        segment = SpeechSegment(text=text, start_ms=0, end_ms=duration_ms, words=spans)
        return TimedSpeech(
            path=path,
            duration_ms=duration_ms,
            segments=[segment],
            voice=f"{self.vendor}:{resolved}",
            provenance=Provenance(provider=f"{self.vendor}", source=self.base_url),
            licence=Licence(
                name=f"{self.vendor} terms",
                url=self.base_url,
                attribution=f"voice {resolved}",
            ),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class ElevenLabsNarration(CloudNarration):
    name = "elevenlabs"
    vendor = "elevenlabs"
    env_key = "ELEVENLABS_API_KEY"
    base_url = ELEVENLABS_BASE

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self.api_key, "accept": "application/json"}

    def voices(self) -> list[Voice] | None:
        if self._voices is not None:
            return self._voices
        try:
            response = self._http().get(f"{self.base_url}/v1/voices", headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None  # a listing failure must not fake a verdict; the call itself will
        self._voices = [
            Voice(id=str(entry.get("voice_id") or ""), name=str(entry.get("name") or ""))
            for entry in payload.get("voices") or []
            if entry.get("voice_id")
        ]
        return self._voices

    def _synthesize(self, text: str, voice: str, language: str) -> tuple[bytes, list[WordSpan], str]:
        response = self._http().post(
            f"{self.base_url}/v1/text-to-speech/{voice}/with-timestamps",
            headers=self._headers(),
            json={"text": text, "model_id": os.environ.get("REEL_ELEVENLABS_MODEL", "eleven_multilingual_v2")},
        )
        response.raise_for_status()
        audio, spans = parse_elevenlabs(response.json())
        return audio, spans, voice


class CartesiaNarration(CloudNarration):
    name = "cartesia"
    vendor = "cartesia"
    env_key = "CARTESIA_API_KEY"
    base_url = CARTESIA_BASE

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Cartesia-Version": os.environ.get("REEL_CARTESIA_VERSION", "2024-06-10"),
            "content-type": "application/json",
        }

    def voices(self) -> list[Voice] | None:
        if self._voices is not None:
            return self._voices
        try:
            response = self._http().get(f"{self.base_url}/voices", headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None
        entries = payload if isinstance(payload, list) else payload.get("data") or []
        self._voices = [
            Voice(id=str(entry.get("id") or ""), name=str(entry.get("name") or ""))
            for entry in entries
            if entry.get("id")
        ]
        return self._voices

    def _synthesize(self, text: str, voice: str, language: str) -> tuple[bytes, list[WordSpan], str]:
        response = self._http().post(
            f"{self.base_url}/tts/bytes",
            headers=self._headers(),
            json={
                "model_id": os.environ.get("REEL_CARTESIA_MODEL", "sonic-2"),
                "transcript": text,
                "voice": {"mode": "id", "id": voice},
                "language": language,
                "add_timestamps": True,
                "output_format": {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000},
            },
        )
        response.raise_for_status()
        if response.headers.get("content-type", "").startswith("application/json"):
            audio, spans = parse_cartesia(response.json())
            return audio, spans, voice
        return response.content, [], voice
