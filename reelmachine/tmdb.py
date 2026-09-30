"""TMDB lookup: bridging a Nadeshiko title to the ids download sites use.

Nadeshiko identifies media by AniList id (`externalIds.anilist`, populated for
19 of 20 titles sampled) while download services key on TMDB.  `externalIds.tmdb`
is populated for roughly 1 in 20, so the bridge has to be built from the title.

Two things make that harder than a plain search:

* **Season folding.** Nadeshiko treats each anime season as its own entry
  ("Rent-a-Girlfriend Season 2"), TMDB folds them into one show with seasons.
  The season number therefore has to be parsed out of the title and removed
  before searching, or the query matches nothing useful.
* **Episode numbering.** Once the season is right, the episode number is assumed
  to line up.  That assumption is checked downstream: the stream carries a
  `duration`, Nadeshiko carries `Episode.lengthSeconds`, and the audio
  alignment is the final gate.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .models import Media
from .sources.base import SourceError

TMDB_BASE = "https://api.themoviedb.org/3"
MIN_SCORE = 1.8

# "Season 2", "2nd Season", "Part 2", "Cour 2", "Season 02"
_SEASON_RE = re.compile(
    r"\b(?:season|part|cour)\s*(\d+)\b"
    r"|\b(\d+)\s*(?:st|nd|rd|th)\s+season\b",
    re.IGNORECASE,
)
# Titles that continue a show without saying "season".
_CONTINUATION_RE = re.compile(r"\s*(?:&|and)\s+beyond\b", re.IGNORECASE)


class TmdbError(SourceError):
    """A TMDB lookup problem, reported like any other source failure."""


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_season(title: str) -> tuple[str, int]:
    """Return (base title, season number) for a Nadeshiko media name.

    >>> split_season("Rent-a-Girlfriend Season 2")
    ('Rent-a-Girlfriend', 2)
    >>> split_season("Blood Blockade Battlefront & Beyond")
    ('Blood Blockade Battlefront', 2)
    """
    name = title or ""
    season = 1
    match = _SEASON_RE.search(name)
    if match:
        season = int(match.group(1) or match.group(2))
        name = _SEASON_RE.sub(" ", name)
    elif _CONTINUATION_RE.search(name):
        # "& Beyond" is how a sequel is titled, not a season marker.
        season = 2
        name = _CONTINUATION_RE.sub(" ", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" :-–—,")
    return name or title, season


@dataclass(slots=True)
class TmdbMatch:
    tmdb_id: int
    media_type: str          # "tv" | "movie"
    season: int
    title: str
    year: int | None
    score: float
    query: str
    reason: str = ""

    def __str__(self) -> str:  # pragma: no cover - display helper
        if self.media_type == "movie":
            return f"{self.title} ({self.year}) [movie] tmdb={self.tmdb_id}"
        return f"{self.title} ({self.year}) S{self.season} [tv] tmdb={self.tmdb_id}"


@dataclass(slots=True)
class _Cache:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str) -> Any | None:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


class TmdbClient:
    def __init__(self, api_key: str | None = None, *, settings: Settings | None = None, timeout: float = 25.0):
        self.settings = settings or get_settings()
        self.api_key = api_key or self.settings.tmdb_api_key
        self.timeout = timeout
        self._cache = _Cache(self.settings.cache_dir / "tmdb.json")
        self._cache.load()

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        if not self.api_key:
            raise TmdbError(
                "no TMDB API key. Set REEL_TMDB_API_KEY in .env — a free key takes a "
                "minute at https://www.themoviedb.org/settings/api"
            )
        params["api_key"] = self.api_key
        params.setdefault("language", "en-US")
        url = f"{TMDB_BASE}{path}?{urllib.parse.urlencode(params)}"
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                self._cache.put(url, payload)
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 429 or 500 <= exc.code < 600:
                    time.sleep(min(2**attempt, 8))
                    continue
                raise TmdbError(f"TMDB {exc.code} for {path}: {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                time.sleep(min(2**attempt, 8))
                if attempt == 3:
                    raise TmdbError(f"TMDB unreachable: {exc}") from exc
        raise TmdbError(f"TMDB gave up on {path}")

    # ------------------------------------------------------------------ search
    def search(self, query: str, media_type: str) -> list[dict[str, Any]]:
        payload = self._get(f"/search/{media_type}", query=query)
        return payload.get("results") or []

    def find_by_imdb(self, imdb_id: str) -> list[dict[str, Any]]:
        payload = self._get(f"/find/{imdb_id}", external_source="imdb_id")
        return (payload.get("tv_results") or []) + (payload.get("movie_results") or [])

    def season_episode_count(self, tmdb_id: int, season: int) -> int:
        try:
            payload = self._get(f"/tv/{tmdb_id}/season/{season}")
        except TmdbError:
            return 0
        return len(payload.get("episodes") or [])

    # ------------------------------------------------------------------- match
    def match_media(self, media: Media) -> TmdbMatch | None:
        """Best TMDB match for a Nadeshiko media entry, or None."""
        cached = self._cache.get(f"match:{media.publicId}")
        if cached:
            return TmdbMatch(**cached)

        # A direct TMDB id always wins — no guessing needed.
        if media.externalIds.tmdb:
            try:
                tmdb_id = int(media.externalIds.tmdb)
            except (TypeError, ValueError):
                tmdb_id = 0
            if tmdb_id:
                kind = "movie" if (media.airingFormat or "").upper() == "MOVIE" else "tv"
                candidate = self._describe(tmdb_id, kind)
                if candidate:
                    candidate = TmdbMatch(
                        **{**candidate.__dict__, "reason": "externalIds.tmdb"}
                    )
                    self._cache.put(f"match:{media.publicId}", _as_dict(candidate))
                    return candidate

        year = _year_of(media.startDate) or media.seasonYear
        kinds = ["movie"] if (media.airingFormat or "").upper() == "MOVIE" else ["tv", "movie"]

        best: TmdbMatch | None = None
        for name in [n for n in (media.nameEn, media.nameRomaji) if n]:
            base, season = split_season(name)
            for kind in kinds:
                try:
                    results = self.search(base, kind)
                except TmdbError:
                    raise
                for result in results[:8]:
                    scored = _score(result, base, year, kind)
                    if scored is None:
                        continue
                    score, title, result_year = scored
                    if best is None or score > best.score:
                        best = TmdbMatch(
                            tmdb_id=int(result["id"]),
                            media_type=kind,
                            season=season,
                            title=title,
                            year=result_year,
                            score=score,
                            query=base,
                            reason=f"title search on {media.nameEn[:40]!r}",
                        )
        if best is not None and best.score >= MIN_SCORE:
            self._cache.put(f"match:{media.publicId}", _as_dict(best))
            return best
        return None

    def _describe(self, tmdb_id: int, kind: str) -> TmdbMatch | None:
        try:
            payload = self._get(f"/{kind}/{tmdb_id}")
        except TmdbError:
            return None
        title = payload.get("name") or payload.get("title") or ""
        date = payload.get("first_air_date") or payload.get("release_date") or ""
        return TmdbMatch(
            tmdb_id=tmdb_id,
            media_type=kind,
            season=1,
            title=title,
            year=_year_of(date),
            score=9.9,
            query=str(tmdb_id),
        )


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


def _year_of(date: str | None) -> int | None:
    if not date:
        return None
    match = re.match(r"(\d{4})", str(date))
    return int(match.group(1)) if match else None


def _score(result: dict[str, Any], base: str, year: int | None, kind: str) -> tuple[float, str, int | None] | None:
    """Plausibility score for one TMDB search result."""
    title = result.get("name") or result.get("title") or ""
    if not title:
        return None
    result_year = _year_of(result.get("first_air_date") or result.get("release_date"))
    want = normalise(base)
    got = normalise(title)

    if got == want:
        score = 3.0
    elif got.startswith(want) or want.startswith(got):
        score = 2.2
    elif normalise(base.split(":")[0]) and normalise(base.split(":")[0]) in got:
        score = 1.6
    else:
        return None

    score += min(float(result.get("popularity") or 0) / 500.0, 1.0)
    if year and result_year:
        delta = abs(year - result_year)
        if delta == 0:
            score += 0.6
        elif delta == 1:
            score += 0.3
        elif delta > 3:
            score -= 0.7
    if kind == "movie" and (result.get("name") or ""):
        score -= 0.2
    return score, title, result_year
