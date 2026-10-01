"""doodle stages: script → narration → scenes → prep → compose → render.

The reveal schedule is built from the narration's real word timings; the render mode is
carried as data from the first stage to the manifest; and a preflight renders one
representative scene before the full job.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Provenance
from ...core.errors import InvalidInput, PreflightFailed, ProviderUnavailable, VoiceUnavailable
from ...core.stage import StageContext
from ...core.style import get_style
from ...core.timeline import Caption, Clip, Timeline, TimedAudioRef, WordSpan
from ...render.captions import write_ass, write_srt
from .models import (
    Beat,
    DoodleCompose,
    DoodleInputs,
    DoodleRenderOutput,
    NarrationOutput,
    NarrationSegment,
    PrepOutput,
    SceneSpec,
    SceneTrack,
    ScenesOutput,
    ScriptOutput,
)
from .narration import provider_for
from .narration.fake import FakeNarration
from .render import GEOMETRY, caption_styles, compose_doodle, normalise_track
from .scenes.raster import art_size
from .scenes.scenewriter import SketchSceneWriter
from .scenes.spec import choose_structure, local_words, program_scene, stroke_scene
from .script import TemplateScriptProvider
from .verify import BLANK_STDDEV, ink_growth, last_frame_stddev

FPS = 30


class ScriptStage:
    """A structured script is used as given; a topic becomes a deterministic beat list."""

    id = "script"
    revision = 1
    cacheable = True
    cache_fields = ("topic", "script", "language")
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: Any) -> ScriptOutput:
        inputs = DoodleInputs.model_validate(payload)
        ctx.cancel.raise_if_cancelled()
        if inputs.script:
            beats = inputs.beat_list()
            source = "supplied"
        elif inputs.topic:
            beats = TemplateScriptProvider().beats(inputs.topic, language=inputs.language)
            source = "template"
        else:
            raise InvalidInput(
                "give a topic or a structured script",
                hint="inputs: {\"topic\": \"compound interest\"} or {\"script\": [{\"narration\": \"…\"}]}",
            )
        return ScriptOutput(inputs=inputs, beats=beats, source=source)


def _scale_segments(segments: list[NarrationSegment], factor: float) -> list[NarrationSegment]:
    scaled: list[NarrationSegment] = []
    for segment in segments:
        words = [
            WordSpan(
                text=word.text,
                start_ms=int(round(word.start_ms * factor)),
                end_ms=int(round(word.end_ms * factor)),
            )
            for word in segment.words
        ]
        scaled.append(
            segment.model_copy(
                update={
                    "start_ms": int(round(segment.start_ms * factor)),
                    "end_ms": int(round(segment.end_ms * factor)),
                    "words": words,
                }
            )
        )
    return scaled


class NarrationStage:
    """Speak every beat, with real timings; a missing voice fails loudly."""

    id = "narration"
    revision = 1
    cacheable = False
    output_model = NarrationOutput

    def run(self, ctx: StageContext, payload: ScriptOutput) -> NarrationOutput:
        ctx.cancel.raise_if_cancelled()
        inputs = payload.inputs
        settings: Settings = ctx.config
        provider = ctx.providers.get("narration")
        provider_name = getattr(provider, "name", "")
        requested = provider_for(inputs.voice)

        degraded = False
        note = ""
        if requested != provider_name:
            if not inputs.allow_visible_fallback:
                raise VoiceUnavailable(
                    f"the voice {inputs.voice!r} needs the {requested!r} narration provider, "
                    f"but this deployment serves {provider_name!r}",
                    hint=(
                        f"use voice '{provider_name}:<voice-id>', or set REEL_TTS={requested} "
                        "on the deployment"
                    ),
                    details={"requested": requested, "bound": provider_name},
                )
            degraded = True
            note = (
                f"requested {requested!r} narration; the deployment serves {provider_name!r} — "
                f"fell back to the {provider_name} voice (visible degradation)"
            )

        workdir = ctx.workdir / "beats"
        workdir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        segments: list[NarrationSegment] = []
        provenance = Provenance(provider=provider_name, source=inputs.voice)
        licence = None
        cursor = 0
        for beat in payload.beats:
            ctx.cancel.raise_if_cancelled()
            try:
                speech = provider.synthesize(beat.narration, voice=inputs.voice, language=inputs.language)
            except VoiceUnavailable as exc:
                if not inputs.allow_visible_fallback:
                    raise
                fallback = FakeNarration(settings, workdir=workdir)
                speech = fallback.synthesize(beat.narration, voice="fake:default")
                degraded = True
                note = f"{exc.message}; fell back to the fake voice (visible degradation)"
            duration = speech.duration_ms
            if duration <= 0:
                duration = int(ffmpeg.probe(speech.path).duration_s * 1000)
            suffix = Path(speech.path).suffix or ".wav"
            local = workdir / f"{beat.id}{suffix}"
            shutil.copy2(speech.path, local)
            paths.append(local)
            segments.append(
                NarrationSegment(
                    beat_id=beat.id,
                    text=beat.narration,
                    start_ms=cursor,
                    end_ms=cursor + duration,
                    words=list(speech.segments[0].words) if speech.segments else [],
                )
            )
            cursor += duration
            licence = speech.licence or licence
            provenance = speech.provenance if speech.provenance.provider else provenance
            ctx.progress.detail(f"  · {beat.id}: {duration / 1000:.1f}s")

        joined = ctx.workdir / "narration.wav"
        if len(paths) == 1:
            shutil.copy2(paths[0], joined)
        else:
            ffmpeg.concat_audio(paths, joined, cancel=ctx.cancel)
        total_ms = int(ffmpeg.probe(joined).duration_s * 1000)
        if cursor and abs(total_ms - cursor) > 120:
            factor = total_ms / cursor
            segments = _scale_segments(segments, factor)
            ctx.progress.detail(f"  · narration {cursor} ms became {total_ms} ms after joining")

        asset = ctx.assets.put(
            joined,
            kind=AssetKind.NARRATION,
            provenance=provenance,
            licence=licence,
            ext=".wav",
            meta={"voice": inputs.voice},
        )
        return NarrationOutput(
            inputs=inputs,
            beats=payload.beats,
            audio_asset=asset.id,
            duration_ms=total_ms,
            segments=segments,
            voice=inputs.voice,
            provider=provider_name,
            provenance=provenance,
            licence=licence,
            degraded=degraded,
            degradation_note=note,
        )


class ScenesStage:
    """Per-beat scene specs, with the reveal schedule read from the narration."""

    id = "scenes"
    revision = 1
    cacheable = False
    output_model = ScenesOutput

    def run(self, ctx: StageContext, payload: NarrationOutput) -> ScenesOutput:
        ctx.cancel.raise_if_cancelled()
        inputs = payload.inputs
        beats: list[Beat] = payload.beats
        structure = choose_structure(inputs, beats)
        writer = SketchSceneWriter()
        art_dir = ctx.workdir / "art"
        scenes: list[SceneSpec] = []

        for index, (beat, segment) in enumerate(zip(beats, payload.segments), start=1):
            duration_ms = segment.end_ms - segment.start_ms
            words = local_words(segment.words, segment.start_ms)
            if inputs.mode == "stroke":
                supplied = beat.art or (inputs.line_art[index - 1] if index <= len(inputs.line_art) else None)
                if supplied:
                    art = Path(supplied).expanduser()
                    if not art.is_file():
                        raise InvalidInput(
                            f"line art not found: {art}",
                            hint="supply a PNG/JPEG per beat in `line_art`",
                        )
                    provenance = Provenance(provider="tenant", source="upload")
                else:
                    missing = writer.missing()
                    if missing:
                        raise InvalidInput(
                            "stroke mode needs line art, and the sketch writer is unavailable",
                            hint="; ".join(missing),
                        )
                    canvas = GEOMETRY[inputs.aspect]
                    art = writer.draw(beat, canvas=canvas, index=index, dest_dir=art_dir)
                    provenance = Provenance(provider=writer.name, source="generated")
                asset = ctx.assets.put(
                    art, kind=AssetKind.LINE_ART, provenance=provenance, ext=art.suffix or ".png"
                )
                scene = stroke_scene(
                    beat,
                    index,
                    structure=structure,
                    words=words,
                    duration_ms=duration_ms,
                    canvas=art_size(art),
                )
                scene.line_art_asset = asset.id
            else:
                scene = program_scene(
                    beat,
                    index,
                    canvas=GEOMETRY[inputs.aspect],
                    duration_ms=duration_ms,
                    words=words,
                )
            scenes.append(scene)

        return ScenesOutput(
            inputs=inputs, scenes=scenes, schedule_source="narration", narration=payload
        )


class PrepStage:
    """Render every scene track, after a preflight of one representative scene."""

    id = "prep"
    revision = 1
    cacheable = False
    output_model = PrepOutput

    def run(self, ctx: StageContext, payload: ScenesOutput) -> PrepOutput:
        ctx.cancel.raise_if_cancelled()
        inputs = payload.inputs
        settings: Settings = ctx.config
        style = get_style(inputs.style)
        renderers = {
            "stroke": ctx.providers.get("renderer_stroke"),
            "program": ctx.providers.get("renderer_program"),
        }
        renderer = renderers[inputs.mode]
        problems = renderer.missing()
        if problems:
            raise ProviderUnavailable(
                f"the {inputs.mode} renderer is not usable on this deployment",
                hint="; ".join(problems),
                details={"mode": inputs.mode},
            )

        width, height = GEOMETRY[inputs.aspect]
        raw_dir = ctx.workdir / "scenes"
        final_dir = ctx.workdir / "normalised"
        raw_dir.mkdir(parents=True, exist_ok=True)
        final_dir.mkdir(parents=True, exist_ok=True)

        def render_one(scene: SceneSpec) -> Path:
            raw = raw_dir / f"{scene.id}.mp4"
            if scene.mode == "stroke":
                art = ctx.assets.get(scene.line_art_asset or "")
                params = style.params or {}
                ctx.progress.detail(f"  · inking scene {scene.index} ({scene.id})")
                renderer.render(
                    scene,
                    art=art,
                    dest=raw,
                    ink_path=str(params.get("ink_path", "skeleton")),
                    color_fill=str(params.get("color_fill", "contour-wipe")),
                    fps=FPS,
                    total_ms=scene.duration_ms,
                    cancel=ctx.cancel,
                )
            else:
                ctx.progress.detail(f"  · drawing scene {scene.index} ({scene.id})")
                renderer.render(scene, dest=raw, settings=settings, fps=FPS, cancel=ctx.cancel)
            final = final_dir / f"{scene.id}.mp4"
            normalise_track(
                raw,
                final,
                width=width,
                height=height,
                fps=FPS,
                settings=settings,
                duration_ms=scene.duration_ms,
                cancel=ctx.cancel,
            )
            return final

        preflight: dict[str, Any] = {"skipped": not inputs.preflight}
        ordered = list(payload.scenes)
        representative = ordered[len(ordered) // 2] if ordered else None
        rendered: dict[str, Path] = {}
        if inputs.preflight and representative is not None:
            ctx.progress.detail(f"  · preflight: scene {representative.index} ({representative.id})")
            path = render_one(representative)
            info = ffmpeg.probe(path)
            try:
                ink = last_frame_stddev(path, settings=settings)
            except Exception:  # noqa: BLE001 - an undecodable track is a failed preflight
                ink = 0.0
            grew, counts = (
                ink_growth(path, settings=settings) if inputs.mode == "stroke" else (True, [])
            )
            # "it produced a file" is not "it drew something": the canvas must not be
            # blank, and in stroke mode the ink must accumulate rather than being a pan
            ok = (
                info.has_video
                and info.duration_s >= 0.3
                and ink >= BLANK_STDDEV
                and grew
            )
            preflight = {
                "scene": representative.id,
                "duration_ms": int(info.duration_s * 1000),
                "ink_stddev": round(ink, 2),
                "ink_counts": counts,
                "ok": ok,
            }
            if not ok:
                raise PreflightFailed(
                    "the representative scene did not render something that accumulates ink",
                    hint=(
                        "check the line art (a blank canvas cannot be inked) and the reveal "
                        "schedule for this beat"
                    ),
                    details=preflight,
                )
            rendered[representative.id] = path

        for scene in ordered:
            if scene.id in rendered:
                continue
            rendered[scene.id] = render_one(scene)

        tracks: list[SceneTrack] = []
        for scene in ordered:
            path = rendered[scene.id]
            info = ffmpeg.probe(path)
            asset = ctx.assets.put(
                path,
                kind=AssetKind.SCENE_TRACK,
                provenance=Provenance(
                    provider=renderer.name, source=inputs.mode, notes=f"scene {scene.index}"
                ),
                ext=".mp4",
            )
            tracks.append(
                SceneTrack(
                    scene_id=scene.id,
                    index=scene.index,
                    asset=asset.id,
                    path=str(path),
                    duration_ms=int(info.duration_s * 1000),
                    renderer=renderer.name,
                )
            )
        return PrepOutput(
            inputs=inputs,
            beats=payload.narration.beats if payload.narration else [],
            narration=payload.narration,
            tracks=tracks,
            duration_ms=sum(track.duration_ms for track in tracks),
            preflight=preflight,
        )


class ComposeStage:
    """Tracks, narration and captions become the timeline."""

    id = "compose"
    revision = 1
    cacheable = False
    output_model = DoodleCompose

    def run(self, ctx: StageContext, payload: PrepOutput) -> DoodleCompose:
        inputs = payload.inputs
        settings: Settings = ctx.config
        style = get_style(inputs.style)
        timeline_aspect = "vertical" if inputs.aspect == "vertical" else "landscape"

        video: list[Clip] = []
        cursor = 0
        for track in payload.tracks:
            video.append(
                Clip(
                    element_id=track.scene_id,
                    asset=track.asset,
                    start_ms=cursor,
                    duration_ms=track.duration_ms,
                    origin="scene",
                )
            )
            cursor += track.duration_ms

        captions: list[Caption] = []
        if inputs.captions:
            for segment in payload.narration.segments if payload.narration else []:
                captions.append(
                    Caption(
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        text=segment.text,
                        lang=inputs.language,
                        style_ref="doodle.caption",
                        words=segment.words,
                        meta={"source": segment.beat_id},
                    )
                )
        for beat in payload.beats:
            if not beat.keywords:
                continue
            window = next(
                (segment for segment in (payload.narration.segments if payload.narration else []) if segment.beat_id == beat.id),
                None,
            )
            if window is None:
                continue
            captions.append(
                Caption(
                    start_ms=window.start_ms,
                    end_ms=window.start_ms + max(1200, int((window.end_ms - window.start_ms) * 0.6)),
                    text=" · ".join(beat.keywords),
                    lang=inputs.language,
                    style_ref="doodle.keyword",
                    meta={"source": beat.id},
                )
            )

        timeline = Timeline(
            duration_ms=max(1, payload.narration.duration_ms if payload.narration else cursor),
            aspect=timeline_aspect,  # type: ignore[arg-type]
            render_mode=inputs.mode,
            style=style.id,
            video=video,
            audio=[
                TimedAudioRef(
                    element_id="narration",
                    asset=payload.narration.audio_asset if payload.narration else "",
                    duration_ms=payload.narration.duration_ms if payload.narration else cursor,
                    role="narration",
                )
            ],
            captions=captions,
            meta={
                "structure": choose_structure(inputs, payload.beats),
                "narrationProvider": payload.narration.provider if payload.narration else "",
                "degraded": bool(payload.narration.degraded) if payload.narration else False,
            },
        )
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        reel_name = inputs.name or ctx.job.id
        return DoodleCompose(
            inputs=inputs,
            timeline=timeline,
            render_options={
                "outdir": str(outdir),
                "reel_name": reel_name,
                "workdir": str(settings.workdir / "render" / reel_name),
                "audio_profile": inputs.audio_profile,
                "style_id": style.id,
                "aspect": inputs.aspect,
                "credits": [f"Narration: {inputs.voice}"],
            },
        )


class RenderStage:
    """Captions to ASS/SRT, one compose pass, one manifest."""

    id = "render"
    revision = 1
    cacheable = False
    output_model = DoodleRenderOutput

    def run(self, ctx: StageContext, payload: DoodleCompose) -> DoodleRenderOutput:
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

        tracks = [ctx.assets.get(element.asset) for element in timeline.video if isinstance(element, Clip)]
        narration = ctx.assets.get(timeline.audio[0].asset)  # type: ignore[union-attr]
        video = outdir / f"{reel_name}.mp4"
        compose_doodle(
            tracks,
            narration,
            ass_path,
            video,
            settings=settings,
            profile=options["audio_profile"],
            width=width,
            height=height,
            fps=FPS,
            cancel=ctx.cancel,
        )

        manifest = {
            "reel": str(video),
            "recipe": "doodle",
            "mode": timeline.render_mode,
            "created": datetime.now(timezone.utc).isoformat(),
            "durationMs": timeline.duration_ms,
            "aspect": payload.inputs.aspect,
            "scenes": len(tracks),
            "captions": len(timeline.captions),
            "audioProfile": options["audio_profile"],
            "credits": options["credits"],
        }
        manifest_path = outdir / f"{reel_name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        return DoodleRenderOutput(
            video=str(video),
            ass=str(ass_path),
            srt=str(srt_path),
            manifest=str(manifest_path),
            duration_ms=timeline.duration_ms,
            render_mode=timeline.render_mode,
        )
