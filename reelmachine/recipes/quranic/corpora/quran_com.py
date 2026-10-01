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
#: The API returns recitation URLs as bare paths ("Alafasy/mp3/001001.mp3").
AUDIO_BASE = "https://verses.quran.com"

#: The API's own spellings differ from the ones users type; names are compared after
#: stripping punctuation and case, so an alias only needs a distinctive substring.
RECITER_ALIASES = {
    "alafasy": "afasy",
    "alafasi": "afasy",
    "afasy": "afasy",
    "mishary": "mishari",
    "abdulbasit": "abdulbaset",
    "abdulbaset": "abdulbaset",
    "shuraim": "shuraym",
    "shuraym": "shuraym",
    "sudais": "sudais",
    "husary": "husary",
    "husari": "husary",
    "minshawi": "minshawi",
    "shatri": "shatri",
    "rifai": "rifai",
    "tablawi": "tablawi",
}

#: Each alias lists the spellings different mirrors use for the same translation.
TRANSLATION_ALIASES: dict[str, tuple[str, ...]] = {
    "en.sahih": ("saheeh international", "sahih international"),
    "en.pickthall": ("pickthall",),
    "en.yusufali": ("yusuf ali",),
    "en.abdelhaleem": ("abdel haleem", "haleem"),
    "fr.hamidullah": ("hamidullah",),
    "id.kemenag": ("indonesian islamic affairs",),
}

_TAGS = re.compile(r"<[^>]+>")
_SUP = re.compile(r"<sup[^>]*>.*?</sup>", re.IGNORECASE | re.DOTALL)
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")
#: Without `word_fields=text_uthmani` the API returns font glyph codes (U+FB50…) for
#: words, which no shaper will join — they render as isolated letters.
_GLYPH_FORMS = re.compile(r"[\ufb50-\ufdff\ufe70-\ufeff]")


def normalise_name(text: str) -> str:
    """Lowercase, punctuation-free form for matching the API's transliterations."""
    return _NOT_ALNUM.sub("", (text or "").lower())


def name_matches(needle: str, candidate: str) -> bool:
    left, right = normalise_name(needle), normalise_name(candidate)
    if not left or not right:
        return False
    return left in right or right in left


def strip_html(text: str) -> str:
    """Plain text: footnote markers (`<sup>1</sup>`) go, not just their tags."""
    return _TAGS.sub("", _SUP.sub("", text or "")).replace("&amp;", "&").strip()


def word_text(word: dict[str, Any]) -> str:
    """A word's Uthmani spelling, never the presentation-form glyph codes."""
    for key in ("text_uthmani", "text"):
        value = strip_html(str(word.get(key) or ""))
        if value and not _GLYPH_FORMS.search(value):
            return value
    return ""


def absolute(url: str, base: str = AUDIO_BASE) -> str:
    """A playable URL: the API serves bare paths and protocol-relative URLs."""
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith(("http://", "https://")):
        return url
    return f"{base.rstrip('/')}/{url.lstrip('/')}"


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
            word_text(word)
            for word in verse.get("words") or []
            if str(word.get("char_type_name") or "word").lower() == "word"
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


def parse_recitation(
    payload: dict[str, Any], *, audio_base: str = AUDIO_BASE
) -> dict[str, dict[str, Any]]:
    """`{verse_key: {"url": ..., "segments": [word spans]}}` from a by_chapter payload."""
    out: dict[str, dict[str, Any]] = {}
    for entry in payload.get("audio_files") or []:
        key = str(entry.get("verse_key") or "")
        if not key:
            continue
        out[key] = {
            "url": absolute(str(entry.get("url") or ""), audio_base),
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
    version = "v5"  # bump when the parsing changes: it is part of the stage cache key

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        base_url: str | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = (base_url or os.environ.get("REEL_QURAN_API_BASE") or API_BASE).rstrip("/")
        self.audio_base = (
            os.environ.get("REEL_QURAN_AUDIO_BASE") or AUDIO_BASE
        ).rstrip("/")
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
        needle = RECITER_ALIASES.get(normalise_name(text), text)
        for entry in self._recitation_listing():
            names = [
                str(entry.get("reciter_name") or ""),
                str((entry.get("translated_name") or {}).get("name") or ""),
            ]
            if any(name_matches(needle, name) or name_matches(text, name) for name in names):
                return str(entry.get("id")), str(entry.get("reciter_name") or "")
        known = ", ".join(sorted(RECITER_ALIASES))
        raise InvalidInput(
            f"unknown reciter {reciter!r} for the quran.com corpus",
            hint=f"use a recitation id, or one of: {known}",
        )

    def resolve_translation(self, translation: str) -> tuple[str, str]:
        text = (translation or "").strip()
        if text.isdigit():
            return text, ""
        candidates = TRANSLATION_ALIASES.get(text.lower(), (text.replace(".", " "),))
        if self._translations is None:
            self._translations = list(self._get("/resources/translations").get("translations") or [])
        for entry in self._translations:
            name = str(entry.get("name") or "")
            if any(name_matches(candidate, name) for candidate in candidates):
                return str(entry.get("id")), name
        known = ", ".join(sorted(TRANSLATION_ALIASES))
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
                "word_fields": "text_uthmani",
            },
        )
        ayahs = parse_verses(verses_payload, surah=inputs.surah, start=start, end=end)
        if not ayahs:
            raise InvalidInput(
                f"the quran.com corpus returned no ayahs for {inputs.surah}:{start}-{end}",
                hint="check the surah and ayah range",
            )

        recitation_payload = self._get(f"/recitations/{recitation_id}/by_chapter/{inputs.surah}")
        audio_by_key = parse_recitation(recitation_payload, audio_base=self.audio_base)
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
