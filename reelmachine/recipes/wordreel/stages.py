"""The wordreel stages: select → scan → prep → render.

Splitting search from cutting is the whole point of the shape. `scan` reads subtitles
and writes a shortlist; `prep` is the first stage that touches video. A caller can stop
after `scan` (mode `scan` or `plan`) and see exactly which lines would be used, from
which files, at which timestamps — before a single frame is decoded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings, normalise_aspect
from ...core.assets import AssetKind, Provenance
from ...core.errors import InvalidInput, RenderFailed
from ...core.stage import StageContext
from ...core.timeline import Caption, Clip, Timeline, timeline_digest
from ...render.captions import EMPHASIS_KEY, write_ass, write_srt
from . import matching
from .clips import ClipRequest
from .models import (
    ClipRecord,
    PrepOutput,
    RenderOutput,
    ScanOutput,
    SelectOutput,
    WordReelInputs,
    element_id,
)
from .render import (
    CLIP_HEIGHT,
    FPS,
    GEOMETRY,
    GLOSS_MS,
    GLOSS_STYLE,
    LINE_STYLE,
    WORD_STYLE,
    caption_styles,
    render_reel,
)

#: How many words `suggest` offers when the caller does not say.
DEFAULT_SUGGESTIONS = 12


def _request(inputs: WordReelInputs, settings: Settings) -> ClipRequest:
    """The input model, expressed as one search request."""
    return ClipRequest(
        word=inputs.normalised_word(),
        match_mode=inputs.match_mode,
        allow_inflected=inputs.allow_inflected,
        languages=inputs.request_languages(),
        library=list(inputs.library),
        subtitles=list(inputs.subtitles),
        min_clip_ms=inputs.min_clip_ms or settings.min_clip_ms,
        max_clip_ms=inputs.max_clip_ms or settings.max_clip_ms,
        per_source=max(1, inputs.per_source),
    )


def _aspect(inputs: WordReelInputs, settings: Settings) -> str:
    chosen = normalise_aspect(inputs.aspect or settings.aspect)
    return "landscape" if chosen == "original" else chosen


class SelectStage:
    """Resolve the target word, or propose ones the corpus can actually teach."""

    id = "select"
    revision = 1
    cacheable = True
    #: This stage's payload *is* the input model, so the fields are named plainly —
    #: `inputs.word` would resolve to nothing here and key every request alike, which
    #: would serve one request's word to the next caller.
    cache_fields = (
        "word",
        "mode",
        "match_mode",
        "allow_inflected",
        "library",
        "subtitles",
        "languages",
    )
    output_model = SelectOutput

    def run(self, ctx: StageContext, payload: Any) -> SelectOutput:
        inputs = WordReelInputs.model_validate(payload)
        corpus = ctx.providers.get("corpus")
        settings: Settings = ctx.config
        word = inputs.normalised_word()
        suggestions: list[str] = []

        if not word:
            if inputs.mode != "suggest":
                raise InvalidInput(
                    "no word given",
                    hint=(
                        "pass `word` (a word or a phrase), or run mode='suggest' first "
                        "to see what this library can teach"
                    ),
                )
            suggestions = list(
                corpus.suggest(_request(inputs, settings), limit=DEFAULT_SUGGESTIONS)
            )
            ctx.progress.detail(f"  · {len(suggestions)} candidate word(s)")
            if not suggestions:
                raise InvalidInput(
                    "the corpus offered no words to teach",
                    hint=(
                        "point `library` (or REEL_WORDREEL_LIBRARY) at a directory of "
                        "media with subtitles beside it, or pass `subtitles` directly"
                    ),
                    details={"directories": list(inputs.library)},
                )

        return SelectOutput(
            inputs=inputs,
            word=word,
            corpus=getattr(corpus, "name", "") or "",
            directories=list(inputs.library),
            suggestions=suggestions,
            stats={"requested": bool(word), "suggestions": len(suggestions)},
            # `suggest` is a question, not a render: answer it and stop.
            halt_pipeline=not word,
        )


class ScanStage:
    """Search the corpus for the word; write the shortlist and stop if asked."""

    id = "scan"
    revision = 1
    cacheable = True
    cache_fields = (
        "inputs.word",
        "inputs.mode",
        "inputs.match_mode",
        "inputs.allow_inflected",
        "inputs.library",
        "inputs.subtitles",
        "inputs.languages",
        "inputs.count",
        "inputs.per_source",
    )
    output_model = ScanOutput

    def run(self, ctx: StageContext, payload: SelectOutput) -> ScanOutput:
        ctx.cancel.raise_if_cancelled()
        inputs = payload.inputs
        settings: Settings = ctx.config
        corpus = ctx.providers.get("corpus")
        word = payload.word
        ctx.progress.detail(f"  · searching {word!r} in {getattr(corpus, 'name', 'corpus')}")

        found = list(corpus.find(_request(inputs, settings), limit=max(1, inputs.count)))

        usable = [item for item in found if item.media_path and Path(item.media_path).is_file()]
        skipped = [
            {
                "text": item.text,
                "media": item.media_label,
                "reason": "no footage behind this line" if not item.media_path else "file is gone",
            }
            for item in found
            if item not in usable
        ]
        if not found:
            raise InvalidInput(
                f"no line in this corpus uses {word!r}",
                hint=(
                    "try match_mode='smart' to accept inflected forms, a more common word, "
                    "or add directories to `library`"
                ),
                details={"word": word, "corpus": getattr(corpus, "name", "")},
            )
        ctx.progress.detail(f"  · {len(found)} match(es), {len(usable)} with footage")

        plan = {
            "word": word,
            "corpus": payload.corpus,
            "matchMode": inputs.match_mode,
            "count": len(found),
            "usable": len(usable),
            "examples": [item.to_dict() for item in found],
        }
        # the filename is the interface: the engine delivers `<stage>/artifacts/*`
        artifacts = ctx.workdir / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "plan.json").write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        stopped = inputs.mode in ("scan", "plan")
        if stopped and not usable:
            raise InvalidInput(
                f"{word!r} matched {len(found)} line(s) but none has footage behind it",
                hint="a scan needs subtitles; a build needs the media those subtitles came from",
                details={"word": word},
            )
        return ScanOutput(
            inputs=inputs,
            word=word,
            corpus=payload.corpus,
            candidates=usable if not stopped else found,
            skipped=skipped,
            plan=plan,
            stats={
                "matches": len(found),
                "usable": len(usable),
                "skipped": len(skipped),
                "sources": len({item.media_label for item in found}),
            },
            halt_pipeline=stopped,
        )


class PrepStage:
    """Cut every example out of its own file and lay the reel out."""

    id = "prep"
    revision = 1
    cacheable = False
    output_model = PrepOutput

    def run(self, ctx: StageContext, payload: ScanOutput) -> PrepOutput:
        settings: Settings = ctx.config
        inputs = payload.inputs
        aspect = _aspect(inputs, settings)
        workdir = settings.workdir / "render" / (inputs.name or ctx.job.id)
        workdir.mkdir(parents=True, exist_ok=True)
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        outdir.mkdir(parents=True, exist_ok=True)

        if not payload.candidates:
            raise RenderFailed(
                "nothing to render: no example had footage behind it",
                hint="run mode='scan' to see which lines matched and where they came from",
                stage=self.id,
            )

        records: list[ClipRecord] = []
        skipped: list[dict[str, Any]] = list(payload.skipped)
        cursor_ms = 0
        total = len(payload.candidates)
        for index, candidate in enumerate(payload.candidates, start=1):
            ctx.cancel.raise_if_cancelled()
            source = Path(candidate.media_path)
            duration_ms = candidate.duration_ms
            if not source.is_file():
                skipped.append(
                    {"text": candidate.text, "media": candidate.media_label, "reason": "file is gone"}
                )
                continue
            if duration_ms < 250:
                skipped.append(
                    {
                        "text": candidate.text,
                        "media": candidate.media_label,
                        "reason": f"only {duration_ms} ms of dialogue",
                    }
                )
                continue

            dest = workdir / f"example-{index:03d}.mp4"
            try:
                ffmpeg.cut_clip(
                    source,
                    dest,
                    start_s=candidate.start_ms / 1000.0,
                    duration_s=duration_ms / 1000.0,
                    height=CLIP_HEIGHT,
                    fps=FPS,
                    cancel=ctx.cancel,
                )
            except ffmpeg.FFmpegError as exc:
                skipped.append(
                    {
                        "text": candidate.text,
                        "media": candidate.media_label,
                        "reason": f"the cut failed: {str(exc)[:160]}",
                    }
                )
                continue

            asset = ctx.assets.put(
                dest,
                kind=AssetKind.SOURCE_CUT,
                provenance=Provenance(
                    provider=payload.corpus or "library",
                    source=f"{candidate.media_path}#t={candidate.start_ms}",
                    notes=candidate.subtitle_source,
                ),
                ext=".mp4",
                meta={
                    "startMs": candidate.start_ms,
                    "endMs": candidate.end_ms,
                    "line": candidate.text,
                    "matched": candidate.match_surface,
                },
            )
            records.append(
                ClipRecord(
                    index=index,
                    element_id=element_id(candidate, index),
                    candidate=candidate,
                    clip=str(dest),
                    clip_asset=asset.id,
                    start_ms=cursor_ms,
                    duration_ms=duration_ms,
                    words=matching.word_spans(candidate.text, cursor_ms, cursor_ms + duration_ms),
                    emphasis=candidate.match_surface or payload.word,
                )
            )
            cursor_ms += duration_ms
            ctx.progress.detail(
                f"  · clip {len(records)}/{total}: {candidate.media_label} "
                f"@ {candidate.start_ms / 1000:.1f}s — {candidate.text[:58]}"
            )
            ctx.progress.percent(100 * index / total)

        if not records:
            raise RenderFailed(
                "every example failed to cut",
                hint="check that the files in `library` are readable video and that ffmpeg is on PATH",
                stage=self.id,
                details={"skipped": skipped[:10]},
            )

        timeline = _build_timeline(records, word=payload.word, aspect=aspect, gloss=inputs.gloss,
                                   include_card=inputs.include_card)
        return PrepOutput(
            timeline=timeline,
            word=payload.word,
            clips=records,
            duration_ms=cursor_ms,
            skipped=skipped,
            render_options={
                "word": payload.word,
                "gloss": inputs.gloss,
                "corpus": payload.corpus,
                "aspect": aspect,
                "watermark": inputs.watermark,
                "outdir": str(outdir),
                "workdir": str(workdir),
                "reel_name": inputs.name or ctx.job.id,
                "stats": payload.stats,
                "sources": sorted({record.candidate.media_label for record in records}),
            },
        )


def _build_timeline(
    records: list[ClipRecord],
    *,
    word: str,
    aspect: str,
    gloss: str,
    include_card: bool,
) -> Timeline:
    """The reel: every cut, plus the three caption layers over it."""
    video = [
        Clip(
            element_id=record.element_id,
            asset=record.clip_asset,
            start_ms=record.start_ms,
            duration_ms=record.duration_ms,
            origin="cut",
            keep_audio=True,   # the dialogue *is* the lesson; never mute it
        )
        for record in records
    ]
    total_ms = sum(record.duration_ms for record in records)

    captions: list[Caption] = []
    if include_card:
        captions.append(
            Caption(
                start_ms=0,
                end_ms=total_ms,
                text=word,
                lang="en",
                style_ref=WORD_STYLE,
                meta={"role": "word"},
            )
        )
        if gloss:
            captions.append(
                Caption(
                    start_ms=0,
                    end_ms=min(total_ms, GLOSS_MS),
                    text=gloss,
                    lang="en",
                    style_ref=GLOSS_STYLE,
                    meta={"role": "gloss"},
                )
            )
    for record in records:
        captions.append(
            Caption(
                start_ms=record.start_ms,
                end_ms=record.start_ms + record.duration_ms,
                text=record.candidate.text,
                lang="en",
                style_ref=LINE_STYLE,
                words=record.words,
                meta={
                    EMPHASIS_KEY: record.emphasis,
                    "source": record.candidate.media_label,
                    "line": record.candidate.text,
                },
            )
        )

    return Timeline(
        duration_ms=total_ms,
        aspect=aspect,  # type: ignore[arg-type]  (normalised by _aspect)
        render_mode="wordreel",
        video=video,
        captions=captions,
        meta={
            "word": word,
            "gloss": gloss,
            "clips": len(records),
            "examples": [record.candidate.to_dict() for record in records],
        },
    )


class RenderStage:
    """Captions to ASS/SRT, one ffmpeg pass, one manifest."""

    id = "render"
    revision = 1
    cacheable = False
    output_model = RenderOutput

    def run(self, ctx: StageContext, payload: PrepOutput) -> RenderOutput:
        ctx.cancel.raise_if_cancelled()
        settings: Settings = ctx.config
        options = payload.render_options
        aspect = str(options["aspect"])
        width, height = GEOMETRY.get(aspect, GEOMETRY["vertical"])
        styles = caption_styles(settings, aspect=aspect)
        outdir = Path(options["outdir"])
        workdir = Path(options["workdir"])
        reel_name = str(options["reel_name"])

        ass_path = write_ass(
            workdir / f"{reel_name}.ass",
            payload.timeline.captions,
            styles=styles,
            width=width,
            height=height,
            title=reel_name,
        )
        spoken = [c for c in payload.timeline.captions if c.style_ref == LINE_STYLE]
        srt_path = write_srt(spoken, workdir / f"{reel_name}.srt")

        clips = [Path(record.clip) for record in payload.clips]
        video = outdir / f"{reel_name}.mp4"
        render_reel(
            clips,
            video,
            ass_path=ass_path,
            aspect=aspect,
            settings=settings,
            cancel=ctx.cancel,
        )

        manifest_path = workdir / f"{reel_name}.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "recipe": "wordreel",
                    "reel": str(video),
                    "word": payload.word,
                    "gloss": options.get("gloss", ""),
                    "aspect": aspect,
                    "durationMs": payload.duration_ms,
                    "clips": len(payload.clips),
                    "sources": options.get("sources", []),
                    "stats": options.get("stats", {}),
                    "skipped": payload.skipped,
                    "examples": [
                        {
                            **record.candidate.to_dict(),
                            "index": record.index,
                            "reelStartMs": record.start_ms,
                        }
                        for record in payload.clips
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return RenderOutput(
            video=str(video),
            ass=str(ass_path),
            srt=str(srt_path),
            manifest=str(manifest_path),
            duration_ms=payload.duration_ms,
            render_mode="wordreel",
            timeline_digest=timeline_digest(payload.timeline),
        )


def ffmpeg_available(settings: Settings) -> bool:
    import shutil

    return shutil.which(settings.ffmpeg) is not None
