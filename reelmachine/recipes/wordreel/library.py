"""The `library` clip source: your own footage, searched by subtitle.

This is the answer to "where do the examples come from" that costs nothing, needs no
account and cannot be rate-limited: the media and the subtitles sitting on your disk.
A film you own is a film whose every line is already indexed — the subtitle track *is*
the index, and ffmpeg can cut the exact second the line is spoken.

Two passes, cheapest first. A **sidecar** subtitle (`Film.en.srt` beside `Film.mkv`)
is read directly: no ffmpeg, no probe, just text. Only when the sidecar pass produced
nothing does the **embedded** pass probe each video and extract its own text tracks —
that is the expensive one, so it is opt-in (`REEL_WORDREEL_EMBEDDED`) and skipped
entirely once a query already has enough answers.

The payoff for cutting from the same file the subtitle came out of is timing: an
embedded track is timed against *exactly* the footage being cut, so a clip lands on
the line with no alignment step and nothing to drift. A sidecar can be a different
release, which is why every candidate carries `subtitle_source` and the recipe reports
which pass it came from.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator

from ... import ffmpeg
from ...config import Settings, get_settings
from ...sources.base import VIDEO_SUFFIXES, find_video_files
from ..storyreel.subtitles import files as subtitle_files
from ..storyreel.transcript import parse_srt
from . import matching
from .clips import ClipCandidate, ClipRequest, describe_row

#: Subtitle file extensions a sidecar pass will pick up, best first.
SIDECAR_SUFFIXES = (".srt", ".ass", ".ssa", ".vtt")

#: `Film.en.srt`, `Film.en.forced.srt`, `Film.eng.srt` — the language tag is noise.
_LANGUAGE_TAG = re.compile(r"\.(?:[a-z]{2,3}(?:[-_][a-z0-9]+)?|forced|sdh|hi)$", re.IGNORECASE)

#: A word that shows up in hundreds of lines is a stopword in disguise (`get`, `go`),
#: and one that shows up once has nothing to teach from. Both are poor suggestions.
SUGGEST_MIN_HITS = 2
SUGGEST_MAX_HITS = 400
SUGGEST_MIN_LENGTH = 3


class LibraryClipSource:
    """Search a local library of media + subtitles for lines using a word."""

    name = "library"
    version = "1"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cues: dict[str, list[Any]] = {}
        self._tracks: dict[str, list[ffmpeg.SubtitleTrack]] = {}

    # ------------------------------------------------------------------ provider
    def missing(self) -> list[str]:
        """A library that has not been pointed anywhere is a misconfiguration, not a
        crash: the recipe's own `library` input can still supply directories."""
        if not self.settings.wordreel_library:
            return [
                "no library configured; set REEL_WORDREEL_LIBRARY to a media directory, "
                "or pass `library` in the request"
            ]
        return []

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "Lines cut from your own media, found by searching its subtitles",
            self.missing(),
            directories=list(self.settings.wordreel_library),
        )

    # --------------------------------------------------------------------- find
    def find(self, request: ClipRequest, *, limit: int = 20) -> list[ClipCandidate]:
        if not request.word.strip():
            raise ValueError("find() needs a word")
        matcher = matching.WordMatcher(request.word, mode=request.match_mode)
        found: list[ClipCandidate] = []
        seen: set[str] = set()

        # every sidecar is free — read them all before deciding anything
        for media_path, subtitle_path, origin in self._cheap_passes(request):
            self._collect(matcher, found, seen, media_path, subtitle_path, origin, request)

        # the embedded pass probes and shells out to ffmpeg per file, so it only runs
        # when the cheap pass did not already answer the query
        if len(found) < limit:
            for media_path, subtitle_path, origin in self._embedded_passes(request):
                self._collect(matcher, found, seen, media_path, subtitle_path, origin, request)

        return _rank(found, request=request, limit=limit)

    def _collect(
        self,
        matcher: matching.WordMatcher,
        found: list[ClipCandidate],
        seen: set[str],
        media_path: Path | None,
        subtitle_path: Path,
        origin: str,
        request: ClipRequest,
    ) -> None:
        for candidate in self._candidates(
            matcher, media_path, subtitle_path, origin, request
        ):
            key = matching.normalise(candidate.text)
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)

    # ------------------------------------------------------------------ suggest
    def suggest(self, request: ClipRequest, *, limit: int = 20) -> list[str]:
        """Words this library can actually teach, ordered by how well it can teach them."""
        counts: Counter[str] = Counter()
        where: dict[str, set[str]] = {}
        for media_path, subtitle_path, _origin in self._passes(request):
            for cue in self._cues_for(subtitle_path):
                key = str(media_path or subtitle_path)
                for word in matching.content_words(cue.text):
                    if len(word) < SUGGEST_MIN_LENGTH:
                        continue
                    counts[word] += 1
                    where.setdefault(word, set()).add(key)

        ranked = [
            word
            for word, hits in counts.items()
            if SUGGEST_MIN_HITS <= hits <= SUGGEST_MAX_HITS
        ]
        # a word spread across several films gives a reel variety; a word confined to
        # one film still works, it just makes a monotonous reel
        ranked.sort(key=lambda word: (-len(where[word]), -counts[word], word))
        return ranked[: max(1, limit)]

    # ------------------------------------------------------------------ internals
    def _cheap_passes(self, request: ClipRequest) -> Iterator[tuple[Path | None, Path, str]]:
        """Caller-supplied subtitles and sidecar files — no ffmpeg, no probe."""
        directories = _directories(request, self.settings)
        for raw in request.subtitles:
            path = Path(raw).expanduser()
            if path.is_file():
                yield _media_beside(path, directories), path, "caller"
        if not directories:
            return
        for path in _media_files(directories, request, self.settings):
            sidecar = _sidecar_for(path)
            if sidecar is not None:
                yield path, sidecar, "sidecar"

    def _embedded_passes(self, request: ClipRequest) -> Iterator[tuple[Path | None, Path, str]]:
        """Text tracks inside the video, for media that has no sidecar.

        `media` is None for a subtitle with no footage behind it: the line is still a
        real example and belongs in a scan, it just cannot be cut.
        """
        if not self.settings.wordreel_embedded:
            return
        directories = _directories(request, self.settings)
        if not directories:
            return
        for path in _media_files(directories, request, self.settings):
            if _sidecar_for(path) is not None:
                continue  # already answered by the cheap pass
            for track in self._text_tracks(path, request.languages):
                extracted = self._extract(path, track)
                if extracted is not None:
                    yield path, extracted, "embedded"

    def _passes(self, request: ClipRequest) -> Iterator[tuple[Path | None, Path, str]]:
        """Everything, cheap and expensive — what `suggest` reads."""
        yield from self._cheap_passes(request)
        yield from self._embedded_passes(request)

    def _candidates(
        self,
        matcher: matching.WordMatcher,
        media_path: Path | None,
        subtitle_path: Path,
        origin: str,
        request: ClipRequest,
    ) -> Iterator[ClipCandidate]:
        label = _label(media_path or subtitle_path)
        roll_in = self.settings.pre_roll_ms
        roll_out = self.settings.post_roll_ms
        for cue in self._cues_for(subtitle_path):
            hit = matching.best_match(cue.text, matcher)
            if hit is None:
                continue
            if not request.allow_inflected and not hit.exact:
                continue
            scored = matching.score_example(
                cue.text,
                hit,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                min_clip_ms=request.min_clip_ms,
                max_clip_ms=request.max_clip_ms,
            )
            yield ClipCandidate(
                text=cue.text,
                media_path=str(media_path) if media_path else "",
                media_label=label,
                subtitle_source=f"{origin}:{subtitle_path.name}",
                # the roll is what makes a cut line start just before it is spoken and
                # hold for a beat after; clamping to zero keeps a cue at 00:00:00 valid
                start_ms=max(0, cue.start_ms - roll_in),
                end_ms=cue.end_ms + roll_out,
                match_start=hit.start,
                match_end=hit.end,
                match_surface=hit.surface,
                exact=hit.exact,
                score=scored.score,
                reasons=list(scored.reasons),
            )

    def _cues_for(self, subtitle_path: Path) -> list[Any]:
        key = str(subtitle_path)
        cached = self._cues.get(key)
        if cached is not None:
            return cached
        cues: list[Any] = []
        collected = subtitle_files.to_srt(subtitle_path, self.settings.cache_dir / "wordreel")
        if collected is not None:
            text = subtitle_files.read_text(collected)
            if text:
                cues = parse_srt(text)
        self._cues[key] = cues
        return cues

    def _text_tracks(self, path: Path, languages: list[str]) -> list[ffmpeg.SubtitleTrack]:
        key = str(path)
        cached = self._tracks.get(key)
        if cached is None:
            try:
                info = ffmpeg.probe(path)
            except Exception:  # noqa: BLE001 - an unreadable file is simply not a source
                self._tracks[key] = []
                return []
            cached = info.text_subtitles()
            self._tracks[key] = cached
        if not cached:
            return []
        wanted = {_short(language) for language in languages if language}
        if not wanted:
            return cached
        preferred = [track for track in cached if _short(track.language) in wanted]
        return preferred or cached

    def _extract(self, path: Path, track: ffmpeg.SubtitleTrack) -> Path | None:
        dest = (
            self.settings.cache_dir
            / "wordreel"
            / "embedded"
            / f"{path.stem}.{track.ordinal}.srt"
        )
        if dest.is_file() and dest.stat().st_size:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            ffmpeg.run(
                [
                    self.settings.ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(path),
                    "-map",
                    f"0:s:{track.ordinal}",
                    "-c:s",
                    "srt",
                    str(dest),
                ]
            )
        except Exception:  # noqa: BLE001 - a failed extraction is one lost source
            return None
        return dest if dest.is_file() and dest.stat().st_size else None


# ------------------------------------------------------------------------ helpers


def _short(language: str) -> str:
    return (language or "").strip().lower()[:2]


def _directories(request: ClipRequest, settings: Settings) -> list[Path]:
    raw = list(request.library) or list(settings.wordreel_library)
    out: list[Path] = []
    for entry in raw:
        if not entry:
            continue
        path = Path(entry).expanduser()
        if path.is_dir():
            out.append(path)
    return out


def _media_files(
    directories: Iterable[Path], request: ClipRequest, settings: Settings
) -> list[Path]:
    suffixes = {
        suffix.lower()
        for suffix in (request.media_suffixes or settings.wordreel_media_suffixes or VIDEO_SUFFIXES)
    }
    found: list[Path] = []
    limit = max(1, settings.wordreel_max_media)
    for directory in directories:
        for path in find_video_files(directory, limit=limit):
            if path.suffix.lower() in suffixes:
                found.append(path)
            if len(found) >= limit:
                return sorted(found)
    return sorted(found)


def _sidecar_for(media: Path) -> Path | None:
    """`Film.mkv` → `Film.en.srt` / `Film.srt` / `Film.en.forced.srt`, if one exists."""
    for suffix in SIDECAR_SUFFIXES:
        exact = media.with_suffix(suffix)
        if exact.is_file():
            return exact
    stem = media.stem
    try:
        siblings = sorted(media.parent.glob(f"{glob_escape(stem)}.*"))
    except OSError:
        return None
    for sibling in siblings:
        if sibling.suffix.lower() not in SIDECAR_SUFFIXES:
            continue
        # `Film.en.srt` — strip the language tag and compare what is left
        without = sibling.with_suffix("")
        bare = _LANGUAGE_TAG.sub("", without.stem)
        if bare == stem:
            return sibling
    return None


def _media_beside(subtitle: Path, directories: list[Path]) -> Path | None:
    """The video a subtitle belongs to, for an explicitly supplied subtitle file.

    Deliberately a directory listing and not a recursive scan: this runs once per
    supplied subtitle, and re-walking a whole library for each one would turn a cheap
    pairing into the most expensive thing in the scan.
    """
    stem = subtitle.with_suffix("").stem
    bare = _LANGUAGE_TAG.sub("", stem)
    for directory in [subtitle.parent, *directories]:
        try:
            siblings = list(directory.iterdir())
        except OSError:
            continue
        for entry in siblings:
            if entry.suffix.lower() in VIDEO_SUFFIXES and entry.stem in (bare, stem):
                return entry
    return None


def _label(path: Path) -> str:
    """A readable source name: the file's own name, without the release noise."""
    stem = path.with_suffix("").stem
    return re.sub(r"[._]+", " ", _LANGUAGE_TAG.sub("", stem)).strip() or stem


def glob_escape(text: str) -> str:
    """`[` and `]` are common in release names and would be read as a character class."""
    return re.sub(r"([\[\]*?])", r"[\1]", text)


def _rank(
    candidates: list[ClipCandidate], *, request: ClipRequest, limit: int
) -> list[ClipCandidate]:
    """Best examples first, with a cap on how many come from one title.

    Without the cap the top of the list is one film: its dialogue happens to use the
    word six times in a row, and the reel teaches one voice instead of six.
    """
    ordered = sorted(
        candidates,
        key=lambda item: (
            -item.score,
            not item.exact,       # a literal hit beats an inflected one at equal score
            item.duration_ms,
            item.text,
        ),
    )
    per_source = max(1, request.per_source)
    used: Counter[str] = Counter()
    chosen: list[ClipCandidate] = []
    overflow: list[ClipCandidate] = []
    for candidate in ordered:
        key = candidate.media_label or candidate.subtitle_source
        if used[key] >= per_source:
            overflow.append(candidate)
            continue
        used[key] += 1
        chosen.append(candidate)
        if len(chosen) >= limit:
            return chosen
    # a thin library should still produce a reel rather than an error
    for candidate in overflow:
        if len(chosen) >= limit:
            break
        chosen.append(candidate)
    return chosen
