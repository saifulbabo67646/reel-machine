"""Quran Foundation API v4: text, translations, recitation audio and word timings.

The parsers are module-level so they can be unit-tested against recorded payloads with no
network. Reciters and translations are resolved by name against the API's own listings
rather than hard-coded ids.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx

from ....config import Settings
from ....core.assets import Licence, Provenance
from ....core.errors import InvalidInput
from ....core.timeline import WordSpan
from ..models import AyahMaterial, QuranSelection, QuranicInputs

API_BASE = "https://api.quran.com/api/v4"

TRANSLATION_HINTS = {
    "en.sahih": "sahih international",
    "en.pickthall": "pickthall",
    "en.yusufali": "yusuf ali",
    "ur.jalandhry": "jalandhry",
    "fr.hamidullah": "hamidullah",
    "es.cortes": "cortes",
    "id.kemenag": "kemenag",
}

_TAGS = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return _TAGS.sub("", text or "").replace("&amp;", "&").strip()


def absolute(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return url


def parse_verses(payload: dict[str, Any], *, surah: int, start: int, end: int) -> list[AyahMaterial]:
    materials: list[AyahMaterial] = []
    for verse in payload.get("verses") or []:
        key = str(verse.get("verse_key") or "")
        try:
            ayah_number = int(key.split(":")[1])
        except (IndexError, ValueError):
            ayah_number = int(verse.get("verse_number") or 0)
        if not (start <= ayah_number <= end):
            continue
        words = [
            strip_html(word.get("text_uthmani") or word.get("text") or "")
            for word in verse.get("words") or []
        ]
        translations = verse.get("translations") or []
        translation = strip_html(translations[0].get("text", "")) if translations else ""
        materials.append(
            AyahMaterial(
                surah=surah,
                ayah=ayah_number,
                text=strip_html(verse.get("text_uthmani") or ""),
                words=[word for word in words if word],
                translation=translation,
            )
        )
    materials.sort(key=lambda item: item.ayah)
    return materials


def parse_recitation(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`{verse_key: {"url": ..., "segments": [word spans]}}` from a by_chapter payload."""
    out: dict[str, dict[str, Any]] = {}
    for entry in payload.get("audio_files") or []:
        key = str(entry.get("verse_key") or "")
        if not key:
            continue
        out[key] = {
            "url": absolute(str(entry.get("url") or "")),
            "segments": entry.get("segments") or [],
        }
    return out


def word_spans_from_segments(words: list[str], segments: Any) -> list[WordSpan]:
    """Map the API's `[word_index, start_ms, end_ms]` segments onto word spans.

    Segments are 1-based per word within the ayah and are only accepted when every word
    has a span — a partial mapping would highlight the wrong word.
    """
    if not segments or not words:
        return []
    spans: dict[int, tuple[int, int]] = {}
    for segment in segments:
        try:
            index, start_ms, end_ms = int(segment[0]), int(segment[1]), int(segment[2])
        except (TypeError, ValueError, IndexError):
            continue
        spans[index] = (start_ms, end_ms)
    if len(spans) < len(words):
        return []
    out: list[WordSpan] = []
    for position, word in enumerate(words, start=1):
        if position not in spans:
            return []
        start_ms, end_ms = spans[position]
        out.append(WordSpan(text=word, start_ms=start_ms, end_ms=max(start_ms + 1, end_ms)))
    return out


class QuranComCorpus:
    name = "quran_com"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        base_url: str | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = (base_url or os.environ.get("REEL_QURAN_API_BASE") or API_BASE).rstrip("/")
        self._client = client
        self._recitations: list[dict[str, Any]] | None = None
        self._translations: list[dict[str, Any]] | None = None

    # -- plumbing ---------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30.0, follow_redirects=True)
        return self._client

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._http().get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "corpora",
            "description": "Quran Foundation v4: text, translations, audio, word timings",
            "missing": [],
        }

    # -- name resolution --------------------------------------------------------
    def _recitation_listing(self) -> list[dict[str, Any]]:
        if self._recitations is None:
            self._recitations = list(self._get("/resources/recitations").get("recitations") or [])
        return self._recitations

    def resolve_reciter(self, reciter: str) -> tuple[str, str]:
        text = (reciter or "").strip()
        if text.isdigit():
            return text, ""
        needle = text.lower().replace("-", " ").replace("_", " ")
        for entry in self._recitation_listing():
            names = {
                str(entry.get("reciter_name") or "").lower(),
                str((entry.get("translated_name") or {}).get("name") or "").lower(),
            }
            if any(needle in name or name in needle for name in names if name):
                return str(entry.get("id")), str(entry.get("reciter_name") or "")
        known = ", ".join(
            str(entry.get("reciter_name") or "") for entry in self._recitation_listing()[:8]
        )
        raise InvalidInput(
            f"unknown reciter {reciter!r} for the quran.com corpus",
            hint=f"use a recitation id, or one of: {known}",
        )

    def resolve_translation(self, translation: str) -> tuple[str, str]:
        text = (translation or "").strip()
        if text.isdigit():
            return text, ""
        needle = TRANSLATION_HINTS.get(text.lower(), text.lower().replace("en.", "").replace(".", " "))
        if self._translations is None:
            self._translations = list(self._get("/resources/translations").get("translations") or [])
        for entry in self._translations:
            name = str(entry.get("name") or "").lower()
            if needle and needle in name:
                return str(entry.get("id")), str(entry.get("name") or "")
        known = ", ".join(str(entry.get("name") or "") for entry in self._translations[:8])
        raise InvalidInput(
            f"unknown translation {translation!r} for the quran.com corpus",
            hint=f"use a translations id, or one of: {known}",
        )

    # -- the corpus protocol ----------------------------------------------------
    def fetch(self, inputs: QuranicInputs) -> QuranSelection:
        start, end = inputs.ayah_range()
        recitation_id, reciter_name = self.resolve_reciter(inputs.reciter)
        translation_id, translation_name = self.resolve_translation(inputs.translation)

        verses_payload = self._get(
            f"/verses/by_chapter/{inputs.surah}",
            params={
                "words": "true",
                "translations": translation_id,
                "per_page": 300,
                "fields": "text_uthmani",
            },
        )
        ayahs = parse_verses(verses_payload, surah=inputs.surah, start=start, end=end)
        if not ayahs:
            raise InvalidInput(
                f"the quran.com corpus returned no ayahs for {inputs.surah}:{start}-{end}",
                hint="check the surah and ayah range",
            )

        recitation_payload = self._get(f"/recitations/{recitation_id}/by_chapter/{inputs.surah}")
        audio_by_key = parse_recitation(recitation_payload)
        used_provider_timings = False
        for ayah in ayahs:
            entry = audio_by_key.get(ayah.key) or {}
            ayah.audio_url = str(entry.get("url") or "")
            spans = word_spans_from_segments(ayah.words, entry.get("segments"))
            if spans:
                ayah.word_timings = spans
                used_provider_timings = True

        return QuranSelection(
            inputs=inputs,
            ayahs=ayahs,
            reciter_name=reciter_name or recitation_id,
            translation_name=translation_name or translation_id,
            provenance=Provenance(
                provider=self.name,
                provider_version="v4",
                source=self.base_url,
                source_id=f"{inputs.surah}:{start}-{end}",
            ),
            licence=Licence(
                name="Quran text: public domain; translation per publisher",
                url="https://quran.com",
                attribution=f"Quran Foundation API v4 · recitation {reciter_name or recitation_id} · translation {translation_name or translation_id}",
            ),
            timings_source="provider" if used_provider_timings else "proportional",
        )

    def fetch_audio(self, url: str, dest: Path) -> Path:
        if not url:
            raise InvalidInput("this ayah has no recitation audio URL")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._http().stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        return dest

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
