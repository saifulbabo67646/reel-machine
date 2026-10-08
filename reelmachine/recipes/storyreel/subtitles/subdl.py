"""SubDL — a TMDB-native subtitle database with a generous free tier.

Search is by `tmdb_id` (a TV episode searches by the *series* id plus season/episode).
The response has been reshaped across API generations, so the parsers here read every
generation found live: entries are matched by the keys they actually carry, not by a
fixed schema. Free keys allow 2 000 searches + 50 downloads a day.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from ....core.errors import ProviderUnavailable
from . import SubtitleFetch, SubtitleRequest, describe_row, language_rank
from .files import to_srt

API_BASES = ("https://api.subdl.com/api/v2", "https://api.subdl.com/api/v1")
DOWNLOAD_HOST = "https://dl.subdl.com"
USER_AGENT = "reel-machine/storyreel (+https://github.com/)" 


def _first(entry: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in entry and entry[key] not in (None, ""):
            return entry[key]
    return None


def _entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("subtitles", "results", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


class SubdlProvider:
    name = "subdl"

    def __init__(self, settings: Any = None, *, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.api_key = os.environ.get("REEL_SUBDL_API_KEY", "").strip()
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0, follow_redirects=True)
        return self._client

    def missing(self) -> list[str]:
        if self.api_key:
            return []
        return ["REEL_SUBDL_API_KEY is not set (free key: https://subdl.com/panel/api)"]

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "SubDL: search by TMDB id, direct SRT download (free: 2 000 searches + 50 downloads/day)",
            self.missing(),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ fetch
    def fetch(self, request: SubtitleRequest, *, dest_dir: Path) -> SubtitleFetch | None:
        if not self.api_key:
            raise ProviderUnavailable(
                "subdl needs an API key",
                hint="set REEL_SUBDL_API_KEY, or let the embedded subtitles be used",
                details={"provider": self.name},
            )
        payload, base = self._search(request)
        entries = _entries(payload)
        if not entries:
            return None
        chosen = self._choose(entries, request.languages)
        if chosen is None:
            return None
        downloaded = self._download(chosen, base, dest_dir)
        if downloaded is None:
            return None
        srt = to_srt(downloaded, dest_dir)
        if srt is None:
            return None
        entry = chosen["entry"]
        return SubtitleFetch(
            path=str(srt),
            language=str(_first(entry, "language", "lang", "language_code", "languageCode") or request.primary_language()),
            source=self.name,
            meta={
                "reference": chosen.get("reference"),
                "release": _first(entry, "release", "release_name", "releaseName", "title"),
                "downloads": _first(entry, "downloads", "downloadCount", "download_count"),
                "hearingImpaired": chosen["hi"],
                "attempts": chosen.get("attempts", []),
            },
        )

    def _search(self, request: SubtitleRequest) -> tuple[dict[str, Any], str]:
        params: dict[str, Any] = {"tmdb_id": request.tmdb_id, "type": request.media_type}
        if request.media_type == "tv":
            params["season_number"] = request.season
            params["episode_number"] = request.episode
        if request.languages:
            params["languages"] = ",".join(language.upper() for language in request.languages)
        params["unpack"] = 1

        attempts: list[str] = []
        last_error = ""
        for base in API_BASES:
            url = f"{base}/subtitles/search" if base.endswith("/v2") else f"{base}/subtitles"
            headers = {"User-Agent": USER_AGENT}
            query = dict(params)
            if base.endswith("/v2"):
                headers["Authorization"] = f"Bearer {self.api_key}"
            else:
                query["api_key"] = self.api_key
            try:
                response = self._http().get(url, params=query, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                attempts.append(f"{base}: {last_error}")
                continue
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    attempts.append(f"{base}: answered non-JSON")
                    continue
                if isinstance(payload, dict) and payload.get("status") is False:
                    attempts.append(f"{base}: {payload.get('error') or 'rejected'}")
                    continue
                return payload, base
            if response.status_code in (401, 403):
                raise ProviderUnavailable(
                    "subdl rejected the API key",
                    hint="check REEL_SUBDL_API_KEY (subdl.com/panel/api)",
                    details={"provider": self.name, "status": response.status_code},
                )
            if response.status_code == 429:
                raise ProviderUnavailable(
                    "subdl's daily quota is used up",
                    hint="wait for the quota to reset, or use another subtitle source",
                    details={"provider": self.name, "remaining": response.headers.get("X-RateLimit-Remaining")},
                )
            attempts.append(f"{base}: HTTP {response.status_code}")
        raise ProviderUnavailable(
            "subdl could not be reached",
            hint="check the network and the key; embedded subtitles may already be enough",
            details={"provider": self.name, "attempts": attempts, "lastError": last_error},
        )

    @staticmethod
    def _choose(entries: list[dict[str, Any]], languages: list[str]) -> dict[str, Any] | None:
        ranked: list[tuple[tuple[int, int, float], dict[str, Any]]] = []
        for entry in entries:
            language = _first(entry, "language", "lang", "language_code", "languageCode") or ""
            hi = bool(_first(entry, "hi", "hearing_impaired", "hearingImpaired") or False)
            downloads = _first(entry, "downloads", "downloadCount", "download_count") or 0
            try:
                downloads_value = float(downloads)
            except (TypeError, ValueError):
                downloads_value = 0.0
            key = (language_rank(str(language), languages), 1 if hi else 0, -downloads_value)
            ranked.append((key, entry))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0])
        best = ranked[0]
        return {
            "entry": best[1],
            "hi": bool(_first(best[1], "hi", "hearing_impaired", "hearingImpaired") or False),
            "reference": _first(best[1], "nId", "nid", "id", "subtitle_id", "subtitleId", "url"),
        }

    def _download(self, chosen: dict[str, Any], base: str, dest_dir: Path) -> Path | None:
        entry = chosen["entry"]
        reference = chosen.get("reference")
        dest_dir.mkdir(parents=True, exist_ok=True)

        url = _first(entry, "downloadUrl", "download_url", "url", "link")
        candidates: list[str] = []
        if reference is not None and base.endswith("/v2"):
            candidates.append(f"{base}/subtitles/{reference}/download?format=file")
        if url:
            text = str(url)
            candidates.append(text if text.startswith("http") else f"{DOWNLOAD_HOST}{text if text.startswith('/') else '/' + text}")
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                response = self._http().get(
                    candidate,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Authorization": f"Bearer {self.api_key}",
                    },
                )
            except httpx.HTTPError:
                continue
            if response.status_code != 200 or not response.content:
                continue
            suffix = Path(httpx.URL(candidate).path).suffix.lower()
            if suffix not in (".srt", ".ass", ".ssa", ".vtt", ".zip", ".sub"):
                suffix = ".zip" if response.content[:2] == b"PK" else ".srt"
            dest = dest_dir / f"subdl-download{suffix}"
            dest.write_bytes(response.content)
            return dest
        return None
