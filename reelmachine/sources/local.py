"""Local library provider: resolve an episode to a file on disk.

Resolution order:
1. Try each `REEL_LOCAL_TEMPLATES` pattern under `REEL_LOCAL_ROOT`.
2. Fall back to scanning for a directory whose name is a close match to one of
   the media's names, then looking for the episode number inside it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..models import Media
from .base import EpisodeAsset, SourceProvider, UnresolvedEpisode, find_video_files, similar, template_context


class LocalLibraryProvider(SourceProvider):
    name = "local"

    def __init__(self, settings: Settings | None = None):
        super().__init__(settings)
        self._root = (self.settings.local_root or Path(".")).expanduser()
        self._index: list[Path] | None = None

    # ------------------------------------------------------------------ helpers
    @property
    def root(self) -> Path:
        return self._root

    def _templates(self) -> list[str]:
        return [t for t in (self.settings.local_templates or []) if t.strip()]

    def _candidates_from_templates(self, media: Media | None, episode: int, season: int) -> list[Path]:
        ctx = template_context(media, episode, season=season)
        out: list[Path] = []
        for template in self._templates():
            try:
                relative = template.format(**ctx)
            except (KeyError, ValueError, IndexError):
                continue
            out.append(self._root / relative)
        return out

    def _episode_patterns(self, episode: int) -> list[re.Pattern[str]]:
        ep2 = f"{episode:02d}"
        ep3 = f"{episode:03d}"
        return [
            re.compile(rf"(?:^|[\s\-_\[\.])0*{episode}(?:v\d)?(?:[\s\-_\]\.]|$)", re.IGNORECASE),
            re.compile(rf"\b(?:e|ep|episode|s\d+e)0*{episode}\b", re.IGNORECASE),
            re.compile(rf"(?:^|\D)0*{episode}\D"),
        ]

    def _scan(self) -> list[Path]:
        if self._index is None:
            if not self._root.is_dir():
                self._index = []
            else:
                self._index = find_video_files(self._root)
        return self._index

    def _match_in_directory(self, directory: Path, episode: int) -> Path | None:
        if not directory.is_dir():
            return None
        files = sorted(
            p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts"}
        )
        if len(files) == 1 and episode in (0, 1):
            return files[0]  # movie or single-file special
        patterns = self._episode_patterns(episode)
        for pattern in patterns:
            for path in files:
                if pattern.search(path.stem):
                    return path
        return None

    def _fuzzy_directory(self, media: Media | None, episode: int) -> Path | None:
        if media is None or not self._root.is_dir():
            return None
        directories = [d for d in self._root.iterdir() if d.is_dir()]
        best: tuple[float, Path] | None = None
        for directory in directories:
            score = max((similar(directory.name, name) for name in media.names), default=0.0)
            if score >= 0.72 and (best is None or score > best[0]):
                best = (score, directory)
        if best is None:
            return None

        target = best[1]
        # Season subdirectory, if the show uses one.
        subdirs = [d for d in target.iterdir() if d.is_dir()]
        season_dirs = sorted(
            (d for d in subdirs if re.search(r"(?:season|s)\s*0*1\b", d.name, re.IGNORECASE)),
            key=lambda d: d.name,
        )
        for directory in [*season_dirs, target, *sorted(subdirs, key=lambda d: d.name)]:
            hit = self._match_in_directory(directory, episode)
            if hit is not None:
                return hit

        # Last resort: search the whole show tree for the episode number.
        if episode > 0:
            patterns = self._episode_patterns(episode)
            for path in find_video_files(target):
                if any(p.search(path.stem) for p in patterns):
                    return path
        return None

    # ------------------------------------------------------------------- public
    def resolve(self, media: Media | None, episode: int, *, season: int = 1, **_: Any) -> EpisodeAsset:
        if not self._root.is_dir():
            raise UnresolvedEpisode(f"REEL_LOCAL_ROOT does not exist: {self._root}")

        for candidate in self._candidates_from_templates(media, episode, season):
            if candidate.is_file():
                return self._asset(candidate, media, episode)

        if media is not None:
            slug_dir = self._root / media.slug
            hit = self._match_in_directory(slug_dir, episode)
            if hit is not None:
                return self._asset(hit, media, episode)

        fuzzy = self._fuzzy_directory(media, episode)
        if fuzzy is not None:
            return self._asset(fuzzy, media, episode)

        label = media.nameEn if media else "?"
        raise UnresolvedEpisode(f"no local file for {label!r} episode {episode} under {self._root}")

    def _asset(self, path: Path, media: Media | None, episode: int) -> EpisodeAsset:
        media_public_id = media.publicId if media else f"local:{path.parent.name}"
        return EpisodeAsset(
            media_public_id=media_public_id,
            episode=episode,
            url=str(path),
            label=str(path),
            local_path=path,
            meta={"path": str(path)},
        )
