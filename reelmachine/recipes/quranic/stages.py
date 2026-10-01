"""quranic stages: select → prep → compose → render.

No source media and no alignment anywhere in this file: the corpus serves text, audio and
timings, and the recipe turns them into a timeline.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings, normalise_aspect
from ...core.assets import AssetKind, Provenance
from ...core.errors import InvalidInput
from ...core.stage import StageContext
from ...core.style import StylePack, get_style
from ...core.timeline import Caption, Clip, Timeline, TimedAudioRef
from ...render.captions import write_ass, write_srt
from .models import (
    AyahAudio,
    FileBackground,
    QuranCompose,
    QuranicInputs,
    QuranPrep,
    QuranRenderOutput,
    QuranSelection,
)
from .render import caption_styles, render_quranic
from .timings import timings_for_ayah

GEOMETRY = {
    "vertical": (1080, 1920),
    "square": (1080, 1080),
    "landscape": (1920, 1080),
}

#: Provider timings further past the probed audio than this are not trusted.
TIMING_TOLERANCE_MS = 500


def _hex(value: str) -> str:
    text = (value or "").strip().lstrip("#")
    return f"0x{text}" if len(text) == 6 else "0x101820"


def _suffix(url: str) -> str:
    for candidate in (".mp3", ".m4a", ".wav", ".ogg", ".opus"):
        if candidate in url:
            return candidate
    return ".mp3"


class SelectStage:
    """Fetch the verse text, translation, recitation audio and timings."""

    id = "select"
    revision = 1
    cacheable = True
    cache_fields = ("surah", "ayah_start", "ayah_end", "corpus", "reciter", "translation")
    output_model = QuranSelection

    def run(self, ctx: StageContext, payload: Any) -> QuranSelection:
        inputs = QuranicInputs.model_validate(payload)
        ctx.cancel.raise_if_cancelled()
        corpus = ctx.providers.get("corpus")
        selection = corpus.fetch(inputs)
        ctx.progress.detail(f"  · {len(selection.ayahs)} ayah(s) via {corpus.name}")
        return selection


class PrepStage:
    """Download the recitation, join it, time the words, materialise the background."""

    id = "prep"
    revision = 1
    cacheable = False
    output_model = QuranPrep

    def run(self, ctx: StageContext, payload: QuranSelection) -> QuranPrep:
        ctx.cancel.raise_if_cancelled()
        inputs = payload.inputs
        settings: Settings = ctx.config
        corpus = ctx.providers.get("corpus")
        style = get_style(inputs.style)

        audio_dir = ctx.workdir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        pieces: list[AyahAudio] = []
        paths: list[Path] = []
        for ayah in payload.ayahs:
            ctx.cancel.raise_if_cancelled()
            dest = audio_dir / f"ayah-{ayah.ayah:03d}{_suffix(ayah.audio_url)}"
            corpus.fetch_audio(ayah.audio_url, dest)
            info = ffmpeg.probe(dest)
            duration_ms = int(info.duration_s * 1000)
            if duration_ms <= 0:
                raise InvalidInput(
                    f"the recitation for {ayah.key} has no measurable duration",
                    hint="check the corpus provider's audio URL",
                )
            pieces.append(AyahAudio(ayah=ayah.ayah, asset="", duration_ms=duration_ms, source_url=ayah.audio_url))
            paths.append(dest)
            ctx.progress.detail(f"  · {ayah.key}: {duration_ms / 1000:.1f}s of recitation")

        joined = ctx.workdir / "recitation.wav"
        if len(paths) == 1:
            shutil.copy2(paths[0], joined)
        else:
            ffmpeg.concat_audio(paths, joined, cancel=ctx.cancel)
        total_ms = int(ffmpeg.probe(joined).duration_s * 1000)

        audio_asset = ctx.assets.put(
            joined,
            kind=AssetKind.NARRATION,
            provenance=payload.provenance,
            licence=payload.licence,
            ext=".wav",
            meta={"reciter": payload.reciter_name},
        )

        windows: list[tuple[int, int, int]] = []
        words = []
        provider_hits = 0
        cursor = 0
        for ayah, piece in zip(payload.ayahs, pieces):
            start, end = cursor, cursor + piece.duration_ms
            local_end = max((span.end_ms for span in ayah.word_timings), default=0)
            if local_end > piece.duration_ms + TIMING_TOLERANCE_MS:
                # the provider's timings describe a longer file than this clip: refall back
                ayah = ayah.model_copy(update={"word_timings": []})
            spans, used_provider = timings_for_ayah(ayah, start, end)
            provider_hits += 1 if used_provider else 0
            words.extend(spans)
            windows.append((ayah.ayah, start, end))
            cursor = end

        background_asset, origin = _materialise_background(
            ctx, inputs, style, duration_ms=total_ms
        )

        if provider_hits == len(pieces):
            timings_source = "provider"
        elif provider_hits:
            timings_source = "mixed"
        else:
            timings_source = "proportional"
        ctx.progress.detail(f"  · word timings: {timings_source}")
        return QuranPrep(
            inputs=inputs,
            style_id=style.id,
            audio_asset=audio_asset.id,
            audio_duration_ms=total_ms,
            background_asset=background_asset.id,
            background_origin=origin,
            ayah_windows=windows,
            words=words,
            ayahs=payload.ayahs,
            audio=pieces,
            timings_source=timings_source,
        )


def _materialise_background(
    ctx: StageContext,
    inputs: QuranicInputs,
    style: StylePack,
    *,
    duration_ms: int,
) -> tuple[Any, str]:
    settings: Settings = ctx.config
    width, height = GEOMETRY[inputs.aspect]
    out = ctx.workdir / "background.mp4"
    duration_s = max(0.5, duration_ms / 1000.0)

    if isinstance(inputs.background, FileBackground):
        source = Path(inputs.background.path).expanduser()
        if not source.is_file():
            raise InvalidInput(
                f"background file not found: {source}",
                hint="points at a video or image the caller supplies",
            )
        cmd = [
            settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-stream_loop", "-1", "-i", str(source),
            "-t", f"{duration_s:.3f}", "-an",
            "-vf", (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps=30"
            ),
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", str(out),
        ]
        ffmpeg.run(cmd, cancel=ctx.cancel)
        origin = "file"
    else:
        colors = inputs.background.colors or [
            str((style.palette or {}).get("background", "#0e1621")),
            str((style.palette or {}).get("highlight", "#1f3b4d")),
        ]
        first = colors[0] if colors else "#0e1621"
        second = colors[1] if len(colors) > 1 else first
        cmd = [
            settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi",
            "-i", (
                f"gradients=s={width}x{height}:c0={_hex(first)}:c1={_hex(second)}:"
                f"speed={inputs.background.speed}"
            ),
            "-t", f"{duration_s:.3f}", "-r", "30",
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", str(out),
        ]
        try:
            ffmpeg.run(cmd, cancel=ctx.cancel)
        except ffmpeg.FFmpegError as exc:
            raise InvalidInput(
                "this ffmpeg cannot generate the procedural gradient background",
                hint="use background kind=file, or upgrade ffmpeg (the gradients source needs 4.4+)",
                details={"stderr": str(exc)[-300:]},
            ) from exc
        origin = "gradient"

    asset = ctx.assets.put(
        out,
        kind=AssetKind.BACKGROUND,
        provenance=Provenance(provider="reelmachine", source=origin),
        ext=".mp4",
    )
    return asset, origin


class ComposeStage:
    """Build the timeline: background + recitation + highlight captions."""

    id = "compose"
    revision = 1
    cacheable = False
    output_model = QuranCompose

    def run(self, ctx: StageContext, payload: QuranPrep) -> QuranCompose:
        inputs = payload.inputs
        settings: Settings = ctx.config
        style = get_style(payload.style_id)
        aspect = normalise_aspect(inputs.aspect)
        timeline_aspect = "landscape" if aspect == "original" else aspect

        captions: list[Caption] = []
        for (ayah_number, start, end), ayah in zip(payload.ayah_windows, payload.ayahs):
            spans = [word for word in payload.words if start <= word.start_ms < end]
            captions.append(
                Caption(
                    start_ms=start,
                    end_ms=end,
                    text=ayah.text,
                    lang="ar",
                    style_ref="quranic.arabic",
                    words=spans if inputs.word_highlight else [],
                    meta={"source": ayah.key},
                )
            )
            if inputs.show_translation and ayah.translation:
                captions.append(
                    Caption(
                        start_ms=start,
                        end_ms=end,
                        text=ayah.translation,
                        lang="en",
                        style_ref="quranic.translation",
                        meta={"source": ayah.key},
                    )
                )

        timeline = Timeline(
            duration_ms=payload.audio_duration_ms,
            aspect=timeline_aspect,  # type: ignore[arg-type]
            render_mode="quranic",
            style=style.id,
            video=[
                Clip(
                    element_id="background",
                    asset=payload.background_asset,
                    start_ms=0,
                    duration_ms=payload.audio_duration_ms,
                    origin="background",
                )
            ],
            audio=[
                TimedAudioRef(
                    element_id="recitation",
                    asset=payload.audio_asset,
                    duration_ms=payload.audio_duration_ms,
                    role="recitation",
                )
            ],
            captions=captions,
            meta={
                "surah": inputs.surah,
                "ayahs": [ayah.key for ayah in payload.ayahs],
                "reciter": payload.ayahs[0].meta.get("reciter") if payload.ayahs else None,
                "background": payload.background_origin,
            },
        )
        reel_name = inputs.name or ctx.job.id
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        return QuranCompose(
            inputs=inputs,
            timeline=timeline,
            render_options={
                "outdir": str(outdir),
                "reel_name": reel_name,
                "workdir": str(settings.workdir / "render" / reel_name),
                "audio_profile": inputs.audio_profile,
                "style_id": style.id,
                "aspect": aspect,
                "credits": [
                    f"Quran text: public domain",
                    f"Recitation: {inputs.reciter}",
                    f"Translation: {inputs.translation}",
                ],
            },
        )


class RenderStage:
    """Captions to ASS/SRT, one ffmpeg pass, one manifest."""

    id = "render"
    revision = 1
    cacheable = False
    output_model = QuranRenderOutput

    def run(self, ctx: StageContext, payload: QuranCompose) -> QuranRenderOutput:
        ctx.cancel.raise_if_cancelled()
        settings: Settings = ctx.config
        options = payload.render_options
        timeline = payload.timeline
        style = get_style(options["style_id"])
        width, height = GEOMETRY[payload.inputs.aspect]
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
        srt_path = write_srt(timeline.captions, workdir / f"{reel_name}.srt")

        background = ctx.assets.get(timeline.video[0].asset)
        audio = ctx.assets.get(timeline.audio[0].asset)  # type: ignore[union-attr]
        video = outdir / f"{reel_name}.mp4"
        render_quranic(
            background=background,
            audio=audio,
            ass_path=ass_path,
            dest=video,
            profile=options["audio_profile"],
            settings=settings,
            cancel=ctx.cancel,
        )

        created = datetime.now(timezone.utc).isoformat()
        manifest = {
            "reel": str(video),
            "recipe": "quranic",
            "surah": payload.inputs.surah,
            "ayahs": list(timeline.meta.get("ayahs", [])),
            "created": created,
            "durationMs": timeline.duration_ms,
            "aspect": payload.inputs.aspect,
            "audioProfile": options["audio_profile"],
            "captions": len([caption for caption in timeline.captions if caption.lang == "ar"]),
            "words": sum(len(caption.words) for caption in timeline.captions),
            "credits": options["credits"],
        }
        manifest_path = outdir / f"{reel_name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        return QuranRenderOutput(
            video=str(video),
            ass=str(ass_path),
            srt=str(srt_path),
            manifest=str(manifest_path),
            duration_ms=timeline.duration_ms,
            render_mode="quranic",
        )
