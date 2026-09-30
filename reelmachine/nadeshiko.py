"""Nadeshiko API client: search, media, episodes, context.

Wraps the endpoints reel-machine needs and adds the three things a batch
automation cannot do without:

* a token-bucket limiter that respects the documented 150 req/min,
* a persisted monthly ledger so you can see the 5,000 req/month quota burn down,
* an on-disk response cache so re-running a build never re-spends quota.

Docs / key: https://nadeshiko.co/user/developer
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx

from .config import Settings, get_settings
from .models import Episode, Media, MediaSummary, SearchPage, Segment, WordMatch

DEFAULT_TTL_S = 30 * 24 * 3600  # search results for a fixed query do not move fast


class NadeshikoError(RuntimeError):
    def __init__(self, status: int, body: Any, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status} from {url}: {_short(body)}")


class QuotaExceeded(NadeshikoError):
    """Monthly quota (or short-term rate limit) exhausted."""


def _short(body: Any, limit: int = 300) -> str:
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return text[:limit] + ("..." if len(text) > limit else "")


@dataclass(slots=True)
class QuotaState:
    month: str
    requests: int
    limit: int = 5000

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.requests)


class RateLimiter:
    """Sliding-window limiter: at most `per_minute` requests in any 60s window."""

    def __init__(self, per_minute: int = 150):
        self.per_minute = per_minute
        self._stamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._stamps = [t for t in self._stamps if now - t < 60.0]
                if len(self._stamps) < self.per_minute:
                    self._stamps.append(now)
                    return
                sleep_for = 60.0 - (now - self._stamps[0]) + 0.01
            time.sleep(max(0.05, sleep_for))


class QuotaLedger:
    """Counts API calls per calendar month in a small JSON file."""

    def __init__(self, path: Path, limit: int = 5000):
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()

    @staticmethod
    def _month() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def state(self) -> QuotaState:
        data = self._read()
        month = self._month()
        return QuotaState(month=month, requests=int(data.get(month) or 0), limit=self.limit)

    def record(self, n: int = 1) -> QuotaState:
        with self._lock:
            data = self._read()
            month = self._month()
            data[month] = int(data.get(month) or 0) + n
            # keep the file tiny: only the last 6 months
            for key in sorted(data)[:-6]:
                data.pop(key, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return QuotaState(month=month, requests=int(data[month]), limit=self.limit)


class NadeshikoClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        settings: Settings | None = None,
        cache: bool = True,
        ttl_s: int = DEFAULT_TTL_S,
        per_minute: int = 150,
        monthly_limit: int = 5000,
        timeout: float = 30.0,
    ):
        self.settings = settings or get_settings()
        self.api_key = api_key or self.settings.api_key
        self.base_url = (base_url or self.settings.base_url).rstrip("/")
        self.cache_enabled = cache
        self.ttl_s = ttl_s
        self.limiter = RateLimiter(per_minute)
        self.ledger = QuotaLedger(self.settings.ledger_path, limit=monthly_limit)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "reel-machine/0.1",
            },
            follow_redirects=True,
        )

    # ------------------------------------------------------------------ plumbing
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "NadeshikoClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.settings.cache_dir / digest[:2] / f"{digest}.json"

    def _cache_get(self, key: str) -> Any | None:
        if not self.cache_enabled:
            return None
        path = self._cache_path(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if self.ttl_s and time.time() - float(payload.get("_at") or 0) > self.ttl_s:
            return None
        return payload.get("data")

    def _cache_put(self, key: str, data: Any) -> None:
        if not self.cache_enabled:
            return
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"_at": time.time(), "_key": key, "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        use_cache: bool = True,
        retries: int = 4,
    ) -> Any:
        if not self.api_key:
            raise NadeshikoError(
                401,
                "no API key configured; set NADESHIKO_API_KEY in .env",
                f"{self.base_url}{path}",
            )

        key = json.dumps(
            {"m": method, "p": path, "b": json_body or {}, "q": params or {}},
            sort_keys=True,
            ensure_ascii=False,
        )
        if use_cache:
            hit = self._cache_get(key)
            if hit is not None:
                return hit

        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(retries):
            self.limiter.acquire()
            self.ledger.record(1)
            try:
                response = self._client.request(method, path, json=json_body, params=params)
            except httpx.HTTPError as exc:  # network-level
                last_error = exc
                time.sleep(min(2**attempt, 8))
                continue

            if response.status_code == 200:
                data = response.json()
                if use_cache:
                    self._cache_put(key, data)
                return data

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if (retry_after or "").isdigit() else min(2**attempt, 30)
                body = _safe_json(response)
                text = json.dumps(body).lower() if body else response.text.lower()
                if "quota" in text or "month" in text:
                    raise QuotaExceeded(429, body, url)
                last_error = NadeshikoError(429, body, url)
                time.sleep(max(1.0, wait))
                continue

            if 500 <= response.status_code < 600:
                last_error = NadeshikoError(response.status_code, _safe_json(response), url)
                time.sleep(min(2**attempt, 10))
                continue

            raise NadeshikoError(response.status_code, _safe_json(response), url)

        if isinstance(last_error, NadeshikoError):
            raise last_error
        raise NadeshikoError(0, str(last_error or "request failed"), url)

    # ------------------------------------------------------------------- search
    def search(
        self,
        query: str | None = None,
        *,
        take: int = 25,
        cursor: str | None = None,
        exact_match: bool = False,
        sort_mode: str = "RELEVANCE",
        seed: int | None = None,
        filters: dict[str, Any] | None = None,
        content_rating: list[str] | None = None,
        include_media: bool = True,
        use_cache: bool = True,
    ) -> SearchPage:
        """POST /v1/search — segments with exact start/end times and translations."""
        body: dict[str, Any] = {"take": max(1, min(50, take))}
        if query:
            body["query"] = {"search": query, "exactMatch": exact_match}
        if cursor:
            body["cursor"] = cursor
        if sort_mode and sort_mode != "RELEVANCE":
            body["sort"] = {"mode": sort_mode}
            if seed is not None:
                body["sort"]["seed"] = seed
        merged = dict(filters or {})
        if content_rating:
            merged.setdefault("contentRating", content_rating)
        if merged:
            body["filters"] = merged
        if include_media:
            body["include"] = ["media"]
        payload = self._request("POST", "/v1/search", json_body=body, use_cache=use_cache)
        return SearchPage.from_api(payload or {})

    def iter_search(
        self,
        query: str | None = None,
        *,
        max_results: int = 50,
        pages: int = 5,
        **kwargs: Any,
    ) -> Iterator[Segment]:
        """Cursor-walk `/v1/search` up to `max_results` segments."""
        seen: set[str] = set()
        cursor: str | None = None
        produced = 0
        for _ in range(max(1, pages)):
            remaining = max(1, max_results - produced)
            page = self.search(query, take=min(50, remaining), cursor=cursor, **kwargs)
            if not page.segments:
                return
            for segment in page.segments:
                if segment.publicId in seen:
                    continue
                seen.add(segment.publicId)
                produced += 1
                yield segment
                if produced >= max_results:
                    return
            if not page.pagination.hasMore or not page.pagination.cursor:
                return
            cursor = page.pagination.cursor

    def search_words(self, words: list[str], *, exact_match: bool = False, filters: dict[str, Any] | None = None) -> list[WordMatch]:
        """POST /v1/search/words — per-word match counts across media (max 100 words).

        Cheap triage: find which words actually exist in the corpus before
        spending a search + render on them.
        """
        body: dict[str, Any] = {
            "query": {"words": words[:100], "exactMatch": exact_match},
            "include": ["media"],
        }
        if filters:
            body["filters"] = filters
        payload = self._request("POST", "/v1/search/words", json_body=body)
        return [WordMatch.model_validate(item) for item in (payload or {}).get("results") or []]

    def search_media(self, query: str, *, take: int = 10) -> list[MediaSummary]:
        """POST /v1/search/media — name -> mediaPublicId."""
        payload = self._request(
            "POST", "/v1/search/media", json_body={"query": query, "take": max(1, min(40, take))}
        )
        items = (payload or {}).get("media") or (payload or {}).get("results") or []
        return [MediaSummary.model_validate(item) for item in items]

    # -------------------------------------------------------------------- media
    def get_media(self, media_public_id: str) -> Media:
        payload = self._request("GET", f"/v1/media/{media_public_id}")
        return Media.model_validate((payload or {}).get("media") or payload)

    def list_media(self, *, take: int = 20, category: str | None = None, query: str | None = None, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"take": max(1, min(50, take))}
        if category:
            params["category"] = category
        if query:
            params["query"] = query
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v1/media", params=params) or {}

    def list_episodes(self, media_public_id: str, *, take: int = 50, cursor: str | None = None) -> list[Episode]:
        params: dict[str, Any] = {"take": max(1, min(50, take))}
        if cursor:
            params["cursor"] = cursor
        payload = self._request("GET", f"/v1/media/{media_public_id}/episodes", params=params)
        items = (payload or {}).get("episodes") or []
        return [Episode.model_validate(item) for item in items]

    def get_episode(self, media_public_id: str, episode_number: int) -> Episode:
        payload = self._request("GET", f"/v1/media/{media_public_id}/episodes/{episode_number}")
        return Episode.model_validate((payload or {}).get("episode") or payload)

    # ----------------------------------------------------------------- segments
    def get_segment(self, segment_public_id: str) -> Segment:
        payload = self._request("GET", f"/v1/media/segments/{segment_public_id}")
        return Segment.model_validate((payload or {}).get("segment") or payload)

    def segment_context(
        self,
        segment_public_id: str,
        *,
        take: int = 3,
        content_rating: list[str] | None = None,
        include_media: bool = False,
    ) -> list[Segment]:
        """GET /v1/media/segments/{id}/context — dialogue around a segment.

        Useful when a single line is too short or lands mid-conversation and you
        want the reel to breathe.
        """
        params: dict[str, Any] = {"take": max(1, min(30, take))}
        if content_rating:
            params["contentRating"] = content_rating
        if include_media:
            params["include"] = ["media"]
        payload = self._request("GET", f"/v1/media/segments/{segment_public_id}/context", params=params)
        return [Segment.model_validate(item) for item in (payload or {}).get("segments") or []]

    # -------------------------------------------------------------------- quota
    def quota(self) -> QuotaState:
        return self.ledger.state()

    # ----------------------------------------------------------------- download
    def download(self, url: str, dest: Path, *, refresh: bool = False) -> Path:
        """Fetch a segment asset (`urls.audioUrl` etc.) with on-disk caching.

        The reference audio clip is the ground truth used to align Nadeshiko's
        timestamps against your own copy of the episode.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file() and dest.stat().st_size > 0 and not refresh:
            return dest
        with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as response:
            response.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as fh:
                for chunk in response.iter_bytes(1 << 16):
                    fh.write(chunk)
            tmp.replace(dest)
        return dest


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]
