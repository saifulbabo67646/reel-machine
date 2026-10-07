"""storyreel stages: acquire → subtitles → transcript → concepts → script →
voiceover → prep → compose → render.

The first three stages are mechanical (download the film, get its script, read it);
the middle two are the creative work, which the caller can supply or the `story`
provider group can produce; the last four build the reel from many short clips.
`mode` decides where the pipeline stops: `transcript` after the script is readable,
`plan` after the concepts exist, `build` through to the render.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Provenance
from ...core.errors import (
    InvalidInput,
    PreflightFailed,
    ProviderUnavailable,
    VoiceUnavailable,
)
from ...core.stage import StageContext
from ...core.style import get_style
from ...core.timeline import Caption, Clip, Music, TimedAudioRef, Timeline, WordSpan
from ...core.timing import proportional_word_spans
from ...render.captions import write_ass, write_srt
from ...sources.base import SourceError
from ...tmdb import TmdbClient
from .models import (
    AcquireOutput,
    ClipRecord,
    ConceptsOutput,
    PrepOutput,
    ScriptOutput,
    SectionTiming,
    StoryCompose,
    StoryConcept,
    StoryReelInputs,
    StoryRenderOutput,
    SubtitlesOutput,
    TranscriptOutput,
    VoiceoverOutput,
)
from .render import caption_styles, render_storyreel
from .selection import (
    clip_window,
    find_concept,
    normalise_story,
    resolve_media,
    resolve_style_id,
    resolve_voice,
    section_budget,
)
from .subtitles import SubtitleRequest, normalise_language
from .subtitles import files as subtitle_files
from .sync import apply_offset, sync_srt
from .transcript import (
    clean_voiceover,
    markers_in,
    parse_srt,
    segment_scenes,
    speech_parts,
    transcript_stats,
    transcript_text,
    write_cues,
)

GEOMETRY = {
    "vertical": (1080, 1920),
    "square": (1080, 1080),
    "landscape": (1920, 1080),
}

CLIP_HEIGHT = 1080
FPS = 30
#: A `[Pause]` marker becomes real silence this long.
PAUSE_MS = 350
#: Longer than this and a section is one lingering shot — worth saying out loud.
LONG_SHOT_MS = 8_000

#: ISO-639-1 → the tags releases actually carry.
_AUDIO_TAGS = {
    "en": ["eng", "en"],
    "ja": ["jpn", "ja", "jp", "japanese"],
    "hi": ["hin", "hi"],
    "ur": ["urd", "ur"],
    "ar": ["ara", "ar"],
    "es": ["spa", "es"],
    "fr": ["fre", "fra", "fr"],
    "de": ["ger", "deu", "de"],
    "pt": ["por", "pt"],
    "ru": ["rus", "ru"],
    "tr": ["tur", "tr"],
    "ko": ["kor", "ko"],
    "zh": ["chi", "zho", "zh"],
    "it": ["ita", "it"],
    "fa": ["per", "fas", "fa"],
    "bn": ["ben", "bn"],
    "ta": ["tam", "ta"],
    "te": ["tel", "te"],
    "ml": ["mal", "ml"],
    "th": ["tha", "th"],
    "id": ["ind", "id"],
    "vi": ["vie", "vi"],
    "he": ["heb", "he"],
    "pl": ["pol", "pl"],
    "nl": ["dut", "nld", "nl"],
    "sv": ["swe", "sv"],
    "no": ["nor", "no"],
    "da": ["dan", "da"],
    "fi": ["fin", "fi"],
    "el": ["gre", "ell", "el"],
    "uk": ["ukr", "uk"],
}


def _audio_preference(language: str) -> list[str]:
    """Which audio tags are acceptable for a film in this original language.

    An empty list means "accept any audio" — the reel mutes the film anyway, so a
    preference that cannot be satisfied must never block the download.
    """
    code = (language or "").strip().lower()[:2]
    if not code or not code.isalpha():
        return []
    return _AUDIO_TAGS.get(code, [code])


def _artifacts_dir(ctx: StageContext) -> Path:
    out = ctx.workdir / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _media_dict(media: Any) -> dict[str, Any]:
    return {
        "tmdbId": media.tmdb_id,
        "mediaType": media.media_type,
        "title": media.title,
        "year": media.year,
        "season": media.season,
        "episode": media.episode,
        "label": media.label(),
    }


class AcquireStage:
    """Resolve the title through TMDB and download the whole film."""

    id = "acquire"
    revision = 1
    cacheable = False  # signed download links expire; a cached resolve would be a trap
    output_model = AcquireOutput

    def run(self, ctx: StageContext, payload: Any) -> AcquireOutput:
        inputs = StoryReelInputs.model_validate(payload)
        settings: Settings = ctx.config
        ctx.cancel.raise_if_cancelled()

        client = TmdbClient(settings=settings)
        media = resolve_media(client, inputs, on_detail=ctx.progress.detail)
        ctx.progress.detail(f"  · {media.label()} — tmdb {media.tmdb_id} ({media.media_type})")

        provider = ctx.providers.get("source")
        provider_name = getattr(provider, "name", "") or "source"
        asset = provider.resolve(
            None,
            inputs.episode,
            tmdb_id=media.tmdb_id,
            media_type=media.media_type,
            season=media.season,
            audio_pref=_audio_preference(media.original_language),
        )
        local = Path(asset.local_path) if asset.local_path else None
        if local is None or not local.is_file():
            raise InvalidInput(
                "the film was resolved but not downloaded",
                hint="storyreel cuts micro-clips, so it needs the file: set REEL_VIDVAULT_DOWNLOAD=true",
                details={"source": provider_name, "url": asset.url},
            )

        duration_ms = int(asset.duration_ms or 0)
        width = height = 0
        size_bytes = 0
        subtitle_tracks: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            info = provider.probe(asset)
        except ffmpeg.FFmpegError as exc:
            raise SourceError(f"the downloaded film could not be probed: {exc}") from exc
        duration_ms = duration_ms or int(info.duration_s * 1000)
        width, height = info.width, info.height
        size_bytes = info.size_bytes or local.stat().st_size
        subtitle_tracks = [
            {
                "ordinal": track.ordinal,
                "codec": track.codec,
                "language": track.language,
                "text": track.text,
                "forced": track.forced,
                "default": track.default,
            }
            for track in info.subtitle_tracks
        ]
        if duration_ms <= 0:
            warnings.append("the film's duration could not be measured")
        if not (width and height):
            warnings.append("the film's frame size could not be measured")
        ctx.progress.detail(
            f"  · {duration_ms / 60_000:.1f} min, {width}x{height}, "
            f"{size_bytes / (1 << 30):.2f} GB, {len(subtitle_tracks)} subtitle track(s)"
        )
        return AcquireOutput(
            inputs=inputs,
            media=media,
            video_path=str(local),
            duration_ms=duration_ms,
            width=width,
            height=height,
            size_bytes=size_bytes,
            provider_label=asset.label,
            subtitle_tracks=subtitle_tracks,
            source_meta={
                key: value
                for key, value in (asset.meta or {}).items()
                if key in {"tmdb", "mediaType", "season", "resolution", "matchReason", "audioLanguage", "warning"}
            },
            warnings=warnings,
        )


class SubtitlesStage:
    """The film's script: the caller's file, the film's own streams, or a database."""

    id = "subtitles"
    revision = 2
    cacheable = True
    # dotted paths reach the *refreshed request* on the payload (runner.payload_field),
    # so a changed subtitle path/language/offset always invalidates this stage
    cache_fields = (
        "video_path",
        "media",
        "inputs.subtitle_path",
        "inputs.subtitle_language",
        "inputs.subtitle_offset_ms",
    )
    output_model = SubtitlesOutput

    def run(self, ctx: StageContext, payload: AcquireOutput) -> SubtitlesOutput:
        inputs = payload.inputs
        media = payload.media
        dest_dir = ctx.workdir / "subtitle"
        warnings = list(payload.warnings)
        attempts: list[dict[str, Any]] = []
        licence = None

        if inputs.subtitle_path:
            supplied = Path(inputs.subtitle_path).expanduser()
            if not supplied.is_file():
                raise InvalidInput(
                    f"subtitle file not found: {supplied}",
                    hint="subtitle_path points at an .srt (or .ass/.vtt) the caller supplies",
                )
            raw = supplied
            source = "caller"
            language = inputs.subtitle_language
            provenance = Provenance(provider="caller", source=str(supplied))
        else:
            provider = ctx.providers.get("subtitles")
            request = SubtitleRequest(
                tmdb_id=media.tmdb_id,
                media_type=media.media_type,
                season=media.season,
                episode=media.episode,
                title=media.title,
                year=media.year,
                languages=[normalise_language(inputs.subtitle_language)] or ["en"],
                movie_path=payload.video_path,
                duration_ms=payload.duration_ms,
            )
            fetch = provider.fetch(request, dest_dir=dest_dir)
            raw = Path(fetch.path)
            source = fetch.source
            language = fetch.language or request.primary_language()
            licence = fetch.licence
            attempts = [item for item in (fetch.meta.get("attempts") or []) if isinstance(item, dict)]
            provenance = Provenance(
                provider=source,
                source=f"tmdb:{media.tmdb_id}",
                notes="; ".join(filter(None, [str(fetch.meta.get("release") or ""), str(fetch.meta.get("reference") or "")])),
            )
            ctx.progress.detail(f"  · subtitle via {source}")

        normalised = subtitle_files.to_srt(raw, ctx.workdir / "normalised")
        if normalised is None or not normalised.is_file():
            raise InvalidInput(
                "the subtitle file could not be read as an SRT",
                hint="a text subtitle is needed; bitmap subtitles (PGS/VobSub) are not readable",
                details={"source": source, "path": str(raw)},
            )
        srt_path = normalised
        cues = parse_srt(subtitle_files.read_text(srt_path) or "")
        if not cues:
            raise InvalidInput(
                "the subtitle has no usable cues",
                hint="check the file; or pass subtitle_path with another release",
                details={"source": source, "path": str(srt_path)},
            )

        synced = source in ("embedded", "caller")
        if source == "embedded":
            ctx.progress.detail("  · embedded subtitles: timing is exactly this file's")
        elif source == "caller":
            # the caller handed us the file this run should trust (often extracted from
            # this very copy of the film); alignment is theirs to guarantee
            ctx.progress.detail(f"  · caller-supplied subtitles ({language})")
        elif os.environ.get("REEL_SUBTITLES_SYNC", "auto").strip().lower() != "off":
            synced_path = ctx.workdir / "synced.srt"
            synced_ok, note = sync_srt(Path(payload.video_path), srt_path, synced_path)
            if synced_ok:
                synced = True
                srt_path = synced_path
                cues = parse_srt(subtitle_files.read_text(srt_path) or "")
                ctx.progress.detail("  · synced to this copy of the film with ffsubsync")
            else:
                warnings.append(note)
        else:
            warnings.append(
                "downloaded subtitles were not aligned to this copy of the film; "
                "clip timestamps may drift (install ffsubsync, or pass subtitle_offset_ms)"
            )

        if inputs.subtitle_offset_ms:
            cues = apply_offset(cues, inputs.subtitle_offset_ms)
            if not cues:
                raise InvalidInput(
                    "subtitle_offset_ms moved every cue off the start of the film",
                    hint="check the sign and the magnitude of the offset",
                    details={"offset_ms": inputs.subtitle_offset_ms},
                )
        final = ctx.workdir / "subtitle.srt"
        write_cues(cues, final)

        asset = ctx.assets.put(
            final,
            kind=AssetKind.SUBTITLE,
            provenance=provenance,
            licence=licence,
            ext=".srt",
            meta={"source": source, "language": language, "synced": synced},
        )
        ctx.progress.detail(f"  · {len(cues)} cues from {source}{' (synced)' if synced else ''}")
        return SubtitlesOutput(
            inputs=inputs,
            media=media,
            video_path=payload.video_path,
            duration_ms=payload.duration_ms,
            width=payload.width,
            height=payload.height,
            path=str(final),
            asset=asset.id,
            source=source,
            language=language,
            cues=cues,
            span_start_ms=min(cue.start_ms for cue in cues),
            span_end_ms=max(cue.end_ms for cue in cues),
            attempts=attempts,
            synced=synced,
            offset_ms=inputs.subtitle_offset_ms,
            provenance=provenance,
            licence=licence,
            warnings=warnings,
        )


class TranscriptStage:
    """Cues become scenes, and the readable transcript the creative work starts from."""

    id = "transcript"
    revision = 2
    cacheable = True
    # the SRT asset id is its content hash; the media copy keeps a changed film from
    # reusing a transcript built for another one
    cache_fields = ("asset", "media")
    output_model = TranscriptOutput

    def halt_for(self, payload: SubtitlesOutput) -> bool:
        """`mode="transcript"` stops here — recomputed even on a cache hit."""
        return payload.inputs.wants_transcript_only()

    def run(self, ctx: StageContext, payload: SubtitlesOutput) -> TranscriptOutput:
        inputs = payload.inputs
        scenes = segment_scenes(payload.cues)
        stats = transcript_stats(payload.cues, scenes)
        _write_transcript_artifacts(ctx, payload, scenes, stats)
        ctx.progress.detail(f"  · {len(payload.cues)} cues → {len(scenes)} scenes ({stats['words']} words)")
        return TranscriptOutput(
            inputs=inputs,
            media=payload.media,
            video_path=payload.video_path,
            duration_ms=payload.duration_ms,
            width=payload.width,
            height=payload.height,
            srt_path=payload.path,
            subtitle_source=payload.source,
            subtitle_language=payload.language,
            synced=payload.synced,
            cues=payload.cues,
            scenes=scenes,
            stats=dict(stats),
            warnings=list(payload.warnings),
            halt_pipeline=inputs.wants_transcript_only(),
        )


def _write_transcript_artifacts(
    ctx: StageContext,
    payload: SubtitlesOutput,
    scenes: list[Any],
    stats: dict[str, Any],
) -> None:
    out = _artifacts_dir(ctx)
    shutil.copy2(payload.path, out / "subtitles.srt")
    (out / "transcript.txt").write_text(
        transcript_text(payload.cues, title=payload.media.label()), encoding="utf-8"
    )
    (out / "transcript.json").write_text(
        json.dumps(
            {
                "media": _media_dict(payload.media),
                "source": payload.source,
                "language": payload.language,
                "synced": payload.synced,
                "offsetMs": payload.offset_ms,
                "stats": stats,
                "cues": [cue.model_dump() for cue in payload.cues],
                "scenes": [scene.model_dump() for scene in scenes],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class ConceptsStage:
    """The top 5 viral story concepts — caller-supplied, or found by the brain."""

    id = "concepts"
    revision = 1
    cacheable = True
    cache_fields = (
        "media",
        "scenes",
        "inputs.content_type",
        "inputs.language",
        "inputs.platform",
        "inputs.target_ms",
        "inputs.concepts",
        "inputs.story",
    )
    output_model = ConceptsOutput

    def halt_for(self, payload: TranscriptOutput) -> bool:
        """`mode="plan"` stops here — recomputed even on a cache hit."""
        return payload.inputs.wants_plan_only()

    def run(self, ctx: StageContext, payload: TranscriptOutput) -> ConceptsOutput:
        inputs = payload.inputs
        warnings = list(payload.warnings)
        if inputs.concepts:
            concepts = _tidy_concepts(list(inputs.concepts))
            source = "caller"
            ctx.progress.detail(f"  · {len(concepts)} concept(s) supplied by the caller")
        elif inputs.story:
            # the caller handed over the finished story package: there is nothing to
            # choose, so no brain is needed (and a brain-less deployment still builds)
            concepts = []
            source = "supplied-story"
            ctx.progress.detail("  · a full story package was supplied; concepts are not needed")
        else:
            brain = ctx.providers.get("brain")
            problems = getattr(brain, "missing", lambda: [])()
            if problems:
                raise ProviderUnavailable(
                    "the story brain is not usable on this deployment",
                    hint="; ".join(problems) + " — or supply concepts yourself",
                    details={"role": "brain", "provider": getattr(brain, "name", "")},
                )
            concepts = _tidy_concepts(
                brain.concepts(
                    scenes=payload.scenes,
                    media=payload.media,
                    inputs=inputs,
                    on_detail=ctx.progress.detail,
                )
            )
            source = "brain"
        out = _artifacts_dir(ctx)
        # the concepts artifact is the interface the caller chooses from
        (out / "concepts.json").write_text(
            json.dumps(
                {
                    "source": source,
                    "media": _media_dict(payload.media),
                    "brief": {
                        "contentType": inputs.content_type,
                        "language": inputs.language,
                        "platform": inputs.platform,
                        "targetMs": inputs.target_ms,
                    },
                    "concepts": [concept.model_dump() for concept in concepts],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        halt = inputs.wants_plan_only()
        if halt:
            ctx.progress.detail(f"  · plan mode: stopping with {len(concepts)} concept(s)")
        return ConceptsOutput(
            inputs=inputs,
            media=payload.media,
            video_path=payload.video_path,
            duration_ms=payload.duration_ms,
            width=payload.width,
            height=payload.height,
            scenes=payload.scenes,
            concepts=concepts,
            source=source,
            warnings=warnings,
            halt_pipeline=halt,
        )


def _tidy_concepts(concepts: list[StoryConcept]) -> list[StoryConcept]:
    tidied: list[StoryConcept] = []
    seen: set[str] = set()
    for index, concept in enumerate(concepts, start=1):
        identifier = concept.id.strip() or f"c{index}"
        while identifier in seen:
            identifier = f"{identifier}-{index}"
        seen.add(identifier)
        tidied.append(concept.model_copy(update={"id": identifier}))
    return tidied


class ScriptStage:
    """The storytelling package: sections, micro-clip plan and SEO."""

    id = "script"
    revision = 3
    cacheable = True
    cache_fields = (
        "media",
        "scenes",
        "concepts",
        "inputs.concept",
        "inputs.concepts",
        "inputs.story",
        "inputs.content_type",
        "inputs.language",
        "inputs.platform",
        "inputs.target_ms",
    )
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: ConceptsOutput) -> ScriptOutput:
        inputs = payload.inputs
        warnings = list(payload.warnings)
        if inputs.story:
            story = inputs.story
            source = "caller"
        else:
            concept = find_concept(payload.concepts, inputs.concept or "")
            if concept is None:
                known = ", ".join(concept.id or concept.name for concept in payload.concepts) or "none"
                raise InvalidInput(
                    "no story concept was chosen",
                    hint=f"pass concept=<one of: {known}>, or supply a full story package",
                    details={"concepts": [item.id for item in payload.concepts]},
                )
            brain = ctx.providers.get("brain")
            problems = getattr(brain, "missing", lambda: [])()
            if problems:
                raise ProviderUnavailable(
                    "the story brain is not usable on this deployment",
                    hint="; ".join(problems) + " — or supply the story package yourself",
                    details={"role": "brain"},
                )
            ctx.progress.detail(f"  · writing the story for {concept.id} ({concept.name})")
            story = brain.script(
                scenes=payload.scenes,
                media=payload.media,
                inputs=inputs,
                concept=concept,
                on_detail=ctx.progress.detail,
            )
            source = "brain"

        story, extra = normalise_story(
            story, movie_ms=payload.duration_ms, inputs=inputs, scenes=payload.scenes
        )
        warnings.extend(extra)
        budget = section_budget(inputs.target_ms)
        if len(story.sections) < budget * 0.4 or len(story.sections) > budget * 1.6:
            warnings.append(
                f"{len(story.sections)} sections for a {inputs.target_ms / 1000:.0f}s target "
                f"(~{budget} wanted)"
            )
        if story.seo is None:
            warnings.append("no SEO package was produced")
        referenced = {
            section.clip.scene_ref for section in story.sections if section.clip.scene_ref
        }
        scene_index = {
            scene.id: (scene.text[:240] + "…" if len(scene.text) > 240 else scene.text)
            for scene in payload.scenes
            if scene.id in referenced
        }
        missing = sorted(reference for reference in referenced if reference not in scene_index)
        if missing:
            warnings.append(
                f"{len(missing)} scene reference(s) are not in the transcript: {', '.join(missing[:4])}"
            )
        ctx.progress.detail(f"  · {len(story.sections)} section(s) [{source}]")
        out = _artifacts_dir(ctx)
        (out / "story.json").write_text(
            json.dumps(
                {
                    "source": source,
                    "media": _media_dict(payload.media),
                    "story": story.model_dump(),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (out / "seo.json").write_text(
            json.dumps(
                {
                    "media": _media_dict(payload.media),
                    "seo": story.seo.model_dump() if story.seo else None,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return ScriptOutput(
            inputs=inputs,
            media=payload.media,
            video_path=payload.video_path,
            duration_ms=payload.duration_ms,
            width=payload.width,
            height=payload.height,
            story=story,
            scene_index=scene_index,
            source=source,
            warnings=warnings,
        )


class VoiceoverStage:
    """Speak every section, with real word timings; a missing voice fails loudly."""

    id = "voiceover"
    revision = 1
    cacheable = False  # cloud voices bill per character; a re-run is a deliberate act
    output_model = VoiceoverOutput

    def run(self, ctx: StageContext, payload: ScriptOutput) -> VoiceoverOutput:
        ctx.cancel.raise_if_cancelled()
        inputs = payload.inputs
        voice = resolve_voice(inputs.voice)
        provider = ctx.providers.get("narration")
        provider_name = getattr(provider, "name", "")
        requested = _provider_of(voice)
        if requested and requested != provider_name:
            raise VoiceUnavailable(
                f"the voice {voice!r} needs the {requested!r} narration provider, "
                f"but this deployment serves {provider_name!r}",
                hint=f"use voice '{provider_name}:<voice-id>', or set REEL_TTS={requested}",
                details={"requested": requested, "bound": provider_name},
            )

        workdir = ctx.workdir / "sections"
        workdir.mkdir(parents=True, exist_ok=True)
        silence = _silence_file(ctx)
        warnings = list(payload.warnings)
        paths: list[Path] = []
        sections: list[SectionTiming] = []
        provenance = Provenance(provider=provider_name, source=voice)
        licence = None
        provider_timed = 0
        cursor = 0

        for index, section in enumerate(payload.story.sections, start=1):
            ctx.cancel.raise_if_cancelled()
            parts = speech_parts(section.voiceover)
            if not any(part for part in parts):
                raise InvalidInput(
                    f"section {section.id!r} has nothing to speak",
                    hint="markers alone are not a line; add the words",
                )
            piece_paths: list[Path] = []
            section_words: list[WordSpan] = []
            local_cursor = 0
            timed_here = False
            for part_index, part in enumerate(parts, start=1):
                if part is None:
                    if silence is None:
                        warnings.append(f"{section.id}: a [Pause] was dropped (no silence file)")
                        continue
                    local = workdir / f"{section.id}-{part_index:02d}-pause.wav"
                    shutil.copy2(silence, local)
                    piece_paths.append(local)
                    local_cursor += PAUSE_MS
                    continue
                speech = provider.synthesize(part, voice=voice, language=inputs.language)
                duration = int(speech.duration_ms or 0) or int(ffmpeg.probe(speech.path).duration_s * 1000)
                if duration <= 0:
                    raise InvalidInput(f"the voice produced no audio for {section.id!r}")
                suffix = Path(speech.path).suffix or ".wav"
                local = workdir / f"{section.id}-{part_index:02d}{suffix}"
                shutil.copy2(speech.path, local)
                piece_paths.append(local)
                words = list(speech.segments[0].words) if speech.segments else []
                if words:
                    timed_here = True
                    section_words.extend(
                        WordSpan(
                            text=word.text,
                            start_ms=local_cursor + word.start_ms,
                            end_ms=local_cursor + word.end_ms,
                        )
                        for word in words
                    )
                else:
                    section_words.extend(
                        proportional_word_spans(
                            part.split(), local_cursor, local_cursor + duration
                        )
                    )
                local_cursor += duration
                licence = speech.licence or licence
                if speech.provenance.provider:
                    provenance = speech.provenance

            joined = workdir / f"{section.id}.wav"
            if len(piece_paths) == 1:
                shutil.copy2(piece_paths[0], joined)
            else:
                ffmpeg.concat_audio(piece_paths, joined, cancel=ctx.cancel)
            actual = int(ffmpeg.probe(joined).duration_s * 1000)
            if actual <= 0:
                raise InvalidInput(f"section {section.id!r} produced unmeasurable audio")
            if local_cursor and abs(actual - local_cursor) > 120:
                factor = actual / local_cursor
                section_words = [
                    WordSpan(
                        text=word.text,
                        start_ms=int(round(word.start_ms * factor)),
                        end_ms=int(round(word.end_ms * factor)),
                    )
                    for word in section_words
                ]
            provider_timed += 1 if timed_here else 0
            sections.append(
                SectionTiming(
                    id=section.id,
                    start_ms=cursor,
                    end_ms=cursor + actual,
                    words=[
                        WordSpan(
                            text=word.text,
                            start_ms=cursor + word.start_ms,
                            end_ms=cursor + word.end_ms,
                        )
                        for word in section_words
                    ],
                    markers=markers_in(section.voiceover),
                )
            )
            paths.append(joined)
            cursor += actual
            ctx.progress.detail(f"  · {section.id}: {actual / 1000:.1f}s")
            ctx.progress.percent(100 * index / len(payload.story.sections))

        joined_total = ctx.workdir / "narration.wav"
        _join_many(paths, joined_total, cancel=ctx.cancel)
        total = int(ffmpeg.probe(joined_total).duration_s * 1000)
        if cursor and abs(total - cursor) > 120:
            factor = total / cursor
            sections = _scale_sections(sections, factor)
            ctx.progress.detail(f"  · narration {cursor} ms became {total} ms after joining")
        asset = ctx.assets.put(
            joined_total,
            kind=AssetKind.NARRATION,
            provenance=provenance,
            licence=licence,
            ext=".wav",
            meta={"voice": voice},
        )
        timings_source = (
            "provider" if provider_timed == len(sections) else ("mixed" if provider_timed else "proportional")
        )
        ctx.progress.detail(f"  · word timings: {timings_source}")
        return VoiceoverOutput(
            inputs=inputs,
            media=payload.media,
            video_path=payload.video_path,
            duration_ms=payload.duration_ms,
            width=payload.width,
            height=payload.height,
            story=payload.story,
            scene_index=payload.scene_index,
            audio_asset=asset.id,
            narration_ms=total,
            sections=sections,
            timings_source=timings_source,
            provider=provider_name,
            provenance=provenance,
            licence=licence,
            warnings=warnings,
        )


def _provider_of(voice: str) -> str:
    """`"elevenlabs:Rachel"` → `"elevenlabs"`; a bare name is itself."""
    text = (voice or "").strip()
    if ":" in text:
        return text.split(":", 1)[0].strip()
    return text or "fake"


def _silence_file(ctx: StageContext) -> Path | None:
    dest = ctx.workdir / "silence.wav"
    if dest.is_file():
        return dest
    settings: Settings = ctx.config
    command = [
        settings.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-t",
        f"{PAUSE_MS / 1000:.3f}",
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    try:
        ffmpeg.run(command, cancel=ctx.cancel)
    except ffmpeg.FFmpegError:
        return None
    return dest


def _join_many(paths: list[Path], dest: Path, *, cancel: Any, batch: int = 24) -> None:
    """Join any number of audio files, a batch at a time.

    A 5-10 minute reel is 80-160 sections; handing ffmpeg 160 inputs in one
    `filter_complex` runs into the process's open-file limit for no benefit. Batching
    keeps every command small (and loggable) while the joins stay lossless PCM.
    """
    if not paths:
        raise InvalidInput("there is no narration to join")
    current = list(paths)
    round_index = 0
    while len(current) > 1:
        round_index += 1
        joined: list[Path] = []
        for index in range(0, len(current), batch):
            group = current[index : index + batch]
            if len(group) == 1:
                joined.append(group[0])
                continue
            part = dest.parent / f"join-{round_index:02d}-{index // batch:03d}.wav"
            ffmpeg.concat_audio(group, part, cancel=cancel)
            joined.append(part)
        current = joined
    if current[0] != dest:
        shutil.copy2(current[0], dest)


def _scale_sections(sections: list[SectionTiming], factor: float) -> list[SectionTiming]:
    scaled: list[SectionTiming] = []
    for section in sections:
        scaled.append(
            section.model_copy(
                update={
                    "start_ms": int(round(section.start_ms * factor)),
                    "end_ms": int(round(section.end_ms * factor)),
                    "words": [
                        WordSpan(
                            text=word.text,
                            start_ms=int(round(word.start_ms * factor)),
                            end_ms=int(round(word.end_ms * factor)),
                        )
                        for word in section.words
                    ],
                }
            )
        )
    return scaled


class PrepStage:
    """Cut every micro-clip out of the film, after a preflight of the first."""

    id = "prep"
    revision = 1
    cacheable = False
    output_model = PrepOutput

    def run(self, ctx: StageContext, payload: VoiceoverOutput) -> PrepOutput:
        inputs = payload.inputs
        story = payload.story
        if len(story.sections) != len(payload.sections):
            raise InvalidInput(
                "the sections and their timings disagree",
                hint="re-run the job; a stale stage output is the usual cause",
            )
        video = Path(payload.video_path)
        if not video.is_file():
            raise InvalidInput(
                "the film is no longer on disk",
                hint="re-run to download it again (REEL_VIDVAULT_DIR keeps it cached)",
                details={"path": str(video)},
            )
        zoom_size = None
        if payload.width and payload.height:
            scaled_width = int(round(payload.width * CLIP_HEIGHT / payload.height))
            zoom_size = (max(2, scaled_width + (scaled_width % 2)), CLIP_HEIGHT)

        width, height = GEOMETRY[inputs.aspect]
        provenance = Provenance(
            provider="source",
            source=f"tmdb:{payload.media.tmdb_id}",
            notes=payload.media.label(),
        )
        clips: list[Any] = []
        warnings = list(payload.warnings)
        total = len(story.sections)
        for index, (section, timing) in enumerate(zip(story.sections, payload.sections), start=1):
            ctx.cancel.raise_if_cancelled()
            section_ms = max(500, timing.end_ms - timing.start_ms)
            start, duration = clip_window(
                section.clip, section_ms=section_ms, movie_ms=payload.duration_ms
            )
            dest = ctx.workdir / f"clip-{index:03d}.mp4"
            zoom = section.clip.zoom
            ffmpeg.cut_clip(
                video,
                dest,
                start_s=start / 1000.0,
                duration_s=duration / 1000.0,
                height=CLIP_HEIGHT,
                fps=FPS,
                zoom=zoom,
                zoom_size=zoom_size if zoom != "none" else None,
                cancel=ctx.cancel,
            )
            info = ffmpeg.probe(dest)
            actual = int(info.duration_s * 1000)
            if index == 1:
                if not info.has_video:
                    raise PreflightFailed(
                        "the first micro-clip has no video",
                        hint="check that the downloaded film is a real video file",
                    )
                if abs(actual - section_ms) > 250:
                    raise PreflightFailed(
                        f"the first micro-clip came out {actual} ms instead of {section_ms} ms",
                        hint="check the film's codec and the ffmpeg build",
                    )
            elif abs(actual - section_ms) > 250:
                warnings.append(
                    f"{section.id}: clip is {actual} ms against a {section_ms} ms slot"
                )
            if section_ms > LONG_SHOT_MS:
                warnings.append(
                    f"{section.id}: {section_ms / 1000:.1f}s is one long shot — short lines keep the edit alive"
                )
            asset = ctx.assets.put(
                dest,
                kind=AssetKind.SOURCE_CUT,
                provenance=provenance,
                ext=".mp4",
                meta={
                    "startMs": start,
                    "durationMs": duration,
                    "zoom": zoom,
                    "scene": section.clip.scene,
                    "tmdb": payload.media.tmdb_id,
                },
            )
            clips.append(
                ClipRecord(
                    element_id=f"clip-{index:03d}",
                    asset=asset.id,
                    start_ms=timing.start_ms,
                    duration_ms=section_ms,
                    source_start_ms=start,
                    zoom=zoom,
                )
            )
            ctx.progress.detail(
                f"  · clip {index}/{total}: film {start / 1000:.1f}s +{duration / 1000:.1f}s"
                f"{f' zoom={zoom}' if zoom != 'none' else ''}"
            )
            ctx.progress.percent(100 * index / total)
        return PrepOutput(
            inputs=inputs,
            media=payload.media,
            video_path=payload.video_path,
            duration_ms=payload.duration_ms,
            width=payload.width,
            height=payload.height,
            story=story,
            scene_index=payload.scene_index,
            audio_asset=payload.audio_asset,
            narration_ms=payload.narration_ms,
            sections=payload.sections,
            clips=clips,
            frame=(width, height),
            clip_height=CLIP_HEIGHT,
            provider=payload.provider,
            warnings=warnings,
        )


class ComposeStage:
    """Clips, narration, captions and overlays become the timeline."""

    id = "compose"
    revision = 1
    cacheable = False
    output_model = StoryCompose

    def run(self, ctx: StageContext, payload: PrepOutput) -> StoryCompose:
        inputs = payload.inputs
        settings: Settings = ctx.config
        style = get_style(resolve_style_id(inputs.style, inputs.language))
        width, height = payload.frame

        captions: list[Caption] = []
        for section, timing in zip(payload.story.sections, payload.sections):
            captions.append(
                Caption(
                    start_ms=timing.start_ms,
                    end_ms=timing.end_ms,
                    text=clean_voiceover(section.voiceover),
                    lang=inputs.language,
                    style_ref="storyreel.voice",
                    words=timing.words,
                    meta={"source": section.id},
                )
            )
            overlay = section.clip.text_overlay.strip()
            if overlay:
                captions.append(
                    Caption(
                        start_ms=timing.start_ms,
                        end_ms=min(timing.end_ms, timing.start_ms + 3_500),
                        text=overlay,
                        style_ref="storyreel.overlay",
                        meta={"source": section.id},
                    )
                )

        audio: list[Any] = [
            TimedAudioRef(
                element_id="narration",
                asset=payload.audio_asset,
                duration_ms=payload.narration_ms,
                role="narration",
            )
        ]
        if inputs.music:
            music_path = Path(inputs.music).expanduser()
            if not music_path.is_file():
                raise InvalidInput(f"music file not found: {music_path}")
            music_asset = ctx.assets.put(
                music_path,
                kind=AssetKind.MUSIC,
                provenance=Provenance(provider="caller", source=str(music_path)),
                meta={"gainDb": -20},
            )
            audio.append(
                Music(
                    element_id="music",
                    asset=music_asset.id,
                    duration_ms=payload.narration_ms,
                    gain_db=-20.0,
                    loop=True,
                )
            )

        timeline = Timeline(
            duration_ms=payload.narration_ms,
            aspect=inputs.aspect,
            render_mode="story",
            style=style.id,
            video=[
                Clip(
                    element_id=record.element_id,
                    asset=record.asset,
                    start_ms=record.start_ms,
                    duration_ms=record.duration_ms,
                    origin="cut",
                    keep_audio=False,
                )
                for record in payload.clips
            ],
            audio=audio,
            captions=captions,
            meta={
                "tmdb": payload.media.tmdb_id,
                "title": payload.media.title,
                "concept": payload.story.concept_id,
                "provider": payload.provider,
                "voice": inputs.voice,
            },
        )

        plan = {
            "media": _media_dict(payload.media),
            "concept": {
                "id": payload.story.concept_id,
                "name": payload.story.concept_name,
                "viralReason": payload.story.viral_reason,
            },
            "platform": inputs.platform,
            "language": inputs.language,
            "fit": inputs.fit,
            "narrationMs": payload.narration_ms,
            "seo": payload.story.seo.model_dump() if payload.story.seo else None,
            "sections": [
                {
                    "id": record.element_id,
                    "startMs": record.start_ms,
                    "endMs": record.start_ms + record.duration_ms,
                    "clip": {
                        "asset": record.asset,
                        "filmStartMs": record.source_start_ms,
                        "durationMs": record.duration_ms,
                        "zoom": record.zoom,
                        "sceneRef": section.clip.scene_ref,
                        "sceneText": payload.scene_index.get(section.clip.scene_ref, ""),
                        "scene": section.clip.scene,
                        "textOverlay": section.clip.text_overlay,
                        "transition": section.clip.transition,
                    },
                }
                for record, section in zip(payload.clips, payload.story.sections)
            ],
        }
        reel_name = inputs.name or ctx.job.id
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        return StoryCompose(
            inputs=inputs,
            timeline=timeline,
            plan=plan,
            render_options={
                "outdir": str(outdir),
                "reel_name": reel_name,
                "workdir": str(settings.workdir / "render" / reel_name),
                "style_id": style.id,
                "audio_profile": inputs.audio_profile,
                "fit": inputs.fit,
                "width": width,
                "height": height,
                "narration_ms": payload.narration_ms,
                "credits": [
                    f"Footage: {payload.media.label()} (TMDB {payload.media.tmdb_id})",
                    f"Story: {payload.story.concept_name or payload.story.concept_id}",
                ],
            },
        )


class RenderStage:
    """Captions to ASS/SRT, one compose pass, one manifest."""

    id = "render"
    revision = 1
    cacheable = False
    output_model = StoryRenderOutput

    def run(self, ctx: StageContext, payload: StoryCompose) -> StoryRenderOutput:
        ctx.cancel.raise_if_cancelled()
        settings: Settings = ctx.config
        options = payload.render_options
        timeline = payload.timeline
        style = get_style(options["style_id"])
        width, height = options["width"], options["height"]
        styles = caption_styles(style, aspect=payload.inputs.aspect)

        outdir = Path(options["outdir"])
        workdir = Path(options["workdir"])
        outdir.mkdir(parents=True, exist_ok=True)
        workdir.mkdir(parents=True, exist_ok=True)
        reel_name = options["reel_name"]

        ass_path = write_ass(
            workdir / f"{reel_name}.ass",
            timeline.captions,
            styles=styles,
            width=width,
            height=height,
            title=reel_name,
        )
        voice_captions = [caption for caption in timeline.captions if caption.style_ref == "storyreel.voice"]
        srt_path = write_srt(voice_captions, workdir / f"{reel_name}.srt")

        clips = [ctx.assets.get(element.asset) for element in timeline.video]
        narration_ref = next(el for el in timeline.audio if getattr(el, "kind", "") == "timed_audio")
        narration = ctx.assets.get(narration_ref.asset)
        music_ref = next((el for el in timeline.audio if getattr(el, "kind", "") == "music"), None)
        music = ctx.assets.get(music_ref.asset) if music_ref is not None else None

        video = outdir / f"{reel_name}.mp4"
        render_storyreel(
            clips=clips,
            narration=narration,
            music=music,
            ass_path=ass_path,
            dest=video,
            width=width,
            height=height,
            fps=FPS,
            fit=options["fit"],
            profile=options["audio_profile"],
            duration_ms=options["narration_ms"],
            settings=settings,
            workdir=workdir,
            cancel=ctx.cancel,
        )
        created = datetime.now(timezone.utc).isoformat()
        manifest = {
            "reel": str(video),
            "recipe": "storyreel",
            "tmdbId": timeline.meta.get("tmdb"),
            "title": timeline.meta.get("title"),
            "concept": timeline.meta.get("concept"),
            "created": created,
            "durationMs": timeline.duration_ms,
            "aspect": payload.inputs.aspect,
            "audioProfile": options["audio_profile"],
            "sections": len(timeline.video),
            "captions": len(voice_captions),
            "words": sum(len(caption.words) for caption in voice_captions),
            "seo": payload.plan.get("seo"),
            "credits": options["credits"],
        }
        manifest_path = outdir / f"{reel_name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return StoryRenderOutput(
            video=str(video),
            ass=str(ass_path),
            srt=str(srt_path),
            manifest=str(manifest_path),
            duration_ms=timeline.duration_ms,
            render_mode="story",
        )
