"""The storyreel recipe: a film becomes a viral storytelling reel.

Steps, in the workflow's own words: pick a film (TMDB), pick the content angle,
language, platform and length; bring the subtitle script (downloaded, embedded in the
film, or supplied); read it; find the top 5 viral story concepts; choose one; write the
storytelling script with a micro-clip plan and an SEO package; speak it; cut many
short clips from across the film; render.

The default brief is the deployment's: a 5-minute Hindi narration over the film (a
5-10 minute explanatory reel is the intended range), spoken by the voice in
`REEL_STORY_VOICE`, captioned in Devanagari — all overridable per request.
The calling agent can be the brain (concepts and story passed in as inputs — used
exactly as given), or the `story` provider group can do the creative work.
"""

from __future__ import annotations

from typing import Any

from ...core.errors import InvalidInput
from ...core.manifest import CheckResult, VerificationReport
from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec, StageSpec
from ...core.stage import StageContext
from ...core.style import StyleNotFound, get_style
from ...tmdb import TmdbClient, TmdbError
from .models import StoryReelInputs
from .selection import resolve_style_id, resolve_voice, trending
from .stages import (
    AcquireStage,
    ComposeStage,
    ConceptsStage,
    PrepStage,
    RenderStage,
    ScriptStage,
    SubtitlesStage,
    TranscriptStage,
    VoiceoverStage,
    _audio_preference,
    _provider_of,
)
from .verify import verify_storyreel


class StoryReelRecipe:
    spec = RecipeSpec(
        id="storyreel",
        title="Movie storytelling reel",
        summary=(
            "A film or episode becomes a viral storytelling reel: the whole title is "
            "downloaded (vidvault), its subtitle script is read for the moments that "
            "hold an audience, the top 5 story concepts are found, one explanatory "
            "voiceover script is written with a micro-clip plan (2-5 s shots from "
            "across the film) and an SEO package, then spoken, cut and rendered "
            "vertical. The default brief is a 5-minute Hindi narration (the intended "
            "range is 5-10 minutes) with Devanagari captions; 30 s-30 min and any "
            "language work per request."
        ),
        input_model=StoryReelInputs,
        version=1,
        stages=(
            StageSpec(stage=AcquireStage(), uses=("source",)),
            StageSpec(stage=SubtitlesStage(), feeds="acquire", uses=("subtitles",)),
            StageSpec(stage=TranscriptStage(), feeds="subtitles"),
            StageSpec(stage=ConceptsStage(), feeds="transcript", uses=("brain",)),
            StageSpec(stage=ScriptStage(), feeds="concepts", uses=("brain",)),
            StageSpec(stage=VoiceoverStage(), feeds="script", uses=("narration",)),
            StageSpec(stage=PrepStage(), feeds="voiceover"),
            StageSpec(stage=ComposeStage(), feeds="prep"),
            StageSpec(stage=RenderStage(), feeds="compose"),
        ),
        providers={
            "source": ProviderReq(
                group="sources",
                default="vidvault",
                description="Downloads the whole film (vidvault keys on TMDB)",
            ),
            "subtitles": ProviderReq(
                group="subtitles",
                default="auto",
                description="The film's script: embedded first, then SubDL, then OpenSubtitles",
            ),
            "brain": ProviderReq(
                group="story",
                default="llm",
                required=False,
                description="Finds the viral concepts and writes the story (caller-supplied output wins)",
            ),
            "narration": ProviderReq(
                group="narration",
                default="fake",
                description="Text-to-speech with word timings",
            ),
        },
        cost=CostNote(
            unit="video_downloads",
            estimate="1 per film (cached afterwards)",
            notes=(
                "the film is downloaded once and re-cut for free; cloud voices bill per "
                "character; subtitle databases have daily download quotas"
            ),
            providers=("vidvault", "subdl", "opensubtitles", "elevenlabs", "cartesia"),
        ),
        artifacts=(
            "reel.mp4",
            "captions.ass",
            "captions.srt",
            "subtitles.srt",
            "transcript.json",
            "transcript.txt",
            "concepts.json",
            "story.json",
            "seo.json",
            "plan.json",
            "timeline.json",
            "manifest.json",
        ),
        render_modes=("story",),
        example={
            "title": "Inception",
            "media_type": "movie",
            "content_type": "ai-decide",
            "language": "hi",
            "platform": "tiktok",
            "target_ms": 300_000,
            "mode": "plan",
            "voice": "cartesia:<hindi-voice-id>",
        },
    )

    # ------------------------------------------------------------------- probe
    def probe(self, inputs: StoryReelInputs, ctx: ProbeContext) -> ProbeReport:
        checks: list[CheckResult] = []
        warnings: list[str] = []
        voice = resolve_voice(inputs.voice)
        style_id = resolve_style_id(inputs.style, inputs.language)
        details: dict[str, Any] = {
            "mode": inputs.mode,
            "language": inputs.language,
            "targetMs": inputs.target_ms,
            "voice": voice,
            "style": style_id,
        }

        try:
            style = get_style(style_id)
            checks.append(CheckResult(name="style", ok=True, detail=style.id))
        except StyleNotFound as exc:
            checks.append(CheckResult(name="style", ok=False, detail=str(exc)))

        provider = ctx.providers.get("narration") if ctx.providers else None
        bound = getattr(provider, "name", "")
        requested = _provider_of(voice)
        checks.append(
            CheckResult(
                name="voice",
                ok=not bound or requested == bound,
                detail=f"{voice} via {bound or '?'}",
            )
        )

        needs_brain = False
        if inputs.mode != "transcript" and not inputs.story:
            if not inputs.concepts:
                needs_brain = True
            elif inputs.mode == "build" and not inputs.concept:
                needs_brain = True
        if needs_brain and ctx.providers is not None:
            brain = ctx.providers.get("brain")
            problems = getattr(brain, "missing", lambda: [])()
            checks.append(
                CheckResult(
                    name="story_brain",
                    ok=not problems,
                    detail="; ".join(problems) or getattr(brain, "name", "ready"),
                )
            )
        else:
            checks.append(
                CheckResult(name="story_brain", ok=True, detail="creative work supplied by the caller")
            )

        if inputs.subtitle_path:
            from pathlib import Path

            path = Path(inputs.subtitle_path).expanduser()
            checks.append(CheckResult(name="subtitle_file", ok=path.is_file(), detail=str(path)))
        elif ctx.providers is not None:
            subtitles = ctx.providers.get("subtitles")
            problems = getattr(subtitles, "missing", lambda: [])()
            checks.append(
                CheckResult(
                    name="subtitles",
                    ok=not problems,
                    detail="; ".join(problems) or getattr(subtitles, "name", "ready"),
                )
            )

        media = None
        try:
            client = TmdbClient(settings=ctx.config)
            if inputs.tmdb_id:
                detail = client.detail(inputs.tmdb_id, inputs.media_type)
                media = (detail.tmdb_id, inputs.media_type, inputs.season)
                details["title"] = {
                    "tmdbId": detail.tmdb_id,
                    "mediaType": detail.media_type,
                    "title": detail.title,
                    "year": detail.year,
                    "originalLanguage": detail.original_language,
                    "runtimeMin": detail.runtime_min,
                    "seasons": detail.seasons,
                }
                checks.append(
                    CheckResult(name="title", ok=True, detail=f"{detail.title} ({detail.year or '?'})")
                )
            elif inputs.title:
                found = client.find_title(
                    inputs.title, media_type=inputs.media_type, year=inputs.year, limit=8
                )
                if found:
                    media = (found[0].tmdb_id, found[0].media_type, inputs.season)
                details["candidates"] = [candidate.as_dict() for candidate in found]
                checks.append(
                    CheckResult(
                        name="title_match",
                        ok=bool(found),
                        detail=f"{len(found)} candidate(s)",
                    )
                )
                if not found:
                    warnings.append("no TMDB match; try the original title or add a year")
            else:
                details["trending"] = trending(client, months=12, limit=6)
                checks.append(
                    CheckResult(
                        name="trending",
                        ok=True,
                        detail="popular films and series from the last 12 months",
                    )
                )
        except TmdbError as exc:
            checks.append(CheckResult(name="tmdb", ok=False, detail=str(exc)))

        if media is not None and ctx.providers is not None:
            source = ctx.providers.get("source")
            try:
                asset = source.resolve(
                    None,
                    inputs.episode,
                    tmdb_id=media[0],
                    media_type=media[1],
                    season=media[2],
                    audio_pref=_audio_preference(""),
                    download=False,
                )
                streams = asset.meta.get("allStreams") or []
                details["availability"] = {
                    "label": asset.label,
                    "resolution": asset.meta.get("resolution"),
                    "sizeBytes": asset.meta.get("sizeBytes"),
                    "warning": asset.meta.get("warning", ""),
                    "streams": streams[:6],
                }
                checks.append(CheckResult(name="source", ok=True, detail=asset.label))
                if asset.meta.get("warning"):
                    warnings.append(str(asset.meta["warning"]))
            except Exception as exc:  # noqa: BLE001 - probe reports, never raises
                checks.append(
                    CheckResult(name="source", ok=False, detail=str(exc)[:240])
                )

        return ProbeReport(
            ok=all(check.ok for check in checks),
            checks=checks,
            warnings=warnings,
            details=details,
            quota_free=True,
        )

    # ------------------------------------------------------------------ verify
    def verify(self, ctx: StageContext, outputs: dict[str, Any]) -> VerificationReport:
        return verify_storyreel(ctx, outputs)
