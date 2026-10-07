"""OpenSubtitles.com — the official API and the largest catalogue.

Search is by `tmdb_id` for a film; a TV episode searches by the *series* id
(`parent_tmdb_id`) plus season and episode, which is what the API asks for. The free
tier allows 20 downloads a day for a registered account (5 anonymously), so this is
the second database in the chain: it is there when SubDL has nothing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from ....core.errors import ProviderUnavailable
from . import SubtitleFetch, SubtitleRequest, describe_row, language_rank
from .files import to_srt

BASE = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "reel-machine v0.2"


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


class OpenSubtitlesProvider:
    name = "opensubtitles"

    def __init__(self, settings: Any = None, *, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.api_key = os.environ.get("REEL_OPENSUBTITLES_API_KEY", "").strip()
        self.username = os.environ.get("REEL_OPENSUBTITLES_USERNAME", "").strip()
        self.password = os.environ.get("REEL_OPENSUBTITLES_PASSWORD", "").strip()
        self._client = client
        self._token = ""

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0, follow_redirects=True)
        return self._client

    def missing(self) -> list[str]:
        if self.api_key:
            return []
        return [
            "REEL_OPENSUBTITLES_API_KEY is not set "
            "(free account: https://www.opensubtitles.com/en/consumers)"
        ]

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "OpenSubtitles.com: the official API (free: 20 downloads/day registered)",
            self.missing(),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ auth
    def _headers(self, *, with_auth: bool = True) -> dict[str, str]:
        headers = {"Api-Key": self.api_key, "User-Agent": USER_AGENT, "Accept": "application/json"}
        if with_auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _login(self) -> None:
        if self._token or not (self.username and self.password):
            return
        response = self._http().post(
            f"{BASE}/login",
            headers=self._headers(with_auth=False),
            json={"username": self.username, "password": self.password},
        )
        if response.status_code in (401, 403):
            raise ProviderUnavailable(
                "opensubtitles rejected the account credentials",
                hint="check REEL_OPENSUBTITLES_USERNAME / REEL_OPENSUBTITLES_PASSWORD",
                details={"provider": self.name, "status": response.status_code},
            )
        if response.status_code != 200:
            raise ProviderUnavailable(
                "opensubtitles login failed",
                hint="check the credentials, or unset them to use anonymous downloads",
                details={"provider": self.name, "status": response.status_code},
            )
        self._token = str(response.json().get("token") or "")

    # ------------------------------------------------------------------ fetch
    def fetch(self, request: SubtitleRequest, *, dest_dir: Path) -> SubtitleFetch | None:
        if not self.api_key:
            raise ProviderUnavailable(
                "opensubtitles needs an API key",
                hint="set REEL_OPENSUBTITLES_API_KEY, or let embedded/subdl be used",
                details={"provider": self.name},
            )
        try:
            self._login()
        except ProviderUnavailable:
            raise
        except httpx.HTTPError:
            pass  # downloads can still work anonymously with the Api-Key

        params: dict[str, Any] = {"languages": ",".join(request.languages) or "en"}
        if request.media_type == "tv":
            params["parent_tmdb_id"] = request.tmdb_id
            params["season_number"] = request.season
            params["episode_number"] = request.episode
        else:
            params["tmdb_id"] = request.tmdb_id

        response = self._http().get(f"{BASE}/subtitles", params=params, headers=self._headers())
        if response.status_code in (401, 403):
            raise ProviderUnavailable(
                "opensubtitles rejected the API key",
                hint="check REEL_OPENSUBTITLES_API_KEY",
                details={"provider": self.name, "status": response.status_code},
            )
        if response.status_code == 429:
            raise ProviderUnavailable(
                "opensubtitles is rate-limiting this key",
                hint="wait a moment and retry",
                details={"provider": self.name},
            )
        if response.status_code != 200:
            raise ProviderUnavailable(
                "opensubtitles search failed",
                hint="retry; if it persists, check the key and the API status",
                details={"provider": self.name, "status": response.status_code},
            )
        entries = (response.json() or {}).get("data") or []
        chosen = self._choose(entries, request.languages)
        if chosen is None:
            return None

        file_id = chosen["file_id"]
        download = self._http().post(
            f"{BASE}/download",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"file_id": file_id, "sub_format": "srt"},
        )
        if download.status_code in (401, 403):
            raise ProviderUnavailable(
                "opensubtitles refused the download",
                hint=(
                    "set REEL_OPENSUBTITLES_USERNAME / REEL_OPENSUBTITLES_PASSWORD "
                    "for a registered account"
                ),
                details={"provider": self.name, "status": download.status_code},
            )
        if download.status_code in (406, 407):
            raise ProviderUnavailable(
                "opensubtitles' daily download quota is used up",
                hint="wait for the quota to reset (shown on opensubtitles.com), or use subdl",
                details={"provider": self.name, "remaining": download.headers.get("x-ratelimit-remaining")},
            )
        if download.status_code != 200:
            raise ProviderUnavailable(
                "opensubtitles could not prepare the download",
                hint="retry; if it persists, the file may have been removed",
                details={"provider": self.name, "status": download.status_code},
            )
        payload = download.json() or {}
        link = str(payload.get("link") or "")
        if not link:
            return None

        fetched = self._http().get(link, headers={"User-Agent": USER_AGENT})
        if fetched.status_code != 200 or not fetched.content:
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(httpx.URL(link).path).suffix.lower()
        if suffix != ".srt":
            suffix = ".srt"
        dest = dest_dir / f"opensubtitles-download{suffix}"
        dest.write_bytes(fetched.content)
        srt = to_srt(dest, dest_dir)
        if srt is None:
            return None
        attributes = chosen["attributes"]
        return SubtitleFetch(
            path=str(srt),
            language=str(attributes.get("language") or request.primary_language()),
            source=self.name,
            meta={
                "fileId": file_id,
                "release": _first(attributes, "release", "feature_details", default=""),
                "downloads": attributes.get("download_count"),
                "hearingImpaired": bool(attributes.get("hearing_impaired") or False),
                "remaining": payload.get("remaining"),
                "authenticated": bool(self._token),
            },
        )

    @staticmethod
    def _choose(entries: list[Any], languages: list[str]) -> dict[str, Any] | None:
        ranked: list[tuple[tuple[int, int, float], dict[str, Any]]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            attributes = entry.get("attributes") or {}
            files = attributes.get("files") or []
            if not files:
                continue
            file_id = _first(files[0], "file_id", "fileId")
            if file_id is None:
                continue
            language = str(attributes.get("language") or "")
            hi = bool(attributes.get("hearing_impaired") or False)
            downloads = attributes.get("download_count") or 0
            try:
                downloads_value = float(downloads)
            except (TypeError, ValueError):
                downloads_value = 0.0
            ranked.append(
                (
                    (language_rank(language, languages), 1 if hi else 0, -downloads_value),
                    {"file_id": file_id, "attributes": attributes},
                )
            )
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0])
        return ranked[0][1]
