"""HLS / m3u8 provider — point this at your own streaming server.

Streaming hosts rarely serve a stable, guessable URL.  The usual shape is:

    https://cdn.example/anime/<animeHash>/<episodeHash>/master.m3u8?token=<signed>

with an opaque hash per title and per episode, and a **time-limited signed
token**.  A plain URL template cannot express that, so this provider composes
the URL from three pieces:

1. ``REEL_HLS_TEMPLATE``   — the path shape, with placeholders.
2. ``REEL_HLS_MAP``        — JSON mapping a Nadeshiko title to the site's
   opaque hashes (those cannot be derived; they have to be recorded once).
3. a **resolver**          — something that mints a fresh signed URL, because
   the token expires.  Either ``REEL_HLS_RESOLVER_CMD`` (a command that prints
   the URL on stdout) or ``REEL_HLS_TOKEN_CMD`` (a command that prints just the
   token, which is substituted into the template).

Placeholders available in the template::

    {slug} {nameEn} {nameRomaji} {nameJa} {anilist} {tmdb} {tvdb} {imdb}
    {mediaPublicId} {season} {ep} {ep2} {ep3} {quality}
    {animeHash} {episodeHash} {token}

If the resolved URL is a *master* playlist, the best rendition at or below
``REEL_HLS_QUALITY`` is selected; if it is already a media playlist it is used
as-is.  ``Referer`` / ``Origin`` / ``Cookie`` / ``User-Agent`` are sent on the
playlist fetch **and** handed to ffmpeg, which needs them again for every
segment request.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from ..config import Settings, get_settings
from ..models import Media
from .base import EpisodeAsset, SourceError, SourceProvider, UnresolvedEpisode, template_context


@dataclass(slots=True)
class Variant:
    uri: str
    bandwidth: int = 0
    width: int = 0
    height: int = 0

    def __str__(self) -> str:
        res = f"{self.height}p" if self.height else f"{self.bandwidth // 1000}kbps"
        return f"{res} ({self.bandwidth} bps)"


def _parse_attributes(line: str) -> dict[str, str]:
    _, _, payload = line.partition(":")
    attrs: dict[str, str] = {}
    current = ""
    in_quotes = False
    for char in payload:
        if char == '"':
            in_quotes = not in_quotes
            current += char
            continue
        if char == "," and not in_quotes:
            key, _, value = current.partition("=")
            attrs[key.strip()] = value.strip().strip('"')
            current = ""
            continue
        current += char
    if current:
        key, _, value = current.partition("=")
        attrs[key.strip()] = value.strip().strip('"')
    return attrs


def parse_master(text: str, base_url: str) -> tuple[list[Variant], list[str]]:
    """Return (variants, media-playlist-uris) from an m3u8 document."""
    variants: list[Variant] = []
    media_playlists: list[str] = []
    lines = [line.strip() for line in text.splitlines()]
    pending: dict[str, str] | None = None
    for line in lines:
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = _parse_attributes(line)
            continue
        if line.startswith("#"):
            continue
        if pending is not None:
            resolution = pending.get("RESOLUTION", "")
            width = height = 0
            if "x" in resolution:
                w, _, h = resolution.partition("x")
                width = int(w) if w.isdigit() else 0
                height = int(h) if h.isdigit() else 0
            variants.append(
                Variant(
                    uri=urljoin(base_url, line),
                    bandwidth=int(pending.get("BANDWIDTH") or pending.get("AVERAGE-BANDWIDTH") or 0),
                    width=width,
                    height=height,
                )
            )
            pending = None
        else:
            media_playlists.append(urljoin(base_url, line))
    return variants, media_playlists


def pick_variant(variants: list[Variant], quality: str) -> Variant | None:
    """Choose the rendition closest to (but not above) the requested height."""
    if not variants:
        return None
    want = 0
    digits = "".join(ch for ch in str(quality) if ch.isdigit())
    if digits:
        want = int(digits)
    sized = [v for v in variants if v.height]
    if want and sized:
        at_or_below = [v for v in sized if v.height <= want]
        pool = at_or_below or sized
        return max(pool, key=lambda v: (v.height, v.bandwidth))
    return max(variants, key=lambda v: v.bandwidth)


class HlsProvider(SourceProvider):
    name = "hls"

    def __init__(self, settings: Settings | None = None):
        super().__init__(settings)
        self._cache: dict[str, tuple[float, list[Variant]]] = {}
        self._text_cache: dict[str, tuple[float, str]] = {}
        self._map: dict[str, Any] | None = None

    def missing(self) -> list[str]:
        problems: list[str] = []
        if not self.settings.hls_template:
            problems.append("REEL_HLS_TEMPLATE is not set")
        elif "{token}" in self.settings.hls_template and not (
            self.settings.hls_resolver_cmd or self.settings.hls_token_cmd
        ):
            problems.append(
                "REEL_HLS_RESOLVER_CMD or REEL_HLS_TOKEN_CMD is required to mint signed tokens"
            )
        return problems

    # ------------------------------------------------------------------- map
    @property
    def site_map(self) -> dict[str, Any]:
        """`REEL_HLS_MAP`: Nadeshiko title -> the site's opaque hash ids.

        Keys may be a `mediaPublicId`, an AniList id, or a slug; values are
        either a bare anime hash or an object::

            {
              "V1StGXR8_Z5d": {
                "animeHash": "abb207957b0abc1d85a7e32ab1c4359c",
                "episodes": {"1": "dfa8b25e59b272cefaa749f2f61c73e4"}
              },
              "21459": { "animeHash": "..." }
            }
        """
        if self._map is None:
            path = self.settings.hls_map
            if path and Path(path).is_file():
                try:
                    self._map = json.loads(Path(path).read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise SourceError(f"REEL_HLS_MAP is not valid JSON ({path}): {exc}") from exc
            else:
                self._map = {}
        return self._map

    def _lookup(self, media: Media | None, episode: int) -> tuple[str, str]:
        """Return (animeHash, episodeHash) for this title/episode, or blanks."""
        if not self.site_map:
            return "", ""
        keys: list[str] = []
        if media is not None:
            keys += [
                media.publicId,
                str(getattr(media.externalIds, "anilist", "") or ""),
                str(getattr(media.externalIds, "tmdb", "") or ""),
                media.slug,
                media.nameEn,
                media.nameRomaji,
            ]
        for key in [k for k in keys if k]:
            entry = self.site_map.get(key)
            if entry is None:
                continue
            if isinstance(entry, str):
                return entry, ""
            anime_hash = str(entry.get("animeHash") or entry.get("anime") or "")
            episodes = entry.get("episodes") or {}
            episode_hash = str(episodes.get(str(episode)) or episodes.get(episode) or "")
            # A per-episode key can also be a plain path segment.
            if not episode_hash:
                episode_hash = str(entry.get("episodeHashes", {}).get(str(episode)) or "")
            return anime_hash, episode_hash
        return "", ""

    # ---------------------------------------------------------------- commands
    def _require_hashes(
        self,
        template: str,
        media: Media | None,
        episode: int,
        anime_hash: str,
        episode_hash: str,
    ) -> None:
        """Fail with an actionable message instead of building a broken URL.

        The site's title/episode ids are opaque hashes; if the template needs one
        and the map has no entry, the resulting URL would 403 or 404 several
        steps later, which is far harder to diagnose than an error here.
        """
        label = f"{media.nameEn if media else '?'} ep{episode}"
        needs_anime = "{animeHash}" in template
        needs_episode = "{episodeHash}" in template
        if not (needs_anime or needs_episode):
            return

        if not self.site_map:
            raise UnresolvedEpisode(
                f"{label}: REEL_HLS_TEMPLATE uses {{animeHash}}/{{episodeHash}} but REEL_HLS_MAP "
                f"is not set ({self.settings.hls_map}). Those ids are opaque hashes and have to be "
                "recorded once per title; see README."
            )
        if needs_anime and not anime_hash:
            raise UnresolvedEpisode(
                f"{label}: no entry for this title in REEL_HLS_MAP ({self.settings.hls_map}). "
                f"Tried keys: mediaPublicId, anilist, tmdb, slug, names."
            )
        if needs_episode and not episode_hash:
            raise UnresolvedEpisode(
                f"{label}: no hash recorded for episode {episode} under this title in "
                f"REEL_HLS_MAP ({self.settings.hls_map})."
            )

    def _run_cmd(self, command: str, context: dict[str, Any], *, label: str) -> str:
        """Run a resolver/token command with the context exported as env vars."""
        env = dict(os.environ)
        env.update({f"REEL_{k.upper()}": str(v) for k, v in context.items()})
        env["REEL_MEDIA_PUBLIC_ID"] = str(context.get("mediaPublicId", ""))
        env["REEL_EPISODE"] = str(context.get("ep", ""))
        proc = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            raise SourceError(
                f"{label} failed ({proc.returncode}): {command}\n"
                f"{(proc.stderr or '').strip()[:400]}"
            )
        # Take the *last* non-empty line, so a script that logs progress before
        # printing the value still works.
        lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
        if not lines:
            raise SourceError(f"{label} produced no output: {command}")
        return lines[-1]

    def mint_token(self, context: dict[str, Any]) -> str:
        if not self.settings.hls_token_cmd:
            return ""
        return self._run_cmd(self.settings.hls_token_cmd, context, label="REEL_HLS_TOKEN_CMD")

    def resolve_url_command(self, context: dict[str, Any]) -> str:
        if not self.settings.hls_resolver_cmd:
            return ""
        return self._run_cmd(self.settings.hls_resolver_cmd, context, label="REEL_HLS_RESOLVER_CMD")

    # ------------------------------------------------------------------ helpers
    def url_for(
        self,
        media: Media | None,
        episode: int,
        *,
        season: int = 1,
        quality: str | None = None,
        token: str = "",
        anime_hash: str = "",
        episode_hash: str = "",
    ) -> str:
        template = (self.settings.hls_template or "").strip()
        if not template:
            raise UnresolvedEpisode(
                "REEL_HLS_TEMPLATE is not set. Paste your streaming URL pattern, e.g. "
                "https://host/anime/{animeHash}/{episodeHash}/master.m3u8?token={token}"
            )
        ctx = template_context(media, episode, season=season, quality=quality or self.settings.hls_quality)
        ctx["animeHash"] = anime_hash
        ctx["episodeHash"] = episode_hash
        ctx["token"] = token
        try:
            return template.format(**ctx)
        except KeyError as exc:
            raise UnresolvedEpisode(
                f"unknown placeholder {exc} in REEL_HLS_TEMPLATE; available: "
                "slug nameEn nameRomaji nameJa anilist tmdb tvdb imdb mediaPublicId "
                "season ep ep2 ep3 quality animeHash episodeHash token"
            ) from exc

    def fetch_playlist(self, url: str, *, ttl_s: float = 20.0) -> str:
        cached = self._text_cache.get(url)
        if cached and time.time() - cached[0] < ttl_s:
            return cached[1]
        response = httpx.get(url, headers=self.settings.source_headers(), timeout=20.0, follow_redirects=True)
        if response.status_code in (401, 403):
            raise UnresolvedEpisode(
                f"{response.status_code} fetching playlist — the signed token has probably expired, "
                "or Referer/Origin/Cookie are missing. Set REEL_HLS_RESOLVER_CMD to mint a fresh URL.\n"
                f"  {url.split('?')[0]}"
            )
        response.raise_for_status()
        self._text_cache[url] = (time.time(), response.text)
        return response.text

    def variants(self, url: str) -> list[Variant]:
        cached = self._cache.get(url)
        if cached and time.time() - cached[0] < 20.0:
            return cached[1]
        text = self.fetch_playlist(url)
        variants, media_playlists = parse_master(text, url)
        if not variants and media_playlists and "#EXTINF" not in text:
            variants = [Variant(uri=media_playlists[0])]
        self._cache[url] = (time.time(), variants)
        return variants

    # ------------------------------------------------------------------- public
    def resolve(
        self,
        media: Media | None,
        episode: int,
        *,
        season: int = 1,
        url: str | None = None,
        quality: str | None = None,
        inspect: bool = True,
        **_: Any,
    ) -> EpisodeAsset:
        headers = self.settings.source_headers()
        label = f"{media.nameEn if media else '?'} ep{episode}"
        meta: dict[str, Any] = {}

        if url:
            target = url
        elif self.settings.hls_resolver_cmd:
            # The resolver owns the whole URL (it knows how to mint the token).
            ctx = template_context(media, episode, season=season, quality=quality or self.settings.hls_quality)
            target = self.resolve_url_command(ctx)
            meta["resolver"] = self.settings.hls_resolver_cmd
        else:
            template = self.settings.hls_template or ""
            anime_hash, episode_hash = self._lookup(media, episode)
            self._require_hashes(template, media, episode, anime_hash, episode_hash)
            ctx = template_context(media, episode, season=season, quality=quality or self.settings.hls_quality)
            ctx |= {"animeHash": anime_hash, "episodeHash": episode_hash}
            token = self.mint_token(ctx)
            target = self.url_for(
                media,
                episode,
                season=season,
                quality=quality,
                token=token,
                anime_hash=anime_hash,
                episode_hash=episode_hash,
            )

        meta["url"] = target
        chosen = target
        if inspect:
            try:
                text = self.fetch_playlist(target)
            except httpx.HTTPError as exc:
                raise UnresolvedEpisode(f"{label}: cannot fetch playlist ({exc})") from exc
            variants, _ = parse_master(text, target)
            if variants:
                variant = pick_variant(variants, quality or self.settings.hls_quality)
                if variant is not None:
                    chosen = variant.uri
                    meta["variants"] = [str(v) for v in variants]
                    meta["chosen"] = str(variant)
            elif "#EXTINF" not in text and "#EXT-X-STREAM-INF" not in text:
                raise UnresolvedEpisode(f"{label}: {target.split('?')[0]} is not an m3u8 playlist")

        return EpisodeAsset(
            media_public_id=media.publicId if media else f"hls:{episode}",
            episode=episode,
            url=chosen,
            label=label,
            headers=headers,
            meta=meta,
        )

    def resolve_direct(self, url: str, *, episode: int = 1, quality: str | None = None) -> EpisodeAsset:
        """Convenience for `reel align --url ...` against a one-off stream."""
        return self.resolve(None, episode, url=url, quality=quality)

    # ------------------------------------------------------------------- health
    def check(self, media: Media | None, episode: int, **kwargs: Any) -> dict[str, Any]:
        """Fetch and summarise the stream without downloading media.

        Used by `reel probe` to confirm the token, headers and rendition
        selection are right before spending an alignment on it.
        """
        asset = self.resolve(media, episode, **kwargs)
        return {
            "label": asset.label,
            "url": asset.url,
            "master": asset.meta.get("url"),
            "variants": asset.meta.get("variants", []),
            "chosen": asset.meta.get("chosen", ""),
            "headers": sorted(asset.headers),
        }
