"""Aligning Nadeshiko timestamps to *your* copy of an episode.

The problem
-----------
Nadeshiko indexes dialogue against an **external source video**
(`Segment.externalVideoId`, a YouTube id).  `startTimeMs` / `endTimeMs` are
offsets into *that* video.

Your library's copy of the same episode is a different release: different
encode, frequently a different cold open, sometimes a TV cut vs a Blu-ray cut,
different frame rate, subtitles burned in or not.  So `ffmpeg -ss startTimeMs`
against your file lands somewhere near the scene, or nowhere at all.

The fix
-------
Every segment ships with `urls.audioUrl` — a ground-truth audio clip of exactly
that line, taken from the indexed source.  That makes the mapping *measurable*
instead of assumed:

1. Decode your episode to a log-compressed RMS envelope (cheap, codec-robust).
2. Decode the reference clip the same way.
3. Sliding normalised cross-correlation (FFT) locates the clip inside the
   episode -> the true local start time.
4. Repeat for several segments spread across the episode.  One anchor gives a
   constant offset; two or more give an affine map `local = a*src + b`, so
   frame-rate drift and inserted/removed footage show up as a slope instead of
   silently desyncing the tail of the reel.

Correlation is done on envelopes rather than raw samples on purpose: it
survives different bitrates, channel layouts, EQ and volume.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

from . import ffmpeg
from .config import get_settings
from .models import Segment

# Envelope parameters.  5 ms hop resolves to 1/200 s, far below the >=200 ms
# scale of a dialogue line, and keeps a 24-minute episode at ~288k samples.
SAMPLE_RATE = 16000
HOP_MS = 5.0
HOP_S = HOP_MS / 1000.0

MIN_SCORE = 0.30          # NCC below this is not a match
GOOD_SCORE = 0.55         # NCC above this is a confident match
MIN_MARGIN = 0.04         # peak must beat the best sidelobe by this much
GUARD_MS = 600            # ...ignoring anything within this distance of the peak
MAX_RESIDUAL_MS = 250.0   # anchors must agree with the fitted line this well
MIN_CLIP_MS = 500         # ignore clips too short to correlate reliably
MIN_SPAN_FOR_SLOPE_MS = 60_000   # below this, anchors cannot describe drift


@dataclass(slots=True)
class Anchor:
    """One measured mapping: Nadeshiko time -> time in the local file."""

    segment_id: str
    src_ms: int          # Segment.startTimeMs
    found_ms: int        # where that audio actually is in our file
    score: float         # normalised cross-correlation peak
    clip_ms: int         # length of the reference clip
    margin: float = 0.0  # peak minus best sidelobe
    expected_ms: int | None = None   # what the current map predicted

    @property
    def residual_ms(self) -> float:
        return float(self.found_ms - self.expected_ms) if self.expected_ms is not None else 0.0


@dataclass(slots=True)
class TimelineMap:
    """Affine time mapping: `local_ms = a * src_ms + b`."""

    a: float = 1.0
    b: float = 0.0
    anchors: list[Anchor] = field(default_factory=list)
    ok: bool = False
    reason: str = ""
    episode_duration_ms: int = 0

    def to_local_ms(self, src_ms: float) -> int:
        return int(round(self.a * float(src_ms) + self.b))

    def span_ms(self, src_start_ms: float, src_end_ms: float) -> tuple[int, int]:
        return self.to_local_ms(src_start_ms), self.to_local_ms(src_end_ms)

    @property
    def offset_ms(self) -> int:
        """Effective constant offset (meaningful when a == 1)."""
        return int(round(self.b))

    @property
    def scale_error(self) -> float:
        return abs(self.a - 1.0)

    @property
    def max_residual_ms(self) -> float:
        return max((abs(a.residual_ms) for a in self.anchors), default=0.0)

    @property
    def confidence(self) -> float:
        if not self.anchors:
            return 0.0
        scores = sorted(a.score for a in self.anchors)
        median = scores[len(scores) // 2]
        penalty = 1.0 if self.max_residual_ms <= MAX_RESIDUAL_MS else 0.5
        return float(max(0.0, min(1.0, median * penalty)))

    def describe(self) -> str:
        if not self.ok:
            return f"unaligned ({self.reason})"
        if self.scale_error < 5e-4:
            return f"offset {self.offset_ms:+d} ms, {len(self.anchors)} anchor(s), conf {self.confidence:.2f}"
        return (
            f"offset {self.offset_ms:+d} ms, rate x{self.a:.6f} "
            f"({(self.a - 1) * 100:+.3f}%), {len(self.anchors)} anchor(s), conf {self.confidence:.2f}"
        )

    def to_dict(self) -> dict:
        return {
            "a": self.a,
            "b": self.b,
            "ok": self.ok,
            "reason": self.reason,
            "offset_ms": self.offset_ms,
            "confidence": self.confidence,
            "max_residual_ms": self.max_residual_ms,
            "anchors": [asdict(a) for a in self.anchors],
        }


# ------------------------------------------------------------------ correlation


def sliding_ncc(haystack: np.ndarray, needle: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of `needle` over every offset of `haystack`.

    Result[i] is the NCC of `needle` against `haystack[i:i+len(needle)]`, in
    [-1, 1].  FFT-based, so a whole-episode search costs milliseconds.
    """
    n = needle.size
    if n == 0 or haystack.size < n:
        return np.zeros(0, dtype=np.float64)

    x = haystack.astype(np.float64)
    y = needle.astype(np.float64)
    x = x - x.mean()
    y = y - y.mean()

    numerator = fftconvolve(x, y[::-1], mode="valid")

    # Sliding energy of x via cumulative sum, so no second FFT is needed.
    cumulative = np.concatenate(([0.0], np.cumsum(x * x)))
    energy_x = cumulative[n:] - cumulative[:-n]
    energy_y = float(np.dot(y, y))

    denominator = np.sqrt(np.maximum(energy_x, 1e-12) * max(energy_y, 1e-12))
    return numerator / denominator


@dataclass(slots=True)
class Match:
    offset_ms: int
    score: float
    searched_from_ms: int
    margin: float = 0.0       # peak minus the best sidelobe
    judged: bool = False      # was there enough context to judge uniqueness?
    ambiguous: bool = False   # another position scores nearly as well

    @property
    def trustworthy(self) -> bool:
        """A strong peak that is also unique.

        Score alone is not enough: a line that occurs twice correlates equally
        well at both places.  Only the margin against the best sidelobe tells
        those apart, and only a search wide enough to contain a sidelobe can
        measure it.
        """
        return self.score >= MIN_SCORE and not self.ambiguous


def locate(
    haystack: np.ndarray,
    needle: np.ndarray,
    *,
    search_start_ms: int = 0,
    margin_ms: int = 0,
) -> Match:
    """Find `needle` inside `haystack`, optionally near a predicted position.

    `margin_ms` restricts the search to a window around `search_start_ms`; that
    is how a first anchor's offset constrains every later one, which is what
    stops a repeated catchphrase from being picked up in the wrong scene.
    """
    if needle.size == 0 or haystack.size < needle.size:
        return Match(offset_ms=0, score=0.0, searched_from_ms=0)

    lo = 0
    hi = haystack.size - needle.size
    if margin_ms > 0:
        center = int(round(max(0, search_start_ms) / HOP_MS))
        lo = max(0, center - int(round(margin_ms / HOP_MS)))
        hi = min(hi, center + int(round(margin_ms / HOP_MS)))
    if hi < lo:
        return Match(offset_ms=0, score=0.0, searched_from_ms=0)

    window = haystack[lo : hi + needle.size]
    ncc = sliding_ncc(window, needle)
    if ncc.size == 0:
        return Match(offset_ms=0, score=0.0, searched_from_ms=0)

    best = int(np.argmax(ncc))
    score = float(ncc[best])

    # How far the winner stands above everything that is not its own shoulder.
    guard = max(1, int(round(GUARD_MS / HOP_MS)))
    outside = np.concatenate(
        (ncc[: max(0, best - guard)], ncc[min(ncc.size, best + guard + 1) :])
    )

    if outside.size >= guard:
        margin = score - float(outside.max())
        ambiguous = margin < MIN_MARGIN
        judged = True
    else:
        # A window barely wider than the clip has no room for a rival peak, so
        # uniqueness simply cannot be measured here; the narrow search the
        # caller asked for is itself the evidence.
        margin = 0.0
        ambiguous = False
        judged = False

    return Match(
        offset_ms=int(round((lo + best) * HOP_MS)),
        score=score,
        searched_from_ms=int(round(lo * HOP_MS)),
        margin=margin,
        judged=judged,
        ambiguous=ambiguous,
    )


# ------------------------------------------------------------------- envelope io


def envelope_of(
    src: str | Path,
    *,
    headers: dict[str, str] | None = None,
    audio_track: int = 0,
) -> np.ndarray:
    return ffmpeg.envelope(
        src, sample_rate=SAMPLE_RATE, hop_ms=HOP_MS, headers=headers, audio_track=audio_track
    )


def cached_envelope(
    src: str | Path,
    *,
    headers: dict[str, str] | None = None,
    audio_track: int = 0,
    key_extra: str = "",
) -> np.ndarray:
    """Episode envelope, cached as .npy keyed by identity + size + mtime.

    Decoding a 24-minute episode to PCM is the single most expensive step of an
    alignment, and it does not change between runs.
    """
    settings = get_settings()
    path = Path(src) if not ffmpeg.is_remote(str(src)) else None
    if path is not None and path.is_file():
        stat = path.stat()
        stamp = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|{audio_track}|{key_extra}"
    else:
        stamp = f"{src}|{audio_track}|{key_extra}"

    digest = hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:32]
    cache_file = settings.cache_dir / "envelopes" / f"{digest}.npy"
    if cache_file.is_file():
        try:
            return np.load(cache_file)
        except (OSError, ValueError):
            pass

    env = envelope_of(src, headers=headers, audio_track=audio_track)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache_file, env)
    return env


# ----------------------------------------------------------------------- fitting


def _fit(anchors: list[Anchor]) -> tuple[float, float]:
    """Least-squares affine fit src -> found.  Falls back to constant offset."""
    believable = [a for a in anchors if a.score >= MIN_SCORE and a.clip_ms >= MIN_CLIP_MS]
    if not believable:
        return 1.0, 0.0
    if len(believable) == 1:
        only = believable[0]
        return 1.0, float(only.found_ms - only.src_ms)

    src = np.array([a.src_ms for a in believable], dtype=np.float64)
    found = np.array([a.found_ms for a in believable], dtype=np.float64)
    # Anchors drawn from a single scene span seconds, not minutes.  A slope
    # fitted over such a short window is noise, and acting on it would scale the
    # whole episode's timings from three nearby points -- so insist on a
    # constant offset until anchors are genuinely spread out.
    if float(np.ptp(src)) < MIN_SPAN_FOR_SLOPE_MS:
        return 1.0, float(np.median(found - src))

    slope, intercept = np.polyfit(src, found, 1)

    # Reject a wild slope (a false anchor would produce one) and fall back.
    if not np.isfinite(slope) or abs(slope - 1.0) > 0.05:
        residuals = found - src
        return 1.0, float(np.median(residuals))
    return float(slope), float(intercept)


def _drop_outliers(anchors: list[Anchor], *, tolerance_ms: float = MAX_RESIDUAL_MS) -> list[Anchor]:
    """Median-residual filter: one mismatched clip must not bend the timeline."""
    usable = [a for a in anchors if a.score >= MIN_SCORE]
    if len(usable) < 3:
        return usable
    residuals = np.array([a.found_ms - a.src_ms for a in usable], dtype=np.float64)
    median = float(np.median(residuals))
    mad = float(np.median(np.abs(residuals - median))) or 1.0
    keep = [a for a, r in zip(usable, residuals) if abs(r - median) <= max(tolerance_ms, 6 * mad)]
    return keep or usable


def solve_timeline(
    anchors: list[Anchor],
    *,
    episode_duration_ms: int = 0,
    require_anchors: int = 1,
) -> TimelineMap:
    """Turn measured anchors into a validated, affine time map."""
    if not anchors:
        return TimelineMap(ok=False, reason="no reference audio could be matched", episode_duration_ms=episode_duration_ms)

    kept = _drop_outliers(anchors)
    best = max((a.score for a in kept), default=0.0)
    if best < MIN_SCORE:
        return TimelineMap(
            ok=False,
            reason=f"best correlation {best:.2f} below threshold {MIN_SCORE}",
            anchors=kept,
            episode_duration_ms=episode_duration_ms,
        )
    if len(kept) < require_anchors:
        return TimelineMap(
            ok=False,
            reason=f"only {len(kept)} usable anchor(s), need {require_anchors}",
            anchors=kept,
            episode_duration_ms=episode_duration_ms,
        )

    a, b = _fit(kept)
    timeline = TimelineMap(a=a, b=b, anchors=kept, ok=True, episode_duration_ms=episode_duration_ms)

    for anchor in kept:
        anchor.expected_ms = timeline.to_local_ms(anchor.src_ms)

    if timeline.max_residual_ms > MAX_RESIDUAL_MS:
        timeline.ok = False
        timeline.reason = (
            f"anchors disagree by {timeline.max_residual_ms:.0f} ms "
            f"(> {MAX_RESIDUAL_MS:.0f} ms) — likely a different cut of the episode"
        )
    elif not timeline.anchors:
        timeline.ok = False
        timeline.reason = "no anchors survived filtering"
    else:
        timeline.reason = "ok"
    return timeline


# ------------------------------------------------------------------- orchestration


def align_episode(
    episode_src: str | Path,
    segments: list[Segment],
    *,
    clip_paths: dict[str, Path],
    headers: dict[str, str] | None = None,
    audio_track: int = 0,
    max_anchors: int = 6,
    episode_duration_ms: int = 0,
    min_anchors: int = 1,
    verbose: bool = False,
) -> TimelineMap:
    """Measure the mapping from Nadeshiko time to `episode_src` time.

    `clip_paths` maps `Segment.publicId` -> already-downloaded `urls.audioUrl`.
    Anchors are spread across the episode so a slope fit is meaningful, and the
    first (whole-file) search is reused as a prediction window for the rest.
    """
    episode_env = cached_envelope(
        episode_src, headers=headers, audio_track=audio_track
    )
    if episode_env.size == 0:
        return TimelineMap(ok=False, reason="could not decode audio from episode source")

    episode_ms = episode_duration_ms or int(episode_env.size * HOP_MS)

    candidates = [s for s in segments if s.publicId in clip_paths and s.duration_ms >= MIN_CLIP_MS]
    candidates.sort(key=lambda s: s.startTimeMs)
    if not candidates:
        return TimelineMap(
            ok=False,
            reason="no usable reference clips (need urls.audioUrl >= " f"{MIN_CLIP_MS} ms)",
            episode_duration_ms=episode_ms,
        )

    # Spread anchors over the episode rather than taking the first N.
    if len(candidates) > max_anchors:
        step = len(candidates) / float(max_anchors)
        picked = [candidates[min(len(candidates) - 1, int(i * step))] for i in range(max_anchors)]
    else:
        picked = candidates

    anchors: list[Anchor] = []
    prediction: TimelineMap | None = None

    for index, segment in enumerate(picked):
        clip = clip_paths[segment.publicId]
        try:
            clip_env = envelope_of(clip)
        except ffmpeg.FFmpegError:
            continue
        if clip_env.size == 0:
            continue

        clip_ms = int(round(clip_env.size * HOP_MS))
        # Sanity: the clip should be about as long as the segment claims.
        if clip_ms < MIN_CLIP_MS:
            continue

        restricted = prediction is not None and prediction.ok
        if restricted:
            # The first follow-up anchor gets a wide leash (a constant offset is
            # provisional); later ones are checked tightly against the fitted line.
            leash = 90_000 if len(anchors) < 2 else 45_000
            match = locate(
                episode_env,
                clip_env,
                search_start_ms=prediction.to_local_ms(segment.startTimeMs),
                margin_ms=leash,
            )
        else:
            match = locate(episode_env, clip_env)

        # Without a prediction, an unclear winner is not evidence; with one, the
        # window has already done the disambiguating.
        accepted = match.score >= MIN_SCORE and (not match.ambiguous or restricted)
        anchor = Anchor(
            segment_id=segment.publicId,
            src_ms=segment.startTimeMs,
            found_ms=match.offset_ms,
            score=match.score,
            clip_ms=clip_ms,
            margin=match.margin,
        )
        if verbose:
            verdict = "accept" if accepted else "reject"
            quality = "ambiguous" if match.ambiguous else ("clear" if match.judged else "unjudged")
            print(
                f"    anchor {index + 1}/{len(picked)} {segment.publicId}: "
                f"src {segment.startTimeMs} ms -> local {match.offset_ms} ms "
                f"[{verdict}] score {match.score:.2f} margin {match.margin:+.2f} ({quality}) "
                f"clip {clip_ms} ms drift {match.offset_ms - segment.startTimeMs:+d} ms"
                + ("  (windowed)" if restricted else "")
            )
        if accepted:
            anchors.append(anchor)
            # Re-fit after every accepted anchor so the next window is tighter,
            # including after the very first one (that is what pins the offset).
            prediction = solve_timeline(anchors, episode_duration_ms=episode_ms)

    return solve_timeline(anchors, episode_duration_ms=episode_ms, require_anchors=min_anchors)


def verify_segment(
    episode_env: np.ndarray,
    clip_env: np.ndarray,
    *,
    predicted_ms: int,
    margin_ms: int = 5000,
) -> Match:
    """Re-check one segment's predicted position (used by `reel align --verify`)."""
    return locate(episode_env, clip_env, search_start_ms=predicted_ms, margin_ms=margin_ms)


def save_timeline(path: Path, timeline: TimelineMap, *, meta: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = timeline.to_dict()
    if meta:
        payload["meta"] = meta
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
