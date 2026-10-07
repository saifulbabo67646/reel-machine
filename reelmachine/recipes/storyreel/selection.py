"""Choosing the film, and mapping a story plan onto it.

Everything here is pure: given TMDB answers (or a caller's story) it decides which
title, how many sections a target length can hold, which voice and caption pack the
deployment serves, and which window of the film a section's clip may come from. No
network, no ffmpeg, no pydantic surprises.
"""

from __future__ import annotations

import os
from typing import Any

from ...core.errors import InvalidInput
from ...tmdb import TmdbClient, TmdbError
from .models import MicroClipSpec, ResolvedMedia, StoryPackage, StoryReelInputs

#: Roughly how long one storytelling line lasts when spoken — used to size the section
#: list. A 5-10 minute reel is built from ~80-160 short lines, each with its own clip.
SECTION_PACE_MS = 3_800
#: More than this and a job is a feature-length explainer, not a reel.
MAX_SECTIONS = 400
#: A shot may lead into its scene's first line by this much and still count as anchored.
SCENE_PAD_MS = 1_500


def resolve_voice(requested: str) -> str:
    """The voice to speak with: the request, the deployment's default, or the fake.

    `REEL_STORY_VOICE=cartesia:<id>` lets a deployment set its storyteller once
    (the Hindi voice, say) instead of passing `voice` on every request.
    """
    text = (requested or "").strip()
    if text:
        return text
    return os.environ.get("REEL_STORY_VOICE", "").strip() or "fake:default"


def resolve_style_id(requested: str, language: str) -> str:
    """The caption pack: the request, the language's own pack, or the default.

    A Devanagari narration needs a Devanagari font, so `language="hi"` picks
    `storyreel.hi` when the deployment ships it. An unknown language always lands on
    `storyreel.default` — never a missing pack.
    """
    text = (requested or "").strip()
    if text:
        return text
    from ...core.style import load_styles

    from .subtitles import normalise_language

    code = normalise_language(language)
    packs = load_styles()
    candidate = f"storyreel.{code}" if code else ""
    if candidate and candidate in packs:
        return candidate
    return "storyreel.default"


def resolve_media(
    client: TmdbClient,
    inputs: StoryReelInputs,
    *,
    on_detail: Any = None,
) -> ResolvedMedia:
    """Settle on one film: an explicit id, or the best hit for a free-text title."""
    detail = None
    candidates: list[dict[str, Any]] = []
    media_type = inputs.media_type
    if inputs.tmdb_id:
        try:
            detail = client.detail(inputs.tmdb_id, media_type)
        except TmdbError as exc:
            raise InvalidInput(
                f"TMDB has no {media_type} with id {inputs.tmdb_id}",
                hint="check the id, or pass a title and let the search resolve it",
                details={"tmdb_id": inputs.tmdb_id},
            ) from exc
    else:
        title = (inputs.title or "").strip()
        if not title:
            raise InvalidInput(
                "name the film or series (title, or tmdb_id)",
                hint="probe this recipe with no title to list what is trending",
            )
        found = client.find_title(
            title, media_type=media_type, year=inputs.year, limit=5
        )
        if not found:
            other = "tv" if media_type == "movie" else "movie"
            alternate = client.find_title(title, media_type=other, year=inputs.year, limit=3)
            if alternate:
                found, media_type = alternate, other
        if not found:
            raise InvalidInput(
                f"no TMDB {inputs.media_type} matched {title!r}",
                hint="try the original title, add a year, or pass tmdb_id",
                details={"title": title, "year": inputs.year},
            )
        candidates = [candidate.as_dict() for candidate in found]
        media_type = found[0].media_type
        detail = client.detail(found[0].tmdb_id, media_type)
        if on_detail:
            on_detail(f"  · matched {detail.title} ({detail.year or '?'}) tmdb={detail.tmdb_id}")

    if detail.seasons and inputs.season > detail.seasons:
        raise InvalidInput(
            f"{detail.title} has {detail.seasons} season(s); season {inputs.season} does not exist",
            hint="pick a season that exists, or use media_type=movie",
            details={"seasons": detail.seasons, "season": inputs.season},
        )
    return ResolvedMedia(
        tmdb_id=detail.tmdb_id,
        media_type=media_type,
        season=inputs.season,
        episode=inputs.episode,
        title=detail.title,
        year=detail.year,
        original_language=detail.original_language,
        runtime_min=detail.runtime_min,
        genres=detail.genres,
        overview=detail.overview,
        candidates=candidates,
    )


def trending(client: TmdbClient, *, months: int = 12, limit: int = 6) -> dict[str, list[dict[str, Any]]]:
    """What the workflow's step 1 shows: recent, popular movies and series."""
    return {
        "movies": [item.as_dict() for item in client.discover_recent(media_type="movie", months=months, limit=limit)],
        "series": [item.as_dict() for item in client.discover_recent(media_type="tv", months=months, limit=limit)],
    }


def section_budget(target_ms: int) -> int:
    """How many sections a reel of this length wants (each ~4 s of speech)."""
    return max(5, min(MAX_SECTIONS, int(round(target_ms / SECTION_PACE_MS))))


def clip_window(
    spec: MicroClipSpec,
    *,
    section_ms: int,
    movie_ms: int,
) -> tuple[int, int]:
    """The window to cut for one section: `(start_ms, duration_ms)` on the film's clock.

    The duration is the section's own spoken length, so picture and voice end together;
    the plain function of `spec` is only *where* it starts. A section longer than a few
    seconds is the script's problem (the prompts ask for 2-5 s lines), not something to
    fix by truncating the shot away from its voiceover.
    """
    duration = max(500, int(section_ms))
    if movie_ms > 0:
        duration = min(duration, max(1_000, movie_ms))
    start = max(0, int(spec.source_start_ms))
    if movie_ms > 0:
        start = min(start, max(0, movie_ms - duration - 100))
    return start, duration


def normalise_story(
    story: StoryPackage,
    *,
    movie_ms: int,
    inputs: StoryReelInputs,
    scenes: list[Any] | None = None,
) -> tuple[StoryPackage, list[str]]:
    """Validate a caller-supplied story and pin its clip windows inside the film.

    When a clip names a transcript scene (`scene_ref`), the timestamp is kept inside
    that scene's own subtitle window — a model that hallucinates a minute off lands at
    the scene's edge (with a warning) instead of showing a random moment; an unknown
    reference is kept and reported. Without a reference the timestamp is honoured, so
    a caller who knows the film exactly keeps full control.
    """
    if not story.sections:
        raise InvalidInput(
            "the story has no sections",
            hint="each section is {voiceover, clip: {source_start_ms, ...}}",
        )
    warnings: list[str] = []
    by_scene_id = {scene.id: scene for scene in (scenes or [])}
    seen: set[str] = set()
    sections = []
    for index, section in enumerate(story.sections, start=1):
        identifier = (section.id or f"sec-{index:03d}").strip()
        while identifier in seen:
            identifier = f"{identifier}-{index}"
        seen.add(identifier)
        if not section.voiceover.strip():
            raise InvalidInput(
                f"section {identifier!r} has no voiceover",
                hint="every section needs a line to speak",
            )
        clip = section.clip
        start = max(0, int(clip.source_start_ms))
        if movie_ms > 0 and start >= movie_ms:
            warnings.append(
                f"section {identifier}: clip start {start} ms is past the end of the film; moved to 0"
            )
            start = 0
        reference = (clip.scene_ref or "").strip()
        if reference:
            scene = by_scene_id.get(reference)
            if scene is None:
                warnings.append(
                    f"section {identifier}: scene_ref {reference!r} is not in the transcript; timestamp kept"
                )
            else:
                lower = max(0, scene.start_ms - SCENE_PAD_MS)
                upper = scene.end_ms + SCENE_PAD_MS
                anchored = min(max(start, lower), upper)
                if anchored != start:
                    warnings.append(
                        f"section {identifier}: clip moved from {start} ms into scene {reference} "
                        f"({scene.start_ms}–{scene.end_ms} ms) → {anchored} ms"
                    )
                    start = anchored
        end = clip.source_end_ms
        if end is not None and end <= start:
            end = None
        clip = clip.model_copy(update={"source_start_ms": start, "source_end_ms": end})
        if clip.transition == "fade":
            warnings.append(
                f"section {identifier}: transition 'fade' is recorded but hard cuts are rendered"
            )
        sections.append(section.model_copy(update={"id": identifier, "clip": clip}))
    if len(sections) < 3:
        warnings.append(f"only {len(sections)} section(s); a story reel usually wants more")
    normalised = story.model_copy(update={"sections": sections})
    if inputs.target_ms and movie_ms:
        spoken = sum(max(1, len(s.voiceover.split())) for s in sections)
        if spoken < 10:
            warnings.append("the voiceover is very short for the requested length")
    return normalised, warnings


def find_concept(concepts: list[Any], wanted: str) -> Any | None:
    """Resolve a chosen concept id — by id, by exact name, or by 1-based position."""
    if not wanted:
        return None
    for concept in concepts:
        if concept.id and concept.id == wanted:
            return concept
    lowered = wanted.strip().lower()
    for concept in concepts:
        if concept.name.strip().lower() == lowered:
            return concept
    if lowered.isdigit():
        position = int(lowered)
        if 1 <= position <= len(concepts):
            return concepts[position - 1]
        if position == 0 and concepts:
            return concepts[0]
    for concept in concepts:
        if concept.id and concept.id.lower() == lowered:
            return concept
    return None
