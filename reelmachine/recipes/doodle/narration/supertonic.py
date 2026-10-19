"""Supertonic: a self-hosted Supertonic 3 server (31 languages, built-in and cloned voices).

It speaks the server's `/v1/tts/timestamps` route, which returns the audio and word
timings in one response. The server runs on the caller's own machine or network, so the
key is optional: a loopback server needs none, a LAN one is given `SUPERTONIC_API_KEY`.
Its word timings are estimated from sentence lengths rather than force-aligned; close
enough for captions, and the clip's real length still comes from the audio itself.
"""

from __future__ import annotations

import base64
import io
import json
import os
import wave
from typing import Any

import httpx

from ....core.assets import Licence, Provenance
from ....core.errors import ProviderUnavailable, VoiceUnavailable
from ....core.timeline import TimedSpeech, WordSpan
from . import Voice
from .cloud import CloudNarration

SUPERTONIC_BASE = "http://127.0.0.1:8787"


def parse_supertonic(payload: dict[str, Any]) -> tuple[bytes, list[WordSpan]]:
    audio = base64.b64decode(payload.get("audio_base64") or "")
    spans = [
        WordSpan(
            text=str(entry.get("text") or ""),
            start_ms=int(entry.get("start_ms") or 0),
            end_ms=max(int(entry.get("start_ms") or 0) + 1, int(entry.get("end_ms") or 0)),
        )
        for entry in payload.get("words") or []
        if entry.get("text")
    ]
    return audio, spans


def _server_message(response: httpx.Response) -> str:
    """The server's own words: `{"error": {"message": ...}}`, else the raw body."""
    try:
        error = response.json().get("error") or {}
        return str(error.get("message") or response.text)[:300]
    except (json.JSONDecodeError, ValueError, AttributeError):
        return response.text[:300]


class SupertonicNarration(CloudNarration):
    name = "supertonic"
    vendor = "supertonic"
    env_key = "SUPERTONIC_API_KEY"
    base_url = SUPERTONIC_BASE
    requires_key = False

    def __init__(self, *args: Any, base_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, base_url=base_url or os.environ.get("REEL_SUPERTONIC_URL") or None, **kwargs)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "description": "Self-hosted Supertonic 3 text-to-speech with word timings"}

    def voices(self) -> list[Voice] | None:
        if self._voices is not None:
            return self._voices
        try:
            response = self._http().get(f"{self.base_url}/v1/voices", headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None  # the synthesis call gives the real verdict
        self._voices = [
            Voice(id=str(entry.get("id") or ""), name=str(entry.get("name") or ""))
            for entry in payload.get("data") or []
            if entry.get("id")
        ]
        return self._voices

    def _synthesize(
        self, text: str, voice: str, language: str
    ) -> tuple[bytes, list[WordSpan], str, str]:
        try:
            response = self._http().post(
                f"{self.base_url}/v1/tts/timestamps",
                headers=self._headers(),
                json={"text": text, "voice": voice, "language": language or "en", "response_format": "wav"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"the Supertonic server at {self.base_url} did not answer ({type(exc).__name__})",
                hint="start the Supertonic server, or point REEL_SUPERTONIC_URL at it",
                details={"vendor": self.vendor, "voice": voice},
            ) from exc
        if response.status_code in (401, 403):
            raise VoiceUnavailable(
                f"the Supertonic server refused the request: {_server_message(response)}",
                hint="set SUPERTONIC_API_KEY to the server's key",
                details={"vendor": self.vendor, "voice": voice, "status": response.status_code},
            )
        if 400 <= response.status_code < 500:  # unknown voice, unsupported language, too long
            raise VoiceUnavailable(
                f"the Supertonic server cannot say this: {_server_message(response)}",
                hint="choose a voice and language the server lists at /v1/voices and /v1/languages",
                details={"vendor": self.vendor, "voice": voice, "status": response.status_code},
            )
        if response.status_code >= 500:
            raise ProviderUnavailable(
                f"the Supertonic server failed: {_server_message(response)}",
                hint="retry; if it persists, check the server's log",
                details={"vendor": self.vendor, "voice": voice, "status": response.status_code},
            )
        audio, spans = parse_supertonic(response.json())
        return audio, spans, voice, ".wav"

    def _duration_ms(self, audio: bytes, spans: list[WordSpan]) -> int:
        try:
            with wave.open(io.BytesIO(audio), "rb") as handle:
                frames, rate = handle.getnframes(), handle.getframerate()
        except (wave.Error, EOFError):
            frames, rate = 0, 0
        spoken = super()._duration_ms(audio, spans)
        return max(spoken, round(frames * 1000 / rate)) if rate else spoken

    def synthesize(self, text: str, *, voice: str = "", language: str = "en") -> TimedSpeech:
        speech = super().synthesize(text, voice=voice, language=language)
        # the model's weights are Supertone's, under OpenRAIL-M, whichever server runs them
        return speech.model_copy(
            update={
                "provenance": Provenance(provider=self.vendor, source=self.base_url),
                "licence": Licence(
                    name="Supertonic 3 model (OpenRAIL-M)",
                    url="https://huggingface.co/Supertone/supertonic-3",
                    attribution=f"voice {speech.voice.partition(':')[2]}",
                ),
            }
        )
