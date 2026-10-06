"""alquran.cloud text and translations plus per-ayah audio, with proportional timings.

This provider carries no word timings, which is exactly what the deterministic
proportional fallback exists for; the manifest records that it was used.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from ....config import Settings
from ....core.assets import Licence, Provenance
from ....core.errors import InvalidInput
from ..models import AyahMaterial, QuranSelection, QuranicInputs

API_BASE = "https://api.alquran.cloud/v1"

RECITER_EDITIONS = {
    "alafasy": "ar.alafasy",
    "abdulbasit": "ar.abdulbasitmurattal",
    "abdulbasitmurattal": "ar.abdulbasitmurattal",
    "husary": "ar.husary",
    "minshawi": "ar.minshawi",
    "shuraym": "ar.saoodshuraym",
}

TRANSLATION_EDITIONS = {
    "en.sahih": "en.sahih",
    "en.pickthall": "en.pickthall",
    "en.yusufali": "en.yusufali",
    "ur.jalandhry": "ur.jalandhry",
    "fr.hamidullah": "fr.hamidullah",
    "es.cortes": "es.cortes",
    "id.kemenag": "id.indonesian",
}


def parse_surah(payload: dict[str, Any], *, start: int, end: int, translation: str = "") -> list[AyahMaterial]:
    data = payload.get("data") or {}
    ayahs = data.get("ayahs") or []
    materials: list[AyahMaterial] = []
    for entry in ayahs:
        number = int(entry.get("numberInSurah") or 0)
        if not (start <= number <= end):
            continue
        materials.append(
            AyahMaterial(
                surah=int(data.get("number") or 0),
                ayah=number,
                text=str(entry.get("text") or ""),
                words=str(entry.get("text") or "").split(),
                translation=translation,
                audio_url=str(entry.get("audio") or ""),
            )
        )
    materials.sort(key=lambda item: item.ayah)
    return materials


def merge_translation(ayahs: list[AyahMaterial], payload: dict[str, Any]) -> str:
    """Copy translation text onto matching ayahs; returns the edition's name if present."""
    data = payload.get("data") or {}
    by_number = {
        int(entry.get("numberInSurah") or 0): str(entry.get("text") or "")
        for entry in (data.get("ayahs") or [])
    }
    for ayah in ayahs:
        ayah.translation = by_number.get(ayah.ayah, ayah.translation)
    return str(data.get("englishName") or data.get("name") or "")


class AlQuranCloudCorpus:
    name = "alquran_cloud"
    version = "v1"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        base_url: str | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = (base_url or os.environ.get("REEL_QURAN_CLOUD_BASE") or API_BASE).rstrip("/")
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30.0, follow_redirects=True)
        return self._client

    def _get(self, path: str) -> dict[str, Any]:
        response = self._http().get(f"{self.base_url}{path}")
        response.raise_for_status()
        return response.json()

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "corpora",
            "description": "alquran.cloud text and translations with per-ayah audio",
            "missing": [],
        }

    def _reciter_edition(self, reciter: str) -> str:
        text = (reciter or "").strip()
        if text.startswith("ar."):
            return text
        edition = RECITER_EDITIONS.get(text.lower().replace("-", "").replace("_", ""))
        if edition is None:
            known = ", ".join(sorted(RECITER_EDITIONS))
            raise InvalidInput(
                f"unknown reciter {reciter!r} for the alquran.cloud corpus",
                hint=f"use an edition id like ar.alafasy, or one of: {known}",
            )
        return edition

    def resolve_reciter(self, reciter: str) -> tuple[str, str]:
        """The edition id for a reciter name — used by `probe` to fail early."""
        edition = self._reciter_edition(reciter)
        return edition, edition

    def resolve_translation(self, translation: str) -> tuple[str, str]:
        edition = self._translation_edition(translation)
        return edition, edition

    def _translation_edition(self, translation: str) -> str:
        text = (translation or "").strip()
        edition = TRANSLATION_EDITIONS.get(text.lower())
        if edition is None:
            known = ", ".join(sorted(TRANSLATION_EDITIONS))
            raise InvalidInput(
                f"unknown translation {translation!r} for the alquran.cloud corpus",
                hint=f"use an edition id like en.sahih, or one of: {known}",
            )
        return edition

    def fetch(self, inputs: QuranicInputs) -> QuranSelection:
        start, end = inputs.ayah_range()
        reciter_edition = self._reciter_edition(inputs.reciter)
        translation_edition = self._translation_edition(inputs.translation)

        arabic_payload = self._get(f"/surah/{inputs.surah}/{reciter_edition}")
        ayahs = parse_surah(arabic_payload, start=start, end=end)
        if not ayahs:
            raise InvalidInput(
                f"alquran.cloud returned no ayahs for {inputs.surah}:{start}-{end}",
                hint="check the surah and ayah range",
            )
        translation_payload = self._get(f"/surah/{inputs.surah}/{translation_edition}")
        translation_name = merge_translation(ayahs, translation_payload)

        return QuranSelection(
            inputs=inputs,
            ayahs=ayahs,
            reciter_name=reciter_edition,
            translation_name=translation_name or translation_edition,
            provenance=Provenance(
                provider=self.name,
                provider_version="v1",
                source=self.base_url,
                source_id=f"{inputs.surah}:{start}-{end}",
            ),
            licence=Licence(
                name="Quran text: public domain; translation per publisher",
                url="https://alquran.cloud",
                attribution=f"alquran.cloud · {reciter_edition} · {translation_edition}",
            ),
            timings_source="proportional",
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
