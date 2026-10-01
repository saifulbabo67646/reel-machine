"""Stock background clips from Pexels.

A deployment chooses whether it has a stock source at all: without `PEXELS_API_KEY` the
provider reports exactly that and the recipe keeps its procedural gradient. Every clip
carries the Pexels licence and the photographer's attribution into the manifest.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
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

# Pexels describes its own media in the page slug ("…/video/woman-in-red-dress-123/") and,
# for photos, in `alt`. A reel behind a verse should not put an unknown person on screen
# — least of all a woman in western dress — so candidates that read like people are
# skipped before anything is downloaded. Exact enough to be useful, deliberately broad:
# a missed shot costs a retry, an unwanted person costs the video's credibility.
PEOPLE_WORDS = (
    "woman", "women", "female", "girl", "girls", "lady", "ladies", "mother", "mom", "mum",
    "daughter", "sister", "wife", "bride", "man", "men", "male", "guy", "guys", "boy",
    "boys", "father", "dad", "son", "brother", "husband", "groom", "kid", "kids", "child",
    "children", "baby", "toddler", "person", "people", "human", "humans", "face", "faces",
    "portrait", "selfie", "model", "couple", "wedding", "dancer", "dancing", "yoga",
    "fitness", "athlete", "runner", "jogger", "posing", "poses", "smile", "smiling",
    "laugh", "laughing", "fashion", "dress", "gown", "swimsuit", "bikini", "shirtless",
    "hands", "arms", "silhouette", "crowd", "audience", "worker", "chef", "doctor",
    "businessman", "businesswoman", "student", "teacher", "hijab", "muslimah",
)
_PEOPLE_PATTERN = re.compile(r"\b(" + "|".join(PEOPLE_WORDS) + r")\b", re.IGNORECASE)


def people_word(entry: dict[str, Any]) -> str:
    """The word that makes this candidate look like a person, or "" when it is clean."""
    text = f"{entry.get('alt') or ''} {entry.get('url') or ''}"
    match = _PEOPLE_PATTERN.search(text)
    return match.group(1).lower() if match else ""


def rank_video_files(
    payload: dict[str, Any],
    *,
    orientation: str = "portrait",
    min_height: int = 720,
    allow_people: bool = False,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every usable `(video, file)` best-first: right shape, enough resolution, mp4.

    Pexels returns every rendition it has; choosing one is policy, so it lives here and
    is unit-tested rather than being buried in the request. A ranked list because a
    candidate can still be refused later — by the face check — and the next one tried.
    """
    ranked: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for video in payload.get("videos") or []:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        if orientation == "portrait" and height <= width:
            continue
        if orientation == "landscape" and width <= height:
            continue
        if not allow_people and people_word(video):
            continue
        best_file: tuple[float, dict[str, Any]] | None = None
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
            score = abs(target - 1080) + abs(file_height - (1920 if orientation == "portrait" else 1080)) / 20
            if best_file is None or score < best_file[0]:
                best_file = (score, entry)
        if best_file is not None:
            ranked.append((best_file[0], video, best_file[1]))
    ranked.sort(key=lambda item: item[0])
    return [(video, entry) for _, video, entry in ranked]


def pick_video_file(
    payload: dict[str, Any],
    *,
    orientation: str = "portrait",
    min_height: int = 720,
    allow_people: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    ranked = rank_video_files(
        payload, orientation=orientation, min_height=min_height, allow_people=allow_people
    )
    return ranked[0] if ranked else None


def rank_photos(
    payload: dict[str, Any],
    *,
    orientation: str = "portrait",
    min_height: int = 1080,
    allow_people: bool = False,
) -> list[dict[str, Any]]:
    """Every usable still, best-first: right shape, enough resolution."""
    ranked: list[tuple[float, dict[str, Any]]] = []
    for photo in payload.get("photos") or []:
        width = int(photo.get("width") or 0)
        height = int(photo.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        if orientation == "portrait" and height <= width:
            continue
        if orientation == "landscape" and width <= height:
            continue
        if height < min_height:
            continue
        if not allow_people and people_word(photo):
            continue
        ranked.append((abs(height - 1920) + abs(width - 1080) / 4, photo))
    ranked.sort(key=lambda item: item[0])
    return [photo for _, photo in ranked]


def pick_photo(
    payload: dict[str, Any],
    *,
    orientation: str = "portrait",
    min_height: int = 1080,
    allow_people: bool = False,
) -> dict[str, Any] | None:
    ranked = rank_photos(
        payload, orientation=orientation, min_height=min_height, allow_people=allow_people
    )
    return ranked[0] if ranked else None


def sample_frames(path: Path, cv2: Any) -> list[Any]:
    """A few frames spread through the shot: enough to catch a face, cheap to run."""
    frames: list[Any] = []
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        frame = cv2.imread(str(path))
        return [frame] if frame is not None else []
    capture = cv2.VideoCapture(str(path))
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        for fraction in (0.05, 0.3, 0.6, 0.9):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(total * fraction))
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(frame)
    finally:
        capture.release()
    return frames


def faces_present(path: Path, *, model: str | Path | None = None) -> bool | None:
    """Whether a face is visible in this media, or None when the check cannot run.

    The description filter catches what Pexels *says* about a shot; this catches what the
    shot *shows* — a person in a query that never mentioned one. It needs a face model
    (`REEL_FACE_MODEL`, a YuNet ONNX file: OpenCV 5 no longer ships the old cascades), so
    a deployment without one still renders, on the description filter alone — and the
    manifest records which check actually ran.
    """
    model_path = Path(model or os.environ.get("REEL_FACE_MODEL", "")).expanduser()
    if not str(model_path) or not model_path.is_file():
        return None
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    detector_cls = getattr(cv2, "FaceDetectorYN", None)
    if detector_cls is None:  # pragma: no cover - depends on the OpenCV build
        return None
    try:
        frames = sample_frames(path, cv2)
        if not frames:
            return None  # could not decode: not evidence, and not a claim
        size = (320, 320)
        detector = detector_cls.create(str(model_path), "", size, 0.7, 0.3, 5000)
        for frame in frames:
            detector.setInputSize(size)
            resized = cv2.resize(frame, size)
            _, found = detector.detect(resized)
            if found is not None and len(found) > 0:
                return True
        return False
    except Exception:  # noqa: BLE001 - a broken detector must never fail a render
        return None


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
        face_detector: Callable[[Path], bool | None] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = (base_url or API_BASE).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("PEXELS_API_KEY", "")
        self.cache_dir = (
            Path(cache_dir) if cache_dir is not None else self.settings.cache_dir / "pexels"
        )
        self._client = client
        self._face_detector = face_detector or faces_present

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

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Every Pexels failure becomes an actionable error, never a bare HTTPError."""
        try:
            response = self._http().get(url, headers={"Authorization": self.api_key}, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            hint = {
                401: "PEXELS_API_KEY was rejected — check the key",
                403: "PEXELS_API_KEY is not allowed to do that",
                429: "Pexels rate limit reached — retry shortly",
            }.get(status, "retry; if it persists, check the query and the account")
            raise ProviderUnavailable(
                f"Pexels answered {status} for this search",
                hint=hint,
                details={"url": exc.request.url.path, "provider": self.name},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                "could not reach Pexels",
                hint="check the network, then retry",
                details={"error": type(exc).__name__, "provider": self.name},
            ) from exc
        return response

    def search(self, query: str, *, orientation: str = "portrait", media: str = "video") -> dict[str, Any]:
        # Pexels is asymmetric: videos live at /videos/search, photos at /v1/search
        endpoint = "videos/search" if media == "video" else "v1/search"
        params = {"query": query, "orientation": orientation, "per_page": PER_PAGE}
        if media == "video":
            params["size"] = "medium"
        return self._get(f"{self.base_url}/{endpoint}", params=params).json()

    def _download(self, url: str, dest: Path) -> None:
        try:
            with self._http().stream("GET", url) as response:
                response.raise_for_status()
                with dest.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            dest.unlink(missing_ok=True)  # never leave half a file in the cache
            raise ProviderUnavailable(
                "the Pexels file could not be downloaded",
                hint="retry; if it persists, widen the query",
                details={"error": type(exc).__name__, "provider": self.name},
            ) from exc

    def _accept(
        self, clip: BackgroundClip, request: BackgroundRequest, blocked: list[tuple[str, str]]
    ) -> BackgroundClip | None:
        """The last gate: what the shot actually shows, not what its slug says."""
        if request.allow_people:
            clip.face_check = "skipped"
            return clip
        verdict = self._face_detector(clip.path)
        clip.face_check = {False: "passed", None: "unavailable"}.get(verdict, "blocked")
        if verdict is True:
            blocked.append(("a face", str(clip.provenance.source)))
            return None
        return clip

    def _photo_clip(self, photo: dict[str, Any], request: BackgroundRequest) -> BackgroundClip:
        source = (photo.get("src") or {}).get("large2x") or (photo.get("src") or {}).get("original")
        if not source:
            raise ProviderUnavailable(
                "the Pexels photo carried no downloadable file",
                hint="retry, or ask for media=video",
                details={"photo": photo.get("id")},
            )
        photographer = str(photo.get("photographer") or "")
        page_url = str(photo.get("url") or "")
        suffix = Path(str(source).split("?", 1)[0]).suffix or ".jpg"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached = self.cache_dir / f"pexels-photo-{photo.get('id')}{suffix}"
        if not cached.is_file():
            self._download(str(source), cached)
        return BackgroundClip(
            path=cached,
            provenance=Provenance(
                provider=self.name,
                provider_version=self.version,
                source=page_url or self.base_url,
                source_id=str(photo.get("id") or ""),
                upstream="https://www.pexels.com",
                notes=f"Photo by {photographer} on Pexels" if photographer else "Pexels",
            ),
            licence=Licence(
                name="Pexels licence",
                url=LICENCE_URL,
                attribution=f"Photo by {photographer} on Pexels" if photographer else "Pexels",
            ),
            width=int(photo.get("width") or 0),
            height=int(photo.get("height") or 0),
            still=True,
        )

    def _no_match(
        self,
        noun: str,
        request: BackgroundRequest,
        candidates: list[dict[str, Any]],
        blocked: list[tuple[str, str]],
    ) -> None:
        """Say which kind of nothing the caller got: an empty search, or a screened one."""
        described = [(people_word(entry), str(entry.get("url"))) for entry in candidates if people_word(entry)]
        faced = [(reason, url) for reason, url in blocked if reason == "a face"]
        failed = [(reason, url) for reason, url in blocked if reason == "a failed download"]
        screened = described + faced
        if screened and not request.allow_people:
            raise InvalidInput(
                f"every Pexels {noun} for {request.query!r} showed a person",
                hint=(
                    "rephrase toward places, light, water, sky or objects; "
                    "set allow_people=true only when the shot is known to be appropriate"
                ),
                details={
                    "query": request.query,
                    "skipped": [{"reason": word, "url": url} for word, url in screened[:3]],
                    "skipped_count": len(screened),
                },
            )
        if failed:
            raise ProviderUnavailable(
                f"every Pexels {noun} for {request.query!r} failed to download",
                hint="retry; the stock CDN dropped the transfer",
                details={"query": request.query, "attempts": len(failed)},
            )
        raise InvalidInput(
            f"no Pexels {noun} matched {request.query!r}",
            hint="try a broader query, or use background kind=gradient",
            details={"orientation": request.orientation},
        )

    def fetch(self, request: BackgroundRequest, *, dest_dir: Path) -> BackgroundClip:
        if not self.api_key:
            raise ProviderUnavailable(
                "stock backgrounds are not configured on this deployment",
                hint="set PEXELS_API_KEY, or use background kind=gradient (the default)",
                details={"provider": self.name},
            )
        if not request.query.strip():
            raise InvalidInput("a Pexels background needs a search query")
        blocked: list[tuple[str, str]] = []  # candidates the face check refused
        if request.media in ("video", "auto"):
            payload = self.search(request.query, orientation=request.orientation, media="video")
            for video, file in rank_video_files(
                payload,
                orientation=request.orientation,
                min_height=request.min_height,
                allow_people=request.allow_people,
            ):
                try:
                    clip = self._video_clip(video, file)
                except ProviderUnavailable:  # a dropped transfer is not the end of the search
                    blocked.append(("a failed download", str(video.get("url") or "")))
                    continue
                accepted = self._accept(clip, request, blocked)
                if accepted is not None:
                    return accepted
            if request.media == "video":
                self._no_match("clip", request, payload.get("videos") or [], blocked)
        payload = self.search(request.query, orientation=request.orientation, media="photo")
        for photo in rank_photos(
            payload,
            orientation=request.orientation,
            min_height=request.min_height,
            allow_people=request.allow_people,
        ):
            try:
                clip = self._photo_clip(photo, request)
            except ProviderUnavailable:
                blocked.append(("a failed download", str(photo.get("url") or "")))
                continue
            accepted = self._accept(clip, request, blocked)
            if accepted is not None:
                return accepted
        self._no_match("photo", request, payload.get("photos") or [], blocked)

    def _video_clip(self, video: dict[str, Any], file: dict[str, Any]) -> BackgroundClip:
        link = str(file["link"])
        photographer = str((video.get("user") or {}).get("name") or "")
        page_url = str(video.get("url") or "")
        suffix = Path(link.split("?", 1)[0]).suffix or ".mp4"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached = self.cache_dir / f"pexels-{video.get('id')}{suffix}"
        if not cached.is_file():
            self._download(link, cached)
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
