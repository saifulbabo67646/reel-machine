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
from ...core.assets import AssetKind, Licence, Provenance
from ...core.errors import InvalidInput, ProviderUnavailable
from ...core.stage import StageContext
from ...core.style import StylePack, get_style
from ...core.timeline import Caption, Clip, Timeline, TimedAudioRef
from ...render.captions import write_ass, write_srt
from .backgrounds import BackgroundRequest
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

        background_asset, origin, background_windows = _materialise_backgrounds(
            ctx, inputs, style, duration_ms=total_ms, ayah_windows=windows
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
            backgrounds=background_windows,
            ayah_windows=windows,
            words=words,
            ayahs=payload.ayahs,
            audio=pieces,
            timings_source=timings_source,
        )


def background_choices(inputs: QuranicInputs) -> list[Any]:
    """`backgrounds` (a sequence) wins over `background` (a single clip)."""
    return list(inputs.backgrounds) if inputs.backgrounds else [inputs.background]


def background_slices(total_ms: int, count: int) -> list[tuple[int, int]]:
    """Split the reel evenly across `count` clips; the last one takes the remainder."""
    bounds = [int(round(total_ms * index / count)) for index in range(count + 1)]
    return [(bounds[index], bounds[index + 1]) for index in range(count)]


def scope_problem(choices: list[Any], ayah_range: tuple[int, int]) -> str:
    """Why the ayah scopes don't tile `ayah_range` exactly, or "" when they do.

    A caller matching backgrounds to meanings needs the mapping to be unambiguous: every
    ayah covered once, in order, with no gaps and nothing claimed twice.
    """
    scopes = [choice.covers() for choice in choices]
    if all(scope is None for scope in scopes):
        return ""
    if any(scope is None for scope in scopes):
        return "either scope every background with ayah_start/ayah_end, or none of them"
    start, end = ayah_range
    wanted = list(range(start, end + 1))
    claimed: list[int] = []
    for scope in scopes:
        lo, hi = scope  # type: ignore[misc]
        claimed.extend(range(lo, hi + 1))
    missing = [ayah for ayah in wanted if ayah not in claimed]
    extra = [ayah for ayah in claimed if ayah not in wanted]
    repeated = sorted({ayah for ayah in claimed if claimed.count(ayah) > 1})
    if missing:
        return f"no background for ayah(s) {missing}"
    if extra:
        return f"background(s) outside the selected range: {extra}"
    if repeated:
        return f"ayah(s) claimed by more than one background: {repeated}"
    if claimed and claimed != sorted(claimed):
        return "backgrounds must follow the ayah order"
    return ""


def scoped_slices(
    choices: list[Any], ayah_windows: list[tuple[int, int, int]]
) -> list[tuple[int, int]]:
    """One clip's time span per choice, taken from the ayahs it covers."""
    by_ayah = {ayah: (start, end) for ayah, start, end in ayah_windows}
    spans: list[tuple[int, int]] = []
    for choice in choices:
        lo, hi = choice.covers()  # type: ignore[misc]
        covered = [by_ayah[ayah] for ayah in range(lo, hi + 1) if ayah in by_ayah]
        if not covered:
            raise InvalidInput(
                f"no recitation for ayah(s) {lo}-{hi} to hang a background on",
                hint="check the ayah range against what the corpus returned",
            )
        spans.append((min(start for start, _ in covered), max(end for _, end in covered)))
    return spans


def _slice_clip(
    ctx: StageContext,
    choice: Any,
    style: StylePack,
    *,
    width: int,
    height: int,
    start_ms: int,
    end_ms: int,
) -> tuple[Path, Provenance, Licence | None, str]:
    """Fetch (or take) one clip and normalise it to the reel's geometry and its slice."""
    settings: Settings = ctx.config
    duration_ms = max(200, end_ms - start_ms)
    duration_s = duration_ms / 1000.0
    if isinstance(choice, FileBackground):
        source = Path(choice.path).expanduser()
        if not source.is_file():
            raise InvalidInput(
                f"background file not found: {source}",
                hint="points at a video or image the caller supplies",
            )
        origin, provenance, licence = "file", Provenance(provider="tenant", source="upload"), None
    else:
        origin = choice.kind
        provider = ctx.providers.get(f"background_{origin}")
        problems = provider.missing()
        if problems:
            raise ProviderUnavailable(
                f"the {origin} background provider is not usable on this deployment",
                hint="; ".join(problems),
                details={"kind": origin},
            )
        colors = list(getattr(choice, "colors", []) or [])
        if origin == "gradient" and not colors:
            colors = [
                str((style.palette or {}).get("background", "#0e1621")),
                str((style.palette or {}).get("highlight", "#1f3b4d")),
            ]
        clip = provider.fetch(
            BackgroundRequest(
                kind=origin,
                query=str(getattr(choice, "query", "") or ""),
                media=str(getattr(choice, "media", "video") or "video"),
                orientation=str(getattr(choice, "orientation", "portrait") or "portrait"),
                min_height=int(getattr(choice, "min_height", 720) or 720),
                colors=colors,
                speed=float(getattr(choice, "speed", 0.02) or 0.02),
                duration_ms=duration_ms,
                width=width,
                height=height,
            ),
            dest_dir=ctx.workdir,
        )
        source, provenance, licence = clip.path, clip.provenance, clip.licence
        still = clip.still
        face_check = clip.face_check
        ctx.progress.detail(
            f"  · background {origin}{' photo' if still else ' clip'}: {clip.width}x{clip.height} "
            f"{clip.duration_ms / 1000:.1f}s → {duration_s:.1f}s on screen"
        )
    out = ctx.workdir / f"background-{start_ms:07d}.mp4"
    if isinstance(choice, FileBackground):
        still = source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"}
        provenance_extra: dict[str, Any] = {}
    else:
        provenance_extra = {"faceCheck": face_check}
    if isinstance(choice, FileBackground):
        media = "photo" if still else "video"
    else:
        media = str(getattr(choice, "media", "video") or "video")
        media = "photo" if still else ("video" if media == "auto" else media)
    if still:
        # a still needs the slow push-in that keeps a hook shot alive
        vf = (
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan=z='min(zoom+0.0008,1.3)':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height},fps=30"
        )
        ffmpeg.run(
            [
                settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
                "-loop", "1", "-framerate", "30", "-i", str(source),
                "-t", f"{duration_s:.3f}", "-an", "-vf", vf,
                "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
                "-pix_fmt", "yuv420p", str(out),
            ],
            cancel=ctx.cancel,
        )
        return out, provenance, licence, origin, {"media": media, **provenance_extra}
    ffmpeg.run(
        [
            settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-stream_loop", "-1", "-i", str(source),
            "-t", f"{duration_s:.3f}", "-an",
            "-vf", (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps=30"
            ),
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", str(out),
        ],
        cancel=ctx.cancel,
    )
    return out, provenance, licence, origin, {"media": media, **provenance_extra}


def _materialise_backgrounds(
    ctx: StageContext,
    inputs: QuranicInputs,
    style: StylePack,
    *,
    duration_ms: int,
    ayah_windows: list[tuple[int, int, int]] = (),
) -> tuple[Any, str, list[tuple[str, int, int]]]:
    """The reel's background: every chosen clip, each covering its own slice.

    The provider owns where a clip comes from (and its licence); this stage owns the
    reel's geometry, the split and the exact durations, so the render has no surprises.
    Scoped backgrounds follow their ayahs; unscoped ones split the reel evenly.
    """
    settings: Settings = ctx.config
    width, height = GEOMETRY[inputs.aspect]
    choices = background_choices(inputs)
    problem = scope_problem(choices, inputs.ayah_range())
    if problem:
        raise InvalidInput(
            f"the background scopes do not cover the ayahs: {problem}",
            hint="scope every background with ayah_start/ayah_end, or none of them",
            details={"ayahs": list(inputs.ayah_range()), "backgrounds": len(choices)},
        )
    if choices and choices[0].covers() is not None:
        windows = scoped_slices(choices, list(ayah_windows))
    else:
        windows = background_slices(duration_ms, len(choices))
    # a crossfade needs each clip to run past its window into the next one's opening
    fade = min(inputs.transition_ms, max(0, min(end - start for start, end in windows) - 200))
    if fade != inputs.transition_ms:
        ctx.progress.detail(
            f"  · transition trimmed to {fade} ms (a window is only "
            f"{min(end - start for start, end in windows)} ms long)"
        )
    parts: list[tuple[Any, str, int, int, str]] = []
    for index, (choice, (start, end)) in enumerate(zip(choices, windows), start=1):
        ctx.cancel.raise_if_cancelled()
        path, provenance, licence, origin, extra = _slice_clip(
            ctx,
            choice,
            style,
            width=width,
            height=height,
            start_ms=start,
            end_ms=end + fade,
        )
        asset = ctx.assets.put(
            path,
            kind=AssetKind.BACKGROUND,
            provenance=provenance,
            licence=licence,
            ext=".mp4",
            # when it plays is on the sequence; what it is, is here
            meta={"origin": origin, "index": index, **extra},
        )
        parts.append((asset, origin, start, end, extra))

    if len(parts) == 1:
        asset, origin, start, end, _ = parts[0]
        return asset, origin, [(asset.id, start, end)]

    merged = ctx.workdir / "background-sequence.mp4"
    if fade > 0:
        # the crossfade sits at each boundary: the incoming shot is fully in exactly
        # when its ayah's text appears
        inputs_args: list[str] = []
        for _, _, start, _, _ in parts:
            inputs_args += ["-i", str(ctx.workdir / f"background-{start:07d}.mp4")]
        chain: list[str] = []
        previous = "0:v"
        elapsed = 0
        for index, (_, _, start, end, _) in enumerate(parts[:-1], start=1):
            elapsed = end  # end of this window, in the final timeline
            label = f"x{index}"
            chain.append(
                f"[{previous}][{index}:v]xfade=transition=fade:duration={fade / 1000:.3f}:"
                f"offset={(elapsed - fade) / 1000:.3f}[{label}]"
            )
            previous = label
        ffmpeg.run(
            [
                settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
                *inputs_args,
                "-filter_complex", ";".join(chain) + f";[{previous}]format=yuv420p[vout]",
                "-map", "[vout]", "-t", f"{duration_ms / 1000:.3f}",
                "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
                "-pix_fmt", "yuv420p", str(merged),
            ],
            cancel=ctx.cancel,
        )
    else:
        # hard cuts: a stream copy is enough, no re-encode
        concat_file = ctx.workdir / "background-concat.txt"
        # absolute paths: the concat demuxer resolves list entries against the list's own
        # directory, and a stage workdir is relative whenever the caller's is
        concat_file.write_text(
            "".join(
                f"file '{(ctx.workdir / f'background-{start:07d}.mp4').resolve()}'\n"
                for _, _, start, _, _ in parts
            ),
            encoding="utf-8",
        )
        ffmpeg.run(
            [
                settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-c", "copy", str(merged),
            ],
            cancel=ctx.cancel,
        )
    providers = ", ".join(sorted({origin for _, origin, _, _, _ in parts}))
    order = " → ".join(f"{origin}/{extra.get('media', '')}" for _, origin, _, _, extra in parts)
    asset = ctx.assets.put(
        merged,
        kind=AssetKind.BACKGROUND,
        provenance=Provenance(
            provider="reelmachine",
            source="background-sequence",
            notes=f"{len(parts)} clips ({order}); parts in provenance {providers}",
        ),
        licence=None,
        ext=".mp4",
        meta={
            "origin": "sequence",
            "transitionMs": fade,
            "parts": [part[0].id for part in parts],
            "windows": [
                {
                    "asset": part[0].id,
                    "media": part[4].get("media", ""),
                    "startMs": part[2],
                    "endMs": part[3],
                }
                for part in parts
            ],
        },
    )
    return asset, "sequence", [(part[0].id, part[2], part[3]) for part in parts]


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
                "transitionMs": inputs.transition_ms,
                "backgrounds": [
                    {"asset": asset, "startMs": start, "endMs": end}
                    for asset, start, end in payload.backgrounds
                ],
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
