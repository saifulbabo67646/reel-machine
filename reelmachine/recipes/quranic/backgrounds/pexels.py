"""Stock background clips from Pexels.

A deployment chooses whether it has a stock source at all: without `PEXELS_API_KEY` the
provider reports exactly that and the recipe keeps its procedural gradient. Every clip
carries the Pexels licence and the photographer's attribution into the manifest.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from ....config import Settings, get_settings
from ....core.assets import Licence, Provenance
from ....core.errors import InvalidInput, ProviderUnavailable
from . import BackgroundClip, BackgroundRequest

API_BASE = "https://api.pexels.com"
PER_PAGE = 15
LICENCE_URL = "https://www.pexels.com/license/"


def pick_video_file(
    payload: dict[str, Any],
    *,
    orientation: str = "portrait",
    min_height: int = 720,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The best `(video, file)` for the reel: right shape, enough resolution, mp4.

    Pexels returns every rendition it has; choosing one is policy, so it lives here and
    is unit-tested rather than being buried in the request.
    """
    best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for video in payload.get("videos") or []:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        if orientation == "portrait" and height <= width:
            continue
        if orientation == "landscape" and width <= height:
            continue
        for entry in video.get("video_files") or []:
            if (entry.get("file_type") or "").lower() not in {"video/mp4", "video/webm"}:
                continue
            file_height = int(entry.get("height") or 0)
            if file_height and file_height < min_height:
                continue
            link = str(entry.get("link") or "")
            if not link:
                continue
            # prefer sharpness close to 1080p without going absurdly large
            target = 1080 if file_height == 0 else min(file_height, 2160)
            score = abs(target - 1080) + abs(height - (1920 if orientation == "portrait" else 1080)) / 20
            if best is None or score < best[0]:
                best = (score, video, entry)
    if best is None:
        return None
    _, video, entry = best
    return video, entry


class PexelsBackgroundProvider:
    name = "pexels"
    version = "v1"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = (base_url or API_BASE).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("PEXELS_API_KEY", "")
        self.cache_dir = (
            Path(cache_dir) if cache_dir is not None else self.settings.cache_dir / "pexels"
        )
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0, follow_redirects=True)
        return self._client

    def missing(self) -> list[str]:
        return [] if self.api_key else ["PEXELS_API_KEY is not set"]

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "backgrounds",
            "description": "Stock background clips from Pexels (needs PEXELS_API_KEY)",
            "missing": self.missing(),
        }

    def search(self, query: str, *, orientation: str = "portrait") -> dict[str, Any]:
        response = self._http().get(
            f"{self.base_url}/videos/search",
            headers={"Authorization": self.api_key},
            params={"query": query, "orientation": orientation, "per_page": PER_PAGE, "size": "medium"},
        )
        response.raise_for_status()
        return response.json()

    def fetch(self, request: BackgroundRequest, *, dest_dir: Path) -> BackgroundClip:
        if not self.api_key:
            raise ProviderUnavailable(
                "stock backgrounds are not configured on this deployment",
                hint="set PEXELS_API_KEY, or use background kind=gradient (the default)",
                details={"provider": self.name},
            )
        if not request.query.strip():
            raise InvalidInput("a Pexels background needs a search query")
        payload = self.search(request.query, orientation=request.orientation)
        chosen = pick_video_file(payload, orientation=request.orientation, min_height=request.min_height)
        if chosen is None:
            raise InvalidInput(
                f"no Pexels clip matched {request.query!r}",
                hint="try a broader query, or use background kind=gradient",
                details={"orientation": request.orientation},
            )
        video, file = chosen
        link = str(file["link"])
        photographer = str((video.get("user") or {}).get("name") or "")
        page_url = str(video.get("url") or "")
        suffix = Path(link.split("?", 1)[0]).suffix or ".mp4"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached = self.cache_dir / f"pexels-{video.get('id')}{suffix}"
        if not cached.is_file():
            with self._http().stream("GET", link) as response:
                response.raise_for_status()
                with cached.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        return BackgroundClip(
            path=cached,
            provenance=Provenance(
                provider=self.name,
                provider_version=self.version,
                source=page_url or self.base_url,
                source_id=str(video.get("id") or ""),
                upstream="https://www.pexels.com",
                notes=f"Video by {photographer} on Pexels" if photographer else "Pexels",
            ),
            licence=Licence(
                name="Pexels licence",
                url=LICENCE_URL,
                attribution=f"Video by {photographer} on Pexels" if photographer else "Pexels",
            ),
            width=int(file.get("width") or video.get("width") or 0),
            height=int(file.get("height") or video.get("height") or 0),
            duration_ms=int(round(float(video.get("duration") or 0) * 1000)),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
