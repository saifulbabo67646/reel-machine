"""mapreel stages: script → geo → images → narration → segments → prep → compose → render."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Provenance
from ...core.errors import InvalidInput, RendererUnavailable, VoiceUnavailable
from ...core.stage import StageContext
from ...core.timeline import Caption, Clip, Timeline, TimedAudioRef, WordSpan
from ...render.captions import write_srt
from ..doodle.narration import provider_for
from ..explainer3d.audio import mix, pad_speech, synth_sfx
from ..explainer3d.markup import display_text, display_words, parse_markup, spoken_text
from ..explainer3d.stages import speak_line
from ..explainer3d.timing import fit_windows, shot_windows
from ..explainer3d.video import join_shots, mux
from . import basemap, compose, geodata, palette
from .geometry import Frame, shape_of, visible_bbox
from .models import (
    ComposeOutput, GeoOutput, ImagesOutput, MapReelInputs, NarrationOutput, PrepOutput, RenderOutput,
    ResolvedSegment, ScriptOutput, SegmentProject, SegmentsOutput, Track, Window,
)
from .places import Atlas, atlas_for
from .timing import FLY_MS, MIN_SEGMENT_MS, match, pop_times

IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}


def _artifacts(ctx: StageContext) -> Path:
    path = ctx.workdir / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codes(segment: Any) -> list[str]:
    return [segment.country] if segment.kind == "country" else list(segment.countries)


def resolve_segments(inputs: MapReelInputs, atlas: Atlas) -> list[ResolvedSegment]:
    """Places to codes (errors carry the segment path) and a colour for every place."""
    resolved: list[tuple[str, int, Any, list[str], dict[str, str]]] = []
    for index, (seg_id, segment) in enumerate(zip(inputs.segment_ids(), inputs.segments)):
        codes, names = [], {}
        for j, query in enumerate(_codes(segment)):
            try:
                place = atlas.resolve(query)
            except InvalidInput as exc:
                field = "country" if segment.kind == "country" else f"countries[{j}]"
                exc.details["path"] = f"segments[{index}].{field}"
                raise
            codes.append(place.code)
            names[place.code] = place.name
        resolved.append((seg_id, index, segment, codes, names))
    fixed = {codes[0]: seg.colour for _, _, seg, codes, _ in resolved if seg.colour}
    colours = palette.assign([codes for *_, codes, _ in resolved], fixed=fixed)
    return [ResolvedSegment(id=seg_id, index=index, kind=seg.kind, codes=codes, names=names,
                            colours={c: colours[c] for c in codes})
            for seg_id, index, seg, codes, names in resolved]


class ScriptStage:
    id = "script"
    revision = 1
    cacheable = False
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: Any) -> ScriptOutput:
        inputs = MapReelInputs.model_validate(payload)
        try:
            segments = resolve_segments(inputs, atlas_for(ctx.config))
        except InvalidInput as exc:
            exc.stage = self.id
            raise
        return ScriptOutput(inputs=inputs, segments=segments)


class GeoStage:
    """Frames for every segment, the world layer, and one sharp layer per segment."""

    id = "geo"
    revision = 1
    cacheable = False
    output_model = GeoOutput

    def run(self, ctx: StageContext, payload: ScriptOutput) -> GeoOutput:
        settings: Settings = ctx.config
        root = geodata.geo_cache(settings)
        atlas = atlas_for(settings)
        world = basemap.world_layer(settings, root)
        frames, layers, bboxes = {}, {}, {}
        previous = None
        for seg, spec in zip(payload.segments, payload.inputs.segments):
            ctx.cancel.raise_if_cancelled()
            if spec.camera.hold and previous is not None:
                frame = previous
            else:
                frame = compose.segment_frame([shape_of(atlas.places[c]) for c in seg.codes], spec.camera.zoom)
            frames[seg.id] = frame.to_dict()
            path, bbox = basemap.detail_layer(settings, root, visible_bbox(frame, margin=0.25))
            layers[seg.id], bboxes[seg.id] = str(path), list(bbox)
            previous = frame
        return GeoOutput(**payload.model_dump(), world=str(world), layers=layers, detail_bboxes=bboxes,
                         frames=frames)


def _name_for(ref: str, suffix: str) -> str:
    """A stable file name: the same image gives the same project, so a render is reused."""
    return hashlib.sha256(ref.encode()).hexdigest()[:16] + suffix


def _fetch_image(ref: str, dest_dir: Path) -> Path:
    """A local file or an http(s) URL, copied into the job; raises on anything unusable."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if ref.startswith(("http://", "https://")):
        response = httpx.get(ref, timeout=30, follow_redirects=True, headers={"User-Agent": geodata.USER_AGENT})
        response.raise_for_status()
        kind = response.headers.get("content-type", "")
        if not kind.startswith("image/") or len(response.content) > IMAGE_MAX_BYTES:
            raise ValueError(f"not an image under 8 MB ({kind or 'no content type'})")
        suffix = "." + kind.split("/", 1)[1].split(";")[0].replace("svg+xml", "svg").replace("jpeg", "jpg")
        path = dest_dir / _name_for(ref, suffix)
        path.write_bytes(response.content)
        return path
    src = Path(ref).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"no file at {src}")
    if src.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"{src.suffix or 'no extension'} is not an image type the renderer draws")
    path = dest_dir / _name_for(str(src.resolve()), src.suffix.lower())
    shutil.copy2(src, path)
    return path


class ImagesStage:
    """Caller-supplied images (paths, URLs). Anything that fails becomes a text badge."""

    id = "images"
    revision = 1
    cacheable = False
    output_model = ImagesOutput

    def run(self, ctx: StageContext, payload: GeoOutput) -> ImagesOutput:
        images: dict[str, str | None] = {}
        warnings: list[str] = []
        for seg, spec in zip(payload.segments, payload.inputs.segments):
            for j, item in enumerate(getattr(spec, "items", [])):
                key = f"{seg.id}/{j}"
                images[key] = None
                if not item.image:
                    continue
                if item.image.startswith("commons:"):
                    warnings.append(f"{seg.id} {item.name}: Commons images arrive with find_images; shown as a badge")
                    continue
                try:
                    images[key] = str(_fetch_image(item.image, ctx.workdir / "images"))
                except (OSError, ValueError, httpx.HTTPError) as exc:
                    warnings.append(f"{seg.id} {item.name}: image not usable ({exc}); shown as a badge")
        return ImagesOutput(**payload.model_dump(), images=images, warnings=warnings)


class NarrationStage:
    id = "narration"
    revision = 1
    cacheable = False
    output_model = NarrationOutput

    def run(self, ctx: StageContext, payload: ImagesOutput) -> NarrationOutput:
        inputs, settings = payload.inputs, ctx.config
        provider = ctx.providers.get("narration")
        name = getattr(provider, "name", "")
        if provider_for(inputs.voice) != name:
            raise VoiceUnavailable(
                f"the voice {inputs.voice!r} needs the {provider_for(inputs.voice)!r} narration provider, "
                f"but this deployment serves {name!r}",
                hint=f"use voice '{name}:<voice-id>', or set REEL_TTS on the deployment", stage=self.id)
        work = ctx.workdir / "lines"
        work.mkdir(parents=True, exist_ok=True)
        lines, licence = [], None
        for seg, spec in zip(payload.segments, inputs.segments):
            ctx.cancel.raise_if_cancelled()
            tokens = parse_markup(spec.narration)
            path, ms, words, line_licence = speak_line(ctx, provider, spoken_text(tokens), inputs.voice,
                                                       inputs.language)
            local = work / f"{seg.id}.wav"
            shutil.copy2(path, local)
            lines.append((local, ms, words, tokens))
            licence = line_licence or licence
        windows = shot_windows([ms for _, ms, _, _ in lines], min_ms=MIN_SEGMENT_MS)
        if inputs.length:
            windows = fit_windows(windows, inputs.length * 1000)
        padded, shown_all, hot_all, spoken_all = [], [], [], []
        for seg, (path, ms, words, tokens), window in zip(payload.segments, lines, windows):
            padded.append(pad_speech(path, work / f"{seg.id}.padded.wav", lead_ms=window.speech_offset_ms,
                                     total_ms=window.duration_ms, settings=settings, cancel=ctx.cancel))
            base = window.start_ms + window.speech_offset_ms
            shown = display_words(tokens, words, total_ms=ms)
            shown_all.append([WordSpan(text=w.text, start_ms=base + w.start_ms, end_ms=base + w.end_ms)
                              for w in shown])
            hot_all.append([w.hot for w in shown])
            spoken_all.append(list(words))
        joined = ctx.workdir / "narration.wav"
        if len(padded) == 1:
            shutil.copy2(padded[0], joined)
        else:
            ffmpeg.concat_audio(padded, joined, cancel=ctx.cancel)
        asset = ctx.assets.put(joined, kind=AssetKind.NARRATION, licence=licence,
                               provenance=Provenance(provider=name, source=inputs.voice), ext=".wav")
        return NarrationOutput(
            **payload.model_dump(), audio_asset=asset.id, duration_ms=windows[-1].end_ms,
            windows=[Window(start_ms=w.start_ms, duration_ms=w.duration_ms, speech_offset_ms=w.speech_offset_ms,
                            speech_ms=w.speech_ms) for w in windows],
            words=shown_all, hot=hot_all, spoken=spoken_all, provider=name, licence=licence)


class SegmentsStage:
    """Pop times, item layout and one HyperFrames project per segment; script.resolved.json."""

    id = "segments"
    revision = 1
    cacheable = False
    output_model = SegmentsOutput

    def run(self, ctx: StageContext, payload: NarrationOutput) -> SegmentsOutput:
        atlas = atlas_for(ctx.config)
        inputs = payload.inputs
        warnings = list(payload.warnings)
        projects, cues, resolved = [], [], []
        previous_frame, previous_places = None, []
        for i, (seg, spec, window) in enumerate(zip(payload.segments, inputs.segments, payload.windows)):
            frame = payload.frames[seg.id]
            start = previous_frame or frame
            fly = 0 if start == frame else FLY_MS
            speech_start = window.speech_offset_ms
            speech_end = window.speech_offset_ms + window.speech_ms
            spoken = payload.spoken[i]
            shapes = {c: shape_of(atlas.places[c]) for c in seg.codes}
            landed = Frame(**frame)

            # places fill when named (highlight) or as the camera lands (country)
            if spec.kind == "highlight":
                heard = match([seg.names[c] for c in seg.codes], spoken)
                fills, _ = pop_times(heard, overrides=[None] * len(seg.codes),
                                     speech_start_ms=speech_start, speech_end_ms=speech_end)
            else:
                fills = [max(300, fly - 200)]
            places = [compose.place_entry(c, seg.names[c], seg.colours[c], shapes[c], landed, fills[k],
                                          atlas.places[c].label) for k, c in enumerate(seg.codes)]

            items_in: list[dict[str, Any]] = []
            items: list[dict[str, Any]] = []
            if spec.kind == "country":
                heard_items = match([it.say or it.name for it in spec.items], spoken)
                overrides = [None if it.at is None else int(it.at * 1000) for it in spec.items]
                times, spread = pop_times(heard_items, overrides=overrides,
                                          speech_start_ms=speech_start, speech_end_ms=speech_end)
                for j, (item, at) in enumerate(zip(spec.items, times)):
                    local = payload.images.get(f"{seg.id}/{j}")
                    items_in.append({"name": item.name, "emoji": item.emoji, "atMs": at,
                                     "image": f"images/{Path(local).name}" if local else None, "_local": local})
                for j in spread:
                    warnings.append(f"{seg.id} {spec.items[j].name}: not heard in the narration; "
                                    "its pop is spread evenly (add `say` or `at`)")
                items = compose.item_entries(items_in, shapes[seg.codes[0]], landed)

            words = [{"text": w.text, "start": (w.start_ms - window.start_ms) / 1000,
                      "end": (w.end_ms - window.start_ms) / 1000, "hot": hot}
                     for w, hot in zip(payload.words[i], payload.hot[i])]
            layers = [{"url": "layers/world.jpg", "bbox": [0, 0, 1, 1]},
                      {"url": "layers/detail.jpg", "bbox": payload.detail_bboxes[seg.id]}]
            camera = {"from": start, "to": frame, "flyMs": fly, "drift": 0.04}
            doc = compose.segment_document(
                duration_ms=window.duration_ms, camera=camera, layers=layers, places=places,
                previous=[{"colour": p["colour"], "rings": p["rings"]} for p in previous_places] if fly else [],
                items=items, words=words, legend=getattr(spec, "legend", None))
            links = {"layers/world.jpg": Path(payload.world), "layers/detail.jpg": Path(payload.layers[seg.id])}
            for it in items_in:
                if it["_local"]:
                    links[it["image"]] = Path(it["_local"])
            directory = compose.write_project(doc, ctx.workdir / "projects" / seg.id, links)
            projects.append(SegmentProject(segment_id=seg.id, index=i, dir=str(directory),
                                           key=compose.project_key(doc, links), nominal_ms=window.duration_ms))
            if fly:
                cues.append({"at_ms": window.start_ms, "sfx": "whoosh"})
            cues.extend({"at_ms": window.start_ms + it["atMs"], "sfx": "pop"} for it in items)
            resolved.append({"id": seg.id, "kind": spec.kind, "codes": seg.codes, "colours": seg.colours,
                             "window": window.model_dump(), "camera": camera,
                             "places": [{"code": p["code"], "fillMs": p["fillMs"]} for p in places],
                             "items": [{"name": it["name"], "atMs": it["atMs"], "image": it["image"],
                                        "x": it["x"], "y": it["y"]} for it in items]})
            previous_frame, previous_places = frame, places
        (_artifacts(ctx) / "script.resolved.json").write_text(
            json.dumps({"segments": resolved, "warnings": warnings}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        return SegmentsOutput(narration=payload, projects=projects, cues=cues)


def _caller_cache(ctx: StageContext) -> Path:
    """Rendered segments are kept per caller, beside the engine's stage cache, so a build
    after its storyboard (or a re-run) reuses them; outside the engine, the cache dir."""
    settings: Settings = ctx.config
    job_id = getattr(ctx.job, "id", "")
    if job_id and settings.workdir.name == job_id:
        return settings.workdir.parent / "cache" / "mapreel"
    return settings.cache_dir / "mapreel"


class PrepStage:
    """Render every segment (cached by content) or, in storyboard mode, one still each."""

    id = "prep"
    revision = 1
    cacheable = False
    output_model = PrepOutput

    def run(self, ctx: StageContext, payload: SegmentsOutput) -> PrepOutput:
        settings: Settings = ctx.config
        renderer = ctx.providers.get("renderer")
        problems = renderer.missing()
        if problems:
            raise RendererUnavailable("the segment renderer is not usable on this deployment",
                                      hint="; ".join(problems), stage=self.id)
        if payload.narration.inputs.mode == "storyboard":
            return self._storyboard(ctx, payload, renderer, settings)
        cache = _caller_cache(ctx) / "segments"

        def render_one(project: SegmentProject) -> Track:
            cached = cache / project.key[:2] / f"{project.key}.mp4"
            if cached.is_file():
                ctx.progress.detail(f"  · {project.segment_id}: cached")
            else:
                ctx.progress.detail(f"  · rendering {project.segment_id} ({project.nominal_ms / 1000:.1f}s)")
                partial = cached.with_name(f"{project.key}.partial.mp4")
                partial.parent.mkdir(parents=True, exist_ok=True)
                renderer.render(Path(project.dir), partial, fps=compose.FPS, seconds=project.nominal_ms / 1000,
                                cancel=ctx.cancel)
                partial.replace(cached)
            return Track(segment_id=project.segment_id, path=str(cached), render_ms=project.nominal_ms)

        with ThreadPoolExecutor(max_workers=2) as pool:
            tracks = list(pool.map(render_one, payload.projects))
        return PrepOutput(segments=payload, tracks=tracks, renderer=renderer.name)

    def _storyboard(self, ctx: StageContext, payload: SegmentsOutput, renderer: Any, settings: Settings) -> PrepOutput:
        stills = ctx.workdir / "stills"
        frames = [renderer.snapshot(Path(p.dir), p.nominal_ms / 1000 * 0.75, stills / f"{p.index:03d}.png",
                                    cancel=ctx.cancel) for p in payload.projects]
        cols = min(4, len(frames))
        rows = math.ceil(len(frames) / cols)
        # number the stills 1..n and fill the grid with white cells so `tile` emits it once
        sheet_in = ctx.workdir / "sheet"
        sheet_in.mkdir(parents=True, exist_ok=True)
        for index, frame in enumerate(frames, start=1):
            shutil.copy2(frame, sheet_in / f"{index:03d}.png")
        for index in range(len(frames) + 1, cols * rows + 1):
            ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi",
                        "-i", "color=white:size=1080x1920", "-frames:v", "1", str(sheet_in / f"{index:03d}.png")],
                       cancel=ctx.cancel)
        sheet = _artifacts(ctx) / "storyboard.jpg"
        ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-framerate", "1", "-start_number", "1",
                    "-i", str(sheet_in / "%03d.png"), "-vf", f"scale=360:640,tile={cols}x{rows}:padding=8:color=white",
                    "-frames:v", "1", "-q:v", "3", str(sheet)], cancel=ctx.cancel)
        return PrepOutput(segments=payload, storyboard=str(sheet), renderer=renderer.name, halt_pipeline=True)


class ComposeStage:
    id = "compose"
    revision = 1
    cacheable = False
    output_model = ComposeOutput

    def run(self, ctx: StageContext, payload: PrepOutput) -> ComposeOutput:
        narration = payload.segments.narration
        inputs = narration.inputs
        settings: Settings = ctx.config
        video = [Clip(element_id=t.segment_id, asset=t.path, start_ms=w.start_ms, duration_ms=w.duration_ms,
                      origin="scene") for t, w in zip(payload.tracks, narration.windows)]
        captions = [Caption(start_ms=words[0].start_ms, end_ms=words[-1].end_ms,
                            text=display_text(parse_markup(spec.narration)), lang=inputs.language,
                            style_ref="mapreel.caption", words=words, meta={"source": seg.id})
                    for seg, spec, words in zip(narration.segments, inputs.segments, narration.words) if words]
        timeline = Timeline(duration_ms=narration.duration_ms, aspect="vertical", render_mode="hyperframes",
                            style=inputs.look, video=video, captions=captions,
                            audio=[TimedAudioRef(element_id="narration", asset=narration.audio_asset,
                                                 duration_ms=narration.duration_ms, role="narration")],
                            meta={"renderer": payload.renderer})
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        name = inputs.name or ctx.job.id
        return ComposeOutput(prep=payload, timeline=timeline, render_options={
            "outdir": str(outdir), "reel_name": name, "workdir": str(settings.workdir / "render" / name),
            "credits": [f"Narration: {inputs.voice}", geodata.NATURAL_EARTH.attribution,
                        geodata.BLUE_MARBLE.attribution]})


class RenderStage:
    id = "render"
    revision = 1
    cacheable = False
    output_model = RenderOutput

    def run(self, ctx: StageContext, payload: ComposeOutput) -> RenderOutput:
        settings: Settings = ctx.config
        prep, timeline, options = payload.prep, payload.timeline, payload.render_options
        narration = prep.segments.narration
        inputs = narration.inputs
        work, outdir = Path(options["workdir"]), Path(options["outdir"])
        work.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)
        name = options["reel_name"]
        joined = join_shots([Path(t.path) for t in prep.tracks], ["cut"] * len(prep.tracks),
                            [t.render_ms for t in prep.tracks], work / "video.mp4",
                            settings=settings, fps=compose.FPS, cancel=ctx.cancel)
        sounds: dict[str, Path] = {}
        for cue in prep.segments.cues:
            if cue["sfx"] not in sounds:
                sounds[cue["sfx"]] = synth_sfx(cue["sfx"], work / "sfx" / f"{cue['sfx']}.wav", settings=settings,
                                               designed=True)
        cues = [(cue["at_ms"], sounds[cue["sfx"]]) for cue in prep.segments.cues]
        music = Path(inputs.music).expanduser() if inputs.music else None
        audio = mix(ctx.assets.get(narration.audio_asset), cues, music, work / "audio.wav",
                    total_ms=timeline.duration_ms, settings=settings, profile="studio", cancel=ctx.cancel)
        video = mux(joined, audio, outdir / f"{name}.mp4", total_ms=timeline.duration_ms, settings=settings,
                    cancel=ctx.cancel)
        srt = write_srt(timeline.captions, work / f"{name}.srt")
        manifest = {"reel": str(video), "recipe": "mapreel", "mode": "hyperframes",
                    "created": datetime.now(timezone.utc).isoformat(), "durationMs": timeline.duration_ms,
                    "segments": len(prep.tracks), "look": inputs.look, "renderer": prep.renderer,
                    "data": {k: v.model_dump() for k, v in geodata.LICENCES.items()},
                    "credits": options["credits"]}
        manifest_path = outdir / f"{name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return RenderOutput(video=str(video), srt=str(srt), manifest=str(manifest_path),
                            duration_ms=timeline.duration_ms)
