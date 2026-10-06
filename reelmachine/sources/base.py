"""Where the actual video comes from.

The Nadeshiko API tells you *which* episode and *when*; it does not serve the
episode.  A `SourceProvider` turns (media, episode number) into something
ffmpeg can open, and supplies the reference audio clips used for alignment.
"""

from __future__ import annotations

import difflib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .. import ffmpeg
from ..config import Settings, get_settings
from ..models import Media, Segment

VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".flv", ".wmv"}


class SourceError(RuntimeError):
    pass


class UnresolvedEpisode(SourceError):
    """The provider has no copy of this episode."""


@dataclass(slots=True)
class EpisodeAsset:
    """A playable copy of one episode."""

    media_public_id: str
    episode: int
    url: str
    duration_ms: int = 0
    label: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    local_path: Path | None = None
    info: ffmpeg.MediaInfo | None = None
    audio_track: int = 0   # chosen by language tag, not assumed to be 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        return self.local_path is not None and not ffmpeg.is_remote(self.url)

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0


def template_context(media: Media | None, episode: int, *, season: int = 1, quality: str = "") -> dict[str, Any]:
    """Values available to `REEL_LOCAL_TEMPLATES` / `REEL_HLS_TEMPLATE`."""
    external = getattr(media, "externalIds", None)
    ctx: dict[str, Any] = {
        "mediaPublicId": getattr(media, "publicId", "") or "",
        "slug": getattr(media, "slug", "") or "",
        "nameEn": getattr(media, "nameEn", "") or getattr(media, "nameRomaji", "") or "",
        "nameRomaji": getattr(media, "nameRomaji", "") or "",
        "nameJa": getattr(media, "nameJa", "") or "",
        "anilist": getattr(external, "anilist", None) or "",
        "tmdb": getattr(external, "tmdb", None) or "",
        "tvdb": getattr(external, "tvdb", None) or "",
        "imdb": getattr(external, "imdb", None) or "",
        "season": season,
        "ep": episode,
        "ep2": f"{episode:02d}",
        "ep3": f"{episode:03d}",
        "quality": quality,
        "title": getattr(media, "nameEn", "") or "",
    }
    return ctx


def _normalise(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalise(a), _normalise(b)).ratio()


class SourceProvider(ABC):
    name: str = "base"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @abstractmethod
    def resolve(self, media: Media | None, episode: int, **kwargs: Any) -> EpisodeAsset:
        """Return a playable asset for this episode or raise `UnresolvedEpisode`."""

    def missing(self) -> list[str]:
        """Unmet requirements — config names, keys, binaries — for this deployment.

        A provider that reports a missing requirement is still discoverable; a
        deployment can gate it (`REEL_PROVIDERS_DISABLED`) and `reel doctor` says why.
        """
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "sources",
            "description": (type(self).__doc__ or "").strip().splitlines()[0] if type(self).__doc__ else "",
            "missing": self.missing(),
        }

    def probe(self, asset: EpisodeAsset) -> ffmpeg.MediaInfo:
        if asset.info is None:
            asset.info = ffmpeg.probe(asset.url, headers=asset.headers or None)
            if not asset.duration_ms and asset.info.duration_s:
                asset.duration_ms = int(asset.info.duration_s * 1000)
            asset.audio_track = ffmpeg.pick_japanese_track(asset.info)
        return asset.info

    def reference_clips(
        self,
        segments: Iterable[Segment],
        *,
        client: Any,
        asset: EpisodeAsset | None = None,
    ) -> dict[str, Path]:
        """Download each segment's ground-truth audio clip from the API.

        Default behaviour for every real provider: these clips are what makes
        timestamp alignment measurable rather than guessed.
        """
        if client is None:
            return {}
        out: dict[str, Path] = {}
        for segment in segments:
            if not segment.urls.audioUrl:
                continue
            dest = self.settings.cache_dir / "clips" / f"{segment.publicId}.m4a"
            try:
                out[segment.publicId] = client.download(segment.urls.audioUrl, dest)
            except Exception:  # noqa: BLE001 - a missing clip just loses an anchor
                continue
        return out


def find_video_files(root: Path, limit: int = 20000) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if len(found) >= limit:
            break
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            found.append(path)
    return found


SOURCE_ALIASES = {"m3u8": "hls", "stream": "hls", "vid": "vidvault", "vv": "vidvault"}


def resolve_source_name(name: str | None = None, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    key = (name or settings.source or "local").strip().lower()
    return SOURCE_ALIASES.get(key, key)


def get_provider(
    name: str | None = None,
    settings: Settings | None = None,
    *,
    registry: Any = None,
) -> SourceProvider:
    """Resolve a source provider through the registry (first-party is not special)."""
    from ..core.registry import Registry

    settings = settings or get_settings()
    registry = registry or Registry()
    key = resolve_source_name(name, settings)
    known = {entry.lower() for entry in registry.names("sources")}
    if key not in known:
        raise SourceError(
            f"unknown source provider {key!r} (expected local | hls | vidvault | mock)"
        )
    provider_class = registry.load("sources", key)
    return provider_class(settings)
