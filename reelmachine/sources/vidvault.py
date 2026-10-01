"""vidvault.to provider: resolve an episode to a downloadable file.

The site's own frontend flow, reproduced (and the whole thing needs no account):

1. ``GET  https://vidvault.to/api/get-token``
       -> ``{"t": "<64 hex>", "e": <expiry ms>}``  — **valid for about 60 s**
2. ``POST https://vidvault.to/api/download-proxy``
       headers ``x-request-token: <t>``
       body    ``{"type": "tv"|"movie", "tmdbId": int, "season": int, "episode": int}``
       -> a payload carrying renditions under several *generations* of key names
          (see ``parse_streams``): ``mp4Data.links[]``, ``mkvData``,
          ``mkvV2Data``, ``mkvV3Data`` — plus the older
          ``mp4Data.downloadInfo.data.downloads[]`` shape.
3. Each rendition's ``url`` is a short-lived CDN link.  The older generation
   signed it in the query string (``?sign=…&t=…``) and ``t`` was the issue time;
   the newer one is an opaque path token
   (``https://<worker>.workers.dev/dl/<token>``) carrying no timestamp at all,
   so age is simply unknowable there.  Either way it must be fetched and used in
   the same breath.

Notes that shaped this module:

* The token dies in ~60 s, so it is minted immediately before each lookup rather
  than cached across episodes.
* ``download-proxy`` keeps a server-side cache and will happily return entries
  whose signatures have long expired (observed: ``fromCache: true`` with links
  dead for 3.3 hours).  Freshness is therefore checked here, and the download is
  retried once through a forced miss.  That check needs a timestamp to work, so
  it only bites on the older ``?t=`` links — the newer opaque path tokens report
  ``age_s() is None`` and are always treated as fresh, leaving the retry (not the
  age filter) as the thing that recovers a dead link.
* The CDN 429s requests that do not look like the site's own client, so the
  browser ``User-Agent``/``Referer`` are sent on every request, including
  ffmpeg's.  A missing ``Referer`` is a hard 404, not a soft failure.
* The free tier is 360p/480p/720p; 1080p comes back ``vipLocked`` with an empty
  url and is skipped.  (As of late 2026 the newer payloads carry no ``vipLocked``
  flag at all and DO expose 1080p, so the ladder is walked on whatever is
  actually present.)
* By far the biggest practical problem is throughput, not access: the CDN
  throttles **per connection** (~0.25 MB/s each) and drops long transfers
  mid-stream.  Aggregate speed scales with concurrency (measured 0.17 MB/s on one
  connection, 1.78 MB/s on eight), so the downloader fetches byte ranges in
  parallel.  A single-stream fetch of one episode can otherwise take ten minutes
  and still truncate.

Because the links expire, the episode is **downloaded once** into the work
directory and every later run cuts from the local file.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .. import ffmpeg
from ..ffmpeg import JAPANESE_TAGS
from ..config import Settings, get_settings
from ..models import Media
from ..tmdb import TmdbClient, TmdbError, TmdbMatch
from .base import EpisodeAsset, SourceError, SourceProvider, UnresolvedEpisode

API_BASE = "https://vidvault.to/api"

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class Stream:
    resolution: int
    format: str
    size_bytes: int
    duration_s: int
    url: str
    vip_locked: bool = False
    group: str = ""
    language: str = ""

    @property
    def issued_at(self) -> int | None:
        """The `t=` parameter: when the CDN signature was minted."""
        match = re.search(r"[?&]t=(\d+)", self.url or "")
        return int(match.group(1)) if match else None

    def age_s(self, *, now: float | None = None) -> float | None:
        issued = self.issued_at
        if issued is None:
            return None
        return (now if now is not None else time.time()) - issued


_SIZE_RE = re.compile(r"^([\d.]+)\s*([KMGTP]?)i?B?$", re.I)
_SIZE_SCALE = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}

#: A range slower than this is treated as stalled rather than merely slow.  The
#: healthy worker has been measured at 0.4-1.6 MB/s per connection and the fast
#: one reaches 17 MB/s in aggregate, while the sick one sits at ~3 KiB/s — so
#: anything under 48 KiB/s is not going to finish a useful amount of an episode.
MIN_RATE_BYTES_S = 48 * 1024

#: Base delay between range retries; the real CDN drops connections often enough
#: that backing off matters, but tests set this to 0 so they do not crawl.
RETRY_BACKOFF_S = 0.5


def coerce_size(value: Any) -> int:
    """A rendition's ``size``, as bytes.

    The API has shipped this three different ways: an int of bytes, a *string*
    of bytes (``"32728862"``), and a human label (``"289 MB"``, ``"112.33 MB"``).
    Unparseable input yields 0, which the downloader treats as "unknown".
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    match = _SIZE_RE.match(str(value).strip())
    if not match:
        return 0
    try:
        number = float(match.group(1))
    except ValueError:
        return 0
    return int(number * _SIZE_SCALE[(match.group(2) or "").upper()])


def coerce_resolution(entry: dict[str, Any]) -> int:
    """A rendition's height in pixels, from whichever key carries it.

    Legacy payloads use ``resolution: 720``; the newer ones use
    ``quality: "720p"``.  Zero means "not stated" and is sorted last.
    """
    for key in ("resolution", "resolutions", "quality", "label", "name"):
        raw = entry.get(key)
        if raw in (None, ""):
            continue
        match = re.search(r"\d{3,4}", str(raw))
        if match:
            return int(match.group(0))
    return 0


def _candidate_entries(block: Any) -> list[Any]:
    """Every shape the API has used to hold a list of renditions.

    ``[ … ]`` · ``{"links": [ … ]}`` · ``{"files": [ … ]}`` ·
    ``{"downloads": [ … ]}`` · ``{"downloadInfo": {"data": {"downloads": [… ]}}}`` ·
    ``{"data": {"streams": [ … ]}}`` · or a single rendition dict with a ``url``.
    """
    if block is None:
        return []
    if isinstance(block, list):
        return list(block)
    if not isinstance(block, dict):
        return []
    for path in (("links",), ("files",), ("downloads",), ("streams",),
                 ("downloadInfo", "data", "downloads"), ("data", "streams")):
        node: Any = block
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, list):
            return list(node)
    if block.get("url"):
        return [block]
    return []


def _v3_entries(block: Any) -> list[Any]:
    """``mkvV3Data`` nests renditions one level deeper than everything else.

    ``{"downloads": [{"qualities": [{"quality": "720p", "episodes": [{"url": …}]}]}]}``
    — each quality carries a list of episodes, of which we want the one we asked
    for; the API does not label them, so they are flattened and the caller picks.
    """
    if not isinstance(block, dict) or not isinstance(block.get("downloads"), list):
        return _candidate_entries(block)
    country, language = block.get("country"), block.get("language")
    out: list[Any] = []
    for item in block["downloads"]:
        if not isinstance(item, dict):
            continue
        qualities = item.get("qualities")
        if isinstance(qualities, list):
            for quality in qualities:
                if not isinstance(quality, dict):
                    continue
                for episode in quality.get("episodes") or []:
                    if not isinstance(episode, dict):
                        continue
                    out.append({
                        **episode,
                        "quality": quality.get("quality"),
                        "country": episode.get("country") or country,
                        "language": episode.get("language") or language,
                    })
        elif item.get("url"):
            out.append({**item, "country": item.get("country") or country,
                        "language": item.get("language") or language})
    return out


def _to_stream(
    entry: Any, *, group: str, default_format: str, default_language: Any = None
) -> Stream | None:
    if not isinstance(entry, dict):
        return None
    label = str(entry.get("format") or "").strip().upper()
    return Stream(
        resolution=coerce_resolution(entry),
        format=label if label in {"MP4", "MKV"} else default_format,
        size_bytes=coerce_size(entry.get("size")),
        duration_s=int(entry.get("duration") or 0),
        url=str(entry.get("url") or ""),
        vip_locked=bool(entry.get("vipLocked")),
        group=group,
        # `mkvV2Data` states the language on the rendition, but a `files: […]`
        # variant states it once on the block, so both are honoured.
        language=str(entry.get("language") or default_language or ""),
    )


def is_japanese(stream: Stream) -> bool:
    """Whether the CDN labels this rendition as Japanese audio."""
    language = (stream.language or "").strip().lower()
    return language in {"japanese", "ja", "jpn", "jp", "日本語"} or language.startswith("jap")


def parse_ladder(quality: str | None) -> list[int]:
    """Turn a quality setting into a descending preference ladder.

    ``"1080,720,480,360"`` -> ``[1080, 720, 480, 360]``; a bare ``"720"`` is
    treated as a cap, i.e. ``[720, 480, 360]`` plus a fallback.
    """
    numbers = [int(n) for n in re.findall(r"\d+", str(quality or ""))]
    if not numbers:
        return [1080, 720, 480, 360]
    ordered: list[int] = []
    for value in numbers:
        if value not in ordered:
            ordered.append(value)
    return ordered


def pick_stream(
    streams: list[Stream],
    *,
    quality: str = "1080,720,480,360",
    max_age_s: float = 0.0,
    now: float | None = None,
) -> Stream | None:
    """Best usable stream, walking the quality ladder from the top down.

    Hard filters: VIP-locked entries and empty urls are never usable.

    **Japanese renditions win outright.**  The rest of the pipeline aligns against
    Nadeshiko's *Japanese* dialogue audio, so a dub cannot be aligned at all — and
    the CDN does serve dubs (one title here carried Hindi and English tracks and
    no Japanese).  When any Japanese rendition exists the ladder is walked over
    those only, even if that means dropping a resolution; a 1080p dub is worth
    less than a 720p original.  Payloads that state no language fall through to
    the old behaviour.

    The ladder is walked in order, so `1080,720,480,360` means "1080 if it is
    actually downloadable, else 720, else 480, else 360" — on the free tier 1080p
    used to come back `vipLocked` with an empty url, so it fell through to 720p.

    `max_age_s` is a *preference*, not a veto.  `download-proxy` caches its
    responses, so a reply can carry links minted hours ago, but the signature's
    real lifetime is not published and cannot be measured from outside — so
    stale links are only dropped when something fresher exists at the same rung.
    The caller attempts the download and re-mints on failure, which is the only
    reliable test.
    """
    usable = [s for s in streams if s.url and not s.vip_locked]
    if not usable:
        return None
    japanese = [s for s in usable if is_japanese(s)]
    usable = japanese or usable

    def age(stream: Stream) -> float:
        value = stream.age_s(now=now)
        return value if value is not None else 0.0

    def best_of(pool: list[Stream]) -> Stream:
        """Freshest link wins; a bigger file breaks a tie."""
        return min(pool, key=lambda s: (age(s), -s.size_bytes))

    for rung in parse_ladder(quality):
        if max_age_s and max_age_s > 0:
            fresh = [s for s in usable if s.resolution <= rung and age(s) <= max_age_s]
            if fresh:
                return best_of(fresh)
        at_or_below = [s for s in usable if s.resolution <= rung]
        if at_or_below:
            highest = max(s.resolution for s in at_or_below)
            return best_of([s for s in at_or_below if s.resolution == highest])

    # Nothing on offer is at or below any rung: take the smallest, since the
    # point of the ladder is to bound the download.
    smallest = min(s.resolution for s in usable)
    return best_of([s for s in usable if s.resolution == smallest])


def host_of(stream: Stream) -> str:
    """The CDN hostname a rendition is served from."""
    match = re.match(r"https?://([^/]+)", stream.url or "")
    return match.group(1) if match else ""


def host_rank(stream: Stream, prefer_hosts: Sequence[str]) -> int:
    """0 for a preferred host, 1 for anything else."""
    host = host_of(stream)
    if not prefer_hosts or not host:
        return 1
    return 0 if any(token and token in host for token in prefer_hosts) else 1


def rank_streams(
    streams: list[Stream],
    *,
    quality: str = "1080,720,480,360",
    max_age_s: float = 0.0,
    prefer_hosts: Sequence[str] = (),
    now: float | None = None,
) -> list[Stream]:
    """Every usable rendition, in the order they should be *tried*.

    The quality ladder stays in charge — a 1080p rendition is still offered
    before a 720p one — but within a rung the preferred host wins, and a
    Japanese tag after that.  Returning the whole order rather than one pick is
    what lets the caller fall back: `pick_stream` answers "which is best", this
    answers "and then what".

    Nothing here guarantees a rendition's audio.  An untagged stream from the
    preferred host is tried first precisely because it is fast, and the caller
    checks its audio before committing to the download.
    """
    usable = [s for s in streams if s.url and not s.vip_locked]
    if not usable:
        return []

    def age(stream: Stream) -> float:
        value = stream.age_s(now=now)
        return value if value is not None else 0.0

    def sort_key(stream: Stream) -> tuple:
        return (
            host_rank(stream, prefer_hosts),
            0 if is_japanese(stream) else 1,
            age(stream),
            -stream.size_bytes,
        )

    ordered: list[Stream] = []
    seen: set[str] = set()
    # Fresh-first inside each rung, when a freshness bound was asked for.
    for rung in parse_ladder(quality):
        pool = [s for s in usable if s.resolution <= rung]
        if not pool:
            continue
        highest = max(s.resolution for s in pool)
        rung_pool = [s for s in pool if s.resolution == highest]
        if max_age_s and max_age_s > 0:
            fresh = [s for s in rung_pool if age(s) <= max_age_s]
            rung_pool = fresh or rung_pool
        for stream in sorted(rung_pool, key=sort_key):
            if stream.url not in seen:
                seen.add(stream.url)
                ordered.append(stream)
    # Anything above every rung, so a "720" cap still has something to fall
    # back to rather than failing outright.
    for stream in sorted(usable, key=sort_key):
        if stream.url not in seen:
            seen.add(stream.url)
            ordered.append(stream)
    return ordered


class VidVaultProvider(SourceProvider):
    name = "vidvault"

    def __init__(self, settings: Settings | None = None, *, tmdb: TmdbClient | None = None):
        super().__init__(settings)
        self.tmdb = tmdb or TmdbClient(settings=self.settings)
        self._token: str = ""
        self._token_expiry: float = 0.0

    def missing(self) -> list[str]:
        if not self.settings.tmdb_api_key:
            return ["REEL_TMDB_API_KEY is not set (needed to bridge Nadeshiko ids to TMDB)"]
        return []

    # ------------------------------------------------------------------ http
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.settings.vidvault_user_agent or DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": self.settings.vidvault_referer,
            "Origin": self.settings.vidvault_referer.rstrip("/"),
        }

    def _request(self, url: str, *, data: bytes | None = None, extra: dict[str, str] | None = None) -> dict[str, Any]:
        headers = self.headers()
        headers.update(extra or {})
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        last: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", "replace")[:200]
                except Exception:  # noqa: BLE001
                    pass
                if exc.code in (403, 429):
                    last = SourceError(
                        f"vidvault rate-limited or refused ({exc.code}) for "
                        f"{url.split('?')[0]}: {body.strip()}"
                    )
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise SourceError(f"vidvault HTTP {exc.code} for {url}: {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last = SourceError(f"vidvault unreachable: {exc}")
                time.sleep(1.0 * (attempt + 1))
        raise last or SourceError("vidvault request failed")

    def request_token(self, *, force: bool = False) -> str:
        """The `x-request-token`, which lives about 60 seconds."""
        now = time.time()
        if not force and self._token and now < self._token_expiry - 10:
            return self._token
        payload = self._request(f"{API_BASE}/get-token")
        token = payload.get("t") or ""
        if not token:
            raise SourceError("vidvault /get-token returned no token")
        self._token = token
        expiry_ms = payload.get("e")
        self._token_expiry = (float(expiry_ms) / 1000.0) if expiry_ms else now + 60.0
        return token

    # ------------------------------------------------------------------ lookup
    def lookup(
        self,
        tmdb_id: int,
        *,
        media_type: str = "tv",
        season: int = 1,
        episode: int = 1,
    ) -> dict[str, Any]:
        """Raw `download-proxy` payload."""
        token = self.request_token()
        body = json.dumps(
            {"type": media_type, "tmdbId": int(tmdb_id), "season": int(season), "episode": int(episode)}
        ).encode()
        return self._request(
            f"{API_BASE}/download-proxy",
            data=body,
            extra={"Content-Type": "application/json", "x-request-token": token},
        )

    @staticmethod
    def parse_streams(payload: dict[str, Any]) -> list[Stream]:
        """Flatten every generation of `download-proxy`'s stream list.

        The payload was reshaped in late 2026: renditions moved from
        ``mp4Data.downloadInfo.data.downloads[]`` (rich: integer ``size``,
        ``duration``, ``vipLocked``) to ``mp4Data.links[]`` (lean: ``url``,
        ``quality: "720p"``, ``size: "289 MB"``) with siblings ``mkvData``,
        ``mkvV2Data`` and ``mkvV3Data``.  Observed live: *no* title still returns
        the old shape, so parsing only that is why resolution silently yielded
        zero streams.  Both are handled here, because the old one is what the
        fixture data and any server-side rollback would look like.

        Note the newer ``mp4Data.links`` entries are misnamed: they serve
        Matroska (``video/x-matroska``, EBML magic) despite the key.
        """
        if not isinstance(payload, dict):
            return []
        out: list[Stream] = []

        def absorb(block: Any, *, group: str, default_format: str, v3: bool = False) -> None:
            source = _v3_entries(block) if v3 else _candidate_entries(block)
            block_language = block.get("language") if isinstance(block, dict) else None
            for entry in source:
                stream = _to_stream(
                    entry, group=group, default_format=default_format, default_language=block_language
                )
                if stream is not None:
                    out.append(stream)

        mp4 = payload.get("mp4Data")
        if isinstance(mp4, list):
            absorb(mp4, group="MP4", default_format="MP4")
        elif isinstance(mp4, dict):
            # `links` first: on a payload carrying both, the newer list is the
            # one whose signatures are fresh.
            if isinstance(mp4.get("links"), list):
                absorb(mp4["links"], group="MP4", default_format="MP4")
            else:
                absorb(mp4, group="MP4", default_format="MP4")

        for key in ("mkvData", "mkvV2Data"):
            absorb(payload.get(key), group="MKV", default_format="MKV")
        absorb(payload.get("mkvV3Data"), group="MKV", default_format="MKV", v3=True)

        # Identical renditions can appear twice once several key families carry
        # the same links; keep the first occurrence of each (resolution, url).
        seen: set[tuple[int, str]] = set()
        unique: list[Stream] = []
        for stream in out:
            key = (stream.resolution, stream.url)
            if key in seen:
                continue
            seen.add(key)
            unique.append(stream)
        return unique

    # ----------------------------------------------------------------- resolve
    def match_media(self, media: Media | None) -> TmdbMatch | None:
        if media is None:
            return None
        return self.tmdb.match_media(media)

    def download_dir(self) -> Path:
        path = self.settings.vidvault_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def local_path(self, match: TmdbMatch, episode: int, resolution: int) -> Path:
        stem = f"tmdb{match.tmdb_id}-s{match.season:02d}e{episode:03d}-{resolution}p"
        return self.download_dir() / f"{stem}.mp4"

    def resolve(
        self,
        media: Media | None,
        episode: int,
        *,
        download: bool | None = None,
        quality: str | None = None,
        match: TmdbMatch | None = None,
        season: int | None = None,
        tmdb_id: int | None = None,
        media_type: str | None = None,
        refresh: bool = False,
        verbose: bool = False,
        **_: Any,
    ) -> EpisodeAsset:
        """Resolve one episode, downloading it unless `download=False`.

        `download=False` answers "is this episode obtainable, and how big is it?"
        without spending bandwidth — `reel plan` uses it.
        """
        settings = self.settings
        quality = quality or settings.vidvault_quality
        should_download = settings.vidvault_download if download is None else download

        if match is None and tmdb_id is not None:
            match = TmdbMatch(
                tmdb_id=int(tmdb_id),
                media_type=media_type or "tv",
                season=season or 1,
                title=(media.nameEn if media else "") or str(tmdb_id),
                year=None,
                score=9.9,
                query=str(tmdb_id),
                reason="explicit tmdb id",
            )
        if match is None:
            match = self.match_media(media)
        if match is None:
            label = (media.nameEn if media else "?") or "?"
            raise UnresolvedEpisode(
                f"{label}: no TMDB match, so vidvault cannot be queried. "
                "Set REEL_TMDB_API_KEY, or pass the id explicitly."
            )
        if season is not None:
            match = TmdbMatch(**{**_as_dict(match), "season": season})

        target_season = match.season
        payload = self.lookup(
            match.tmdb_id, media_type=match.media_type, season=target_season, episode=episode
        )
        streams = self.parse_streams(payload)
        max_age = settings.vidvault_max_link_age_s
        chosen = pick_stream(streams, quality=quality, max_age_s=max_age)

        if chosen is None:
            stale = [s for s in streams if s.url and not s.vip_locked]
            if stale:
                # Everything was real but expired: mint a fresh token and retry
                # once, since the cache may simply have been old.
                if verbose:
                    print(f"    vidvault: {len(stale)} cached stream(s) expired; re-requesting")
                self.request_token(force=True)
                payload = self.lookup(
                    match.tmdb_id, media_type=match.media_type, season=target_season, episode=episode
                )
                streams = self.parse_streams(payload)
                chosen = pick_stream(streams, quality=quality, max_age_s=max_age)
        if chosen is None:
            locked = [s.resolution for s in streams if s.vip_locked]
            if locked:
                raise UnresolvedEpisode(
                    f"{match} ep{episode}: only VIP-locked renditions available "
                    f"({', '.join(f'{r}p' for r in locked)}); lower REEL_VIDVAULT_QUALITY"
                )
            raise UnresolvedEpisode(
                f"{match} ep{episode}: vidvault returned no usable stream "
                f"({len(streams)} entr{'y' if len(streams) == 1 else 'ies'})"
            )

        # A duration mismatch is the signal that season/episode numbering drifted.
        # The newer payloads carry no `duration` at all, so Nadeshiko's own length
        # doubles as the fallback for the asset's duration.
        claimed: int | None = None
        if media is not None:
            try:
                claimed = self.episode_length_s(media, episode)
            except Exception:  # noqa: BLE001
                claimed = None
        length_warning = ""
        if claimed and chosen.duration_s and abs(claimed - chosen.duration_s) > 90:
            length_warning = (
                f"vidvault says {chosen.duration_s}s but Nadeshiko says {claimed}s — "
                f"season/episode numbering may not line up"
            )
        if chosen.language and not is_japanese(chosen):
            dub_warning = (
                f"the only rendition available is tagged {chosen.language!r}, not Japanese — "
                f"the dialogue alignment needs the original audio and will refuse"
            )
            length_warning = f"{length_warning}; {dub_warning}" if length_warning else dub_warning

        dest = self.local_path(match, episode, chosen.resolution)
        asset = EpisodeAsset(
            media_public_id=media.publicId if media else f"tmdb:{match.tmdb_id}",
            episode=episode,
            url=str(dest),
            label=f"{match.title} S{target_season}E{episode} ({chosen.resolution}p)",
            local_path=dest if dest.is_file() else None,
            duration_ms=(chosen.duration_s or claimed or 0) * 1000,
            meta={
                "tmdb": match.tmdb_id,
                "mediaType": match.media_type,
                "season": target_season,
                "resolution": chosen.resolution,
                "sizeBytes": chosen.size_bytes,
                "remoteUrl": chosen.url,
                "allStreams": [
                    {"resolution": s.resolution, "size": s.size_bytes, "vip": s.vip_locked,
                     "group": s.group, "language": s.language, "ageS": s.age_s()}
                    for s in streams
                ],
                "matchReason": match.reason,
                "warning": length_warning,
            },
        )
        if length_warning and verbose:
            print(f"    ! {length_warning}")

        if not should_download:
            return asset
        if dest.is_file() and dest.stat().st_size > 0 and not refresh:
            asset.local_path = dest
            return asset

        # Walk the renditions in preference order rather than committing to one.
        # Two independent things go wrong here and each costs a *candidate*, not
        # the episode: the preferred host is fast but its renditions may be
        # dubs, and the Japanese-tagged ones come from a worker that has been
        # measured stalling at a few KiB/s for minutes at a time.
        candidates = rank_streams(
            streams,
            quality=quality,
            max_age_s=max_age,
            prefer_hosts=list(getattr(settings, "vidvault_prefer_hosts", []) or []),
        )
        if not candidates:
            raise UnresolvedEpisode(f"{match} ep{episode}: nothing usable to download")

        path: Path | None = None
        last_error: SourceError | None = None
        for candidate in candidates:
            # A rendition the CDN itself labels as a dub is never worth trying:
            # the alignment needs the original audio and would refuse it.
            if candidate.language and not is_japanese(candidate):
                if verbose:
                    print(f"    vidvault: skipping {candidate.language} rendition")
                continue
            # Untagged means unknown, not safe.  A few MB settles it for the
            # price of a few MB — versus the whole episode if the aligner is
            # left to discover the dub at the end.
            if not candidate.language:
                languages = self.audio_languages(candidate.url, verbose=verbose)
                if languages and not any(lang in JAPANESE_TAGS for lang in languages):
                    if verbose:
                        print(
                            f"    vidvault: {candidate.resolution}p on "
                            f"{host_of(candidate)} carries {', '.join(languages)} audio, "
                            f"not Japanese — trying the next rendition"
                        )
                    continue

            # The signed link is short-lived, and a large episode on the slow
            # worker takes far longer to fetch than the signature lives.  When
            # it dies mid-transfer every remaining range fails and retrying that
            # same URL cannot help — the only recovery is a fresh link, which is
            # what the checkpointed .part file is for: a re-mint resumes instead
            # of restarting.
            stream = candidate
            for attempt in range(3):
                dest = self.local_path(match, episode, stream.resolution)
                try:
                    path = self.download(stream, dest, refresh=refresh, verbose=verbose)
                    break
                except SourceError as exc:
                    last_error = exc
                    if attempt == 2:
                        break
                    if verbose:
                        print(f"    vidvault: {exc} — minting a fresh link and resuming")
                    self.request_token(force=True)
                    payload = self.lookup(
                        match.tmdb_id, media_type=match.media_type,
                        season=target_season, episode=episode,
                    )
                    streams = self.parse_streams(payload)
                    refreshed = pick_stream(streams, quality=quality, max_age_s=max_age)
                    if refreshed is None:
                        break
                    stream = refreshed
            if path is not None:
                break
            if verbose:
                print(f"    vidvault: {candidate.resolution}p on {host_of(candidate)} "
                      f"gave up; trying another rendition")

        if path is None:
            raise last_error or SourceError(
                "every rendition failed — either the CDN is blocking this IP or "
                "no rendition carried Japanese audio"
            )
        chosen = stream

        asset.local_path = path
        asset.url = str(path)
        asset.headers = dict(self.headers())
        asset.label = f"{match.title} S{target_season}E{episode} ({stream.resolution}p)"
        asset.meta["resolution"] = stream.resolution
        asset.meta["remoteUrl"] = stream.url
        asset.meta["sizeBytes"] = stream.size_bytes
        if stream.duration_s:
            asset.duration_ms = stream.duration_s * 1000
        return asset

    # ------------------------------------------------------------------- health
    def check(self, media: Media | None, episode: int, **kwargs: Any) -> dict[str, Any]:
        """Report what vidvault has for this episode *without downloading it*.

        `reel probe` uses this, so asking "do you have it?" never costs a
        multi-hundred-megabyte download.
        """
        match = kwargs.pop("match", None) or self.match_media(media)
        if match is None:
            label = (media.nameEn if media else "?") or "?"
            raise UnresolvedEpisode(f"{label}: no TMDB match; set REEL_TMDB_API_KEY")
        asset = self.resolve(media, episode, download=False, match=match, **kwargs)
        streams = asset.meta.get("allStreams") or []
        return {
            "label": asset.label,
            "tmdb": match.tmdb_id,
            "mediaType": match.media_type,
            "season": match.season,
            "matchReason": match.reason,
            "matchScore": round(match.score, 2),
            "streams": streams,
            "selected": f"{asset.meta.get('resolution')}p" if asset.meta.get("resolution") else "",
            "sizeBytes": asset.meta.get("sizeBytes"),
            "wouldDownloadTo": str(asset.local_path or ""),
            "warning": asset.meta.get("warning", ""),
            "headers": sorted(self.headers()),
        }

    def episode_length_s(self, media: Media, episode: int) -> int | None:
        """Nadeshiko's own duration for the episode, used as a cross-check."""
        client = getattr(self, "_nadeshiko", None)
        if client is None:
            return None
        try:
            meta = client.get_episode(media.publicId, episode)
        except Exception:  # noqa: BLE001
            return None
        return meta.lengthSeconds

    # ---------------------------------------------------------------- download
    def audio_languages(
        self, url: str, *, probe_bytes: int = 6 << 20, verbose: bool = False
    ) -> list[str]:
        """Audio language tags from the head of a rendition.

        Matroska and MP4 both keep their track table near the start, so a few
        MB of ranged fetch is enough to see which languages a rendition really
        carries.  This is the only way to tell a dub from the original *before*
        spending a few hundred MB on it, and the provider's own `language`
        field is absent on exactly the fast-host renditions that need checking.

        Returns [] when the answer cannot be determined, which the caller
        treats as "unknown, probably fine" rather than "definitely a dub".
        """
        headers = self.headers()
        headers["Accept"] = "*/*"
        headers["Range"] = f"bytes=0-{probe_bytes - 1}"
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=45) as response:
                blob = response.read(probe_bytes)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            if verbose:
                print(f"    vidvault: could not probe audio ({str(exc)[:60]})")
            return []
        if not blob:
            return []

        import tempfile

        from .. import ffmpeg as ffmpeg_mod

        handle = Path(tempfile.mkdtemp(prefix="reel-probe-")) / "head.bin"
        try:
            handle.write_bytes(blob)
            info = ffmpeg_mod.probe(handle)
        except Exception:  # noqa: BLE001 - a truncated head may not parse
            return []
        finally:
            handle.unlink(missing_ok=True)
        return [lang.lower() for lang in info.audio_langs]

    def probe_url(self, url: str) -> tuple[int, bool]:
        """(total bytes, honours Range) — one cheap request, no body.

        Only one byte is asked for, so this costs nothing even on a slow link.
        Deliberately *not* called ``probe``: `SourceProvider.probe` already means
        "ffprobe this asset" and is called all over the pipeline, so shadowing it
        would break every run past the download.
        """
        headers = self.headers()
        headers["Accept"] = "*/*"
        headers["Range"] = "bytes=0-0"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status == 206:
                    match = re.search(r"/(\d+)\s*$", response.headers.get("Content-Range") or "")
                    return (int(match.group(1)) if match else 0), True
                return int(response.headers.get("Content-Length") or 0), False
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise SourceError(
                    f"CDN refused the download ({exc.code}). The signed link is single-use and "
                    f"short-lived, or this IP is blocked by the CDN. Re-run to mint a fresh link."
                ) from exc
            raise SourceError(f"CDN download failed: HTTP {exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SourceError(f"CDN download failed: {exc}") from exc

    @staticmethod
    def plan_segments(total: int, connections: int, *, chunk: int = 4 << 20) -> list[tuple[int, int]]:
        """Inclusive byte ranges to fetch in parallel, ~`chunk` bytes each.

        Ranges are sized by `chunk`, *not* by dividing the file between
        `connections`: the workers are a pool that drains however many ranges
        exist, so `connections` bounds concurrency while `chunk` bounds how much
        work one stalled or expired link can waste.  That matters a lot here —
        the slow Japanese worker delivers ~0.03–0.25 MB/s, so a quarter-file
        range is many minutes of transfer to throw away on a single retry.
        """
        if total <= 0:
            return []
        count = max(1, (total + chunk - 1) // chunk)
        span = (total + count - 1) // count
        ranges: list[tuple[int, int]] = []
        start = 0
        while start < total:
            end = min(start + span, total) - 1
            ranges.append((start, end))
            start = end + 1
        return ranges

    @staticmethod
    def _read_checkpoint(
        checkpoint: Path, total: int, part: Path, ranges: list[tuple[int, int]]
    ) -> set[int]:
        """Range indices already banked in `part`, or none if we cannot be sure.

        The checkpoint records the *layout* it was written against.  Without that
        check, changing `chunk` or the connection count would reinterpret old
        indices against new boundaries and mark ranges complete whose bytes were
        never written — leaving silent holes in the middle of the episode.  A
        mismatch just re-fetches, which is cheap next to a corrupt file.
        """
        if not (part.is_file() and checkpoint.is_file()):
            return set()
        try:
            state = json.loads(checkpoint.read_text())
        except (ValueError, OSError):
            return set()
        if int(state.get("total") or 0) != total:
            return set()
        stored = [tuple(int(v) for v in pair) for pair in state.get("ranges") or []]
        if stored != ranges:
            return set()
        return {int(i) for i in state.get("done") or [] if 0 <= int(i) < len(ranges)}

    @staticmethod
    def _write_checkpoint(checkpoint: Path, total: int, done: set[int], ranges: list[tuple[int, int]]) -> None:
        try:
            checkpoint.write_text(
                json.dumps({"total": total, "ranges": [list(r) for r in ranges], "done": sorted(done)})
            )
        except OSError:
            pass  # a lost checkpoint only costs a re-fetch, never correctness

    def fetch_range(
        self,
        url: str,
        part: Path,
        start: int,
        end: int,
        *,
        on_bytes: Any = None,
        attempts: int = 4,
    ) -> None:
        """Fetch `start`-`end` into `part` at the right offset, retrying the range.

        Retries are per *range*, so a connection dropped at 90% costs one range,
        not the whole episode — which is the failure this CDN actually exhibits.
        """
        length = end - start + 1
        headers = self.headers()
        headers["Accept"] = "*/*"
        headers["Range"] = f"bytes={start}-{end}"
        stall_after = float(getattr(self.settings, "vidvault_stall_timeout_s", 45.0) or 0.0)
        last: Exception | None = None
        for attempt in range(attempts):
            got = 0
            started_at = progress_at = time.time()
            try:
                request = urllib.request.Request(url, headers=headers)
                # The socket timeout is the *silence* bound; the throughput
                # floor below catches the other half of the problem.
                socket_timeout = max(5.0, min(60.0, stall_after)) if stall_after else 60.0
                with urllib.request.urlopen(request, timeout=socket_timeout) as response:
                    if response.status != 206:
                        raise SourceError(
                            f"CDN ignored the Range request (HTTP {response.status}); "
                            "cannot download in segments"
                        )
                    with part.open("r+b") as handle:
                        handle.seek(start)
                        while got < length:
                            now = time.time()
                            if stall_after and now - progress_at > stall_after:
                                raise SourceError(
                                    f"segment {start}-{end}: stalled — no data for "
                                    f"{stall_after:.0f}s after {got} bytes"
                                )
                            # A link trickling a few KiB/s is not *silent*, so no
                            # socket timeout ever fires — it simply never
                            # finishes.  Measured on the slow worker: 3 KiB/s,
                            # which is what wedged two downloads until the
                            # process was killed by hand.
                            elapsed = now - started_at
                            if (
                                stall_after
                                and elapsed > stall_after
                                and got / elapsed < MIN_RATE_BYTES_S
                            ):
                                raise SourceError(
                                    f"segment {start}-{end}: too slow — "
                                    f"{got / elapsed / 1024:.1f} KiB/s over {elapsed:.0f}s"
                                )
                            # `read1` returns as soon as *any* data arrives.
                            # `read(n)` would block until it had all n bytes, so
                            # the checks above would never get a turn.
                            reader = getattr(response, "read1", None) or response.read
                            chunk = reader(min(1 << 16, length - got))
                            if not chunk:
                                break
                            handle.write(chunk)
                            got += len(chunk)
                            progress_at = time.time()
                            if on_bytes is not None:
                                on_bytes(len(chunk))
                if got == length:
                    return
                last = SourceError(f"segment {start}-{end}: short read ({got} of {length} bytes)")
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429) and attempt == attempts - 1:
                    raise SourceError(
                        f"CDN refused the download ({exc.code}) mid-transfer. The signed link is "
                        f"short-lived — re-run to mint a fresh link."
                    ) from exc
                last = SourceError(f"segment {start}-{end}: HTTP {exc.code} {exc.reason}")
            except SourceError as exc:
                last = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = SourceError(f"segment {start}-{end}: {exc}")
            if got and on_bytes is not None:
                on_bytes(-got)  # this range will be re-fetched from the start
            time.sleep(min(4.0, RETRY_BACKOFF_S * (attempt + 1)))
        raise last or SourceError(f"segment {start}-{end} failed")

    def download_segmented(
        self,
        url: str,
        part: Path,
        dest: Path,
        total: int,
        *,
        connections: int = 8,
        verbose: bool = False,
    ) -> Path:
        """Parallel byte-range fetch of `total` bytes into `part`, then move to `dest`."""
        ranges = self.plan_segments(total, connections)
        checkpoint = Path(f"{part}.json")
        done = self._read_checkpoint(checkpoint, total, part, ranges)
        part.parent.mkdir(parents=True, exist_ok=True)
        with part.open("a+b") as handle:
            handle.truncate(total)

        todo = [i for i in range(len(ranges)) if i not in done]
        state = {
            "have": sum(ranges[i][1] - ranges[i][0] + 1 for i in done),
            "done": len(done),
            "total_count": len(ranges),
            "started": time.time(),
            "lock": threading.Lock(),
            "cursor": 0,
        }
        failures: list[Exception] = []

        def on_bytes(count: int) -> None:
            with state["lock"]:
                state["have"] += count

        def run(index: int) -> None:
            start, end = ranges[index]
            try:
                self.fetch_range(url, part, start, end, on_bytes=on_bytes)
            except Exception as exc:  # noqa: BLE001 — collected, then re-raised as SourceError
                with state["lock"]:
                    failures.append(exc)
                return
            with state["lock"]:
                done.add(index)
                state["done"] += 1
                self._write_checkpoint(checkpoint, total, done, ranges)

        def worker() -> None:
            while True:
                with state["lock"]:
                    if state["cursor"] >= len(todo):
                        return
                    index = todo[state["cursor"]]
                    state["cursor"] += 1
                run(index)

        if verbose:
            print(
                f"      {len(todo)} range(s) of ~{total / max(1, len(ranges)) / 1e6:.0f} MB "
                f"across {min(connections, len(todo)) or 1} connection(s)"
            )

        threads = [
            threading.Thread(target=worker, daemon=True)
            for _ in range(max(1, min(connections, len(todo))))
        ]
        stop = threading.Event()
        reporter = threading.Thread(target=self._report, args=(state, total, stop), daemon=True) if verbose else None
        if reporter:
            reporter.start()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        stop.set()
        if reporter:
            reporter.join(timeout=2.0)

        if verbose:
            elapsed = max(0.1, time.time() - state["started"])
            print(f"\n      {total / 1e6:.1f} MB in {elapsed:.1f}s ({total / elapsed / 1e6:.2f} MB/s average)")

        if failures:
            raise SourceError(
                f"{len(failures)} of {len(ranges)} segment(s) failed: {failures[0]} "
                f"(kept {part.name} — re-run to resume)"
            )
        if len(done) != len(ranges):
            raise SourceError(f"only {len(done)} of {len(ranges)} segments completed (kept {part.name})")

        checkpoint.unlink(missing_ok=True)
        shutil.move(str(part), str(dest))
        return dest

    @staticmethod
    def _report(state: dict[str, Any], total: int, stop: threading.Event) -> None:
        while not stop.wait(2.0):
            with state["lock"]:
                have, done = state["have"], state["done"]
            elapsed = max(0.1, time.time() - state["started"])
            pct = have / total * 100 if total else 0.0
            print(
                f"      {pct:5.1f}% {have / 1e6:7.1f}/{total / 1e6:.1f} MB  "
                f"{have / elapsed / 1e6:5.2f} MB/s  [{done} ranges done]",
                end="\r",
            )
            if done >= state["total_count"]:
                return

    def download(
        self,
        stream: Stream,
        dest: Path,
        *,
        refresh: bool = False,
        verbose: bool = False,
        connections: int | None = None,
    ) -> Path:
        """Fetch a signed CDN URL to `dest`, resuming a partial file if present.

        The CDN throttles each connection to roughly 0.25 MB/s and cuts long
        transfers off part-way, so anything sizeable is fetched as several byte
        ranges at once.  `connections=1` forces the plain single-stream path.
        """
        if not stream.url:
            raise SourceError("cannot download an empty stream url")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file() and dest.stat().st_size > 0 and not refresh:
            return dest

        if connections is None:
            connections = int(getattr(self.settings, "vidvault_connections", 8) or 1)

        part = dest.with_suffix(dest.suffix + ".part")
        total, ranged = self.probe_url(stream.url)
        if ranged and connections > 1 and len(self.plan_segments(total, connections)) > 1:
            # download_segmented only moves the file into place once every range
            # has landed, so a return from it is already a complete download.
            return self.download_segmented(
                stream.url, part, dest, total, connections=connections, verbose=verbose
            )

        expected = total or stream.size_bytes
        have = part.stat().st_size if part.is_file() else 0
        headers = self.headers()
        headers["Accept"] = "*/*"
        if have:
            headers["Range"] = f"bytes={have}-"

        request = urllib.request.Request(stream.url, headers=headers)
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0) + have
                mode = "ab" if have and response.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                with part.open(mode) as handle:
                    last_report = 0.0
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        handle.write(chunk)
                        have += len(chunk)
                        if verbose and time.time() - last_report > 2.0:
                            last_report = time.time()
                            pct = f"{have / total * 100:5.1f}%" if total else "  ?  "
                            rate = have / max(0.1, time.time() - started) / 1e6
                            print(f"      {pct} {have / 1e6:7.1f} MB  {rate:5.1f} MB/s", end="\r")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise SourceError(
                    f"CDN refused the download ({exc.code}). The signed link is single-use and "
                    f"short-lived, or this IP is blocked by the CDN. Re-run to mint a fresh link."
                ) from exc
            raise SourceError(f"CDN download failed: HTTP {exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SourceError(f"CDN download failed: {exc}") from exc

        if verbose:
            print()
        return self._verified(part, expected, dest)

    @staticmethod
    def _verified(part: Path, expected: int, dest: Path) -> Path:
        """Move a finished `.part` into place, refusing an obviously short one.

        `expected` comes from the CDN's own Content-Length / Content-Range; the
        API's advertised size is rounded ("289 MB") and has been wrong by several
        percent, so it is only ever a fallback.
        """
        got = part.stat().st_size
        if expected and got < expected * 0.98:
            raise SourceError(
                f"truncated download: got {got / 1e6:.1f} MB of {expected / 1e6:.1f} MB "
                f"(kept {part.name} for resume)"
            )
        if part != dest:
            shutil.move(str(part), str(dest))
        return dest


def _as_dict(match: TmdbMatch) -> dict[str, Any]:
    return {
        "tmdb_id": match.tmdb_id,
        "media_type": match.media_type,
        "season": match.season,
        "title": match.title,
        "year": match.year,
        "score": match.score,
        "query": match.query,
        "reason": match.reason,
    }
