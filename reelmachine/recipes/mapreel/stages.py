"""mapreel stages: script → geo → images → narration → segments → prep → compose → render."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Provenance
from ...core.errors import InvalidInput, JobCancelled, RendererUnavailable, VoiceUnavailable
from ...core.stage import StageContext
from ...core.timeline import Caption, Clip, Timeline, TimedAudioRef, WordSpan
from ...render.captions import write_srt
from ..doodle.narration import provider_for
from ..explainer3d.audio import mix, pad_speech, synth_sfx
from ..explainer3d.markup import display_text, display_words, parse_markup, spoken_text
from ..explainer3d.music import synth_music
from ..explainer3d.stages import speak_line
from ..explainer3d.timing import ShotWindow, estimate_speech_ms, fit_windows, shot_windows
from ..explainer3d.video import join_shots, mux
from . import basemap, commons, compose, geodata, kinds, lint, palette, sources
from .geometry import SAFE, Frame, Shape, frame_for, shape_of, visible_bbox
from .models import (
    ComposeOutput, GeoOutput, ImagesOutput, MapReelInputs, NarrationOutput, PrepOutput, RenderOutput,
    ResolvedSegment, ScriptOutput, SegmentProject, SegmentsOutput, Track, Window,
)
from .places import Atlas, atlas_for
from .regions import RegionAtlas, region_atlas_for
from .timing import FLY_MS, MIN_SEGMENT_MS

IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_MAX_SIDE = 1024  # every item image is normalised to a PNG no longer than this on its longest side
IMAGE_MAX_PIXELS = 40_000_000  # refused before decoding: RGBA at this size is already 160 MB
IMAGE_DEADLINE_S = 60  # wall clock for one image download, however slowly it trickles in
PILLOW_MISSING = "Pillow is not installed, so images cannot be read (pip install Pillow)"
COMMONS_ORIGINAL_MAX = 2048  # a Commons original wider than this is fetched as its 1024 px thumb instead
SVG_REFUSED = "SVG is not drawn; give a PNG or JPEG, or a Commons id (its PNG render is used)"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
#: the only content types a downloaded image may have, and the suffix each one is saved with
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
               "image/svg+xml": ".svg"}
POP_TAIL_MS = 300  # nothing pops or fills later than this before its segment's cut
STABLE_MTIME = 1_000_000_000  # downloads are named by content; a fixed mtime keeps project keys stable


def _artifacts(ctx: StageContext) -> Path:
    path = ctx.workdir / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codes(segment: Any) -> list[str]:
    return list(segment.countries) if segment.kind in ("highlight", "versus") else [segment.country]


def _resolve_regions(index: int, segment: Any, country: str, regions: RegionAtlas,
                     country_colour: str) -> list[dict[str, str]]:
    """A regions segment's regions, found inside its country, each coloured apart from the others and the country."""
    found = []
    for j, entry in enumerate(segment.regions):
        try:
            place = regions.resolve(entry.region, country)
        except InvalidInput as exc:
            exc.details["path"] = f"segments[{index}].regions[{j}].region"
            raise
        if any(p.code == place.code for p in found):  # one region twice would be one fill and one colour
            raise InvalidInput(f"{entry.region!r} is {place.name} again", hint="name each region once",
                               details={"path": f"segments[{index}].regions[{j}].region"})
        found.append(place)
    fixed = {country: country_colour, **{p.code: e.colour for p, e in zip(found, segment.regions) if e.colour}}
    colours = palette.assign([[country], [p.code for p in found]], fixed=fixed)
    return [{"code": p.code, "name": p.name, "colour": colours[p.code]} for p in found]


def resolve_segments(inputs: MapReelInputs, atlas: Atlas,
                     region_atlas: Callable[[], RegionAtlas] | None = None) -> list[ResolvedSegment]:
    """Places to codes (errors carry the segment path) and a colour for every place. `region_atlas` loads the
    admin-1 data; it is called once, and only when a `regions` segment needs it."""
    resolved: list[tuple[str, int, Any, list[str], dict[str, str]]] = []
    for index, (seg_id, segment) in enumerate(zip(inputs.segment_ids(), inputs.segments)):
        if index and segment.camera.hold and inputs.segments[index - 1].kind == "versus":
            raise InvalidInput(f"segment {index + 1} holds the camera right after a versus",
                               hint="a versus has no single frame to hold; drop hold",
                               details={"path": f"segments[{index}].camera.hold"})
        codes, names = [], {}
        for j, query in enumerate(_codes(segment)):
            try:
                place = atlas.resolve(query)
            except InvalidInput as exc:
                field = f"countries[{j}]" if segment.kind in ("highlight", "versus") else "country"
                exc.details["path"] = f"segments[{index}].{field}"
                raise
            if segment.kind == "versus" and place.code in codes:  # "Japan" and "JPN": one panel twice
                raise InvalidInput(f"versus needs different countries: {query!r} is {place.name} again",
                                   hint="compare two or three different countries",
                                   details={"path": f"segments[{index}].countries[{j}]"})
            codes.append(place.code)
            names[place.code] = place.name
        resolved.append((seg_id, index, segment, codes, names))
    fixed = {codes[0]: seg.colour for _, _, seg, codes, _ in resolved if seg.colour}
    colours = palette.assign([codes for *_, codes, _ in resolved], fixed=fixed)
    regions: RegionAtlas | None = None
    out = []
    for seg_id, index, seg, codes, names in resolved:
        entries = []
        if seg.kind == "regions":
            if regions is None:
                if region_atlas is None:
                    raise ValueError("resolve_segments needs region_atlas for a regions segment")
                regions = region_atlas()
            entries = _resolve_regions(index, seg, codes[0], regions, colours[codes[0]])
        out.append(ResolvedSegment(id=seg_id, index=index, kind=seg.kind, codes=codes, names=names,
                                   colours={c: colours[c] for c in codes}, regions=entries))
    return out


def _shifted(rings: list[list[list[float]]], dx: int) -> list[list[list[float]]]:
    """Rings moved by a whole number of worlds."""
    return [[[x + dx, y] for x, y in ring] for ring in rings] if dx else rings


def _faded(place: dict[str, Any], wrap: int) -> dict[str, Any]:
    """A place the previous segment showed, for this one to fade out: moved with the camera's start, and at
    the share of a full fill it was drawn at (a regions segment's faint country)."""
    out = {"colour": place["colour"], "rings": _shifted(place["rings"], -wrap)}
    if "alpha" in place:
        out["alpha"] = place["alpha"]
    return out


def check_music(inputs: MapReelInputs) -> None:
    """`music` is "auto" (a synthesised bed) or an existing local audio file."""
    if inputs.music and inputs.music != "auto" and not Path(inputs.music).expanduser().is_file():
        raise InvalidInput(f"music file not found: {inputs.music}",
                           hint='give a local audio file, "auto" for a synthesised bed, or leave music out',
                           details={"path": "music"})


def estimated_windows(inputs: MapReelInputs) -> list[ShotWindow]:
    """Segment windows from the speech estimate alone (no voice yet), stretched to `length` when it is set."""
    estimates = [estimate_speech_ms(spoken_text(parse_markup(s.narration)), inputs.language)
                 for s in inputs.segments]
    windows = shot_windows(estimates, min_ms=MIN_SEGMENT_MS)
    return fit_windows(windows, inputs.length * 1000) if inputs.length else windows


def heard_names(inputs: MapReelInputs, resolved: list[ResolvedSegment], atlas: Atlas) -> dict[str, list[str]]:
    """What the voice says for each highlight or versus country, keyed "seg-N/j" (the runtime's `heard_as`), and
    for each region of a regions segment, keyed "seg-N/region-j": its `say`, else its name (and what the script
    wrote, unless that is an ISO code: "US-TX" is never said), as kinds.build_regions hears it."""
    out = {}
    for seg, spec in zip(resolved, inputs.segments):
        if spec.kind in ("highlight", "versus"):
            for j, (code, query) in enumerate(zip(seg.codes, spec.countries)):
                out[f"{seg.id}/{j}"] = atlas.heard_as(code, query)
        elif spec.kind == "regions":
            for j, (entry, info) in enumerate(zip(spec.regions, seg.regions)):
                written = None if entry.region.strip().upper() == info["code"] else entry.region
                out[f"{seg.id}/region-{j}"] = list(dict.fromkeys(p for p in (entry.say, info["name"], written) if p))
    return out


class ScriptStage:
    id = "script"
    revision = 1
    cacheable = False
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: Any) -> ScriptOutput:
        inputs = MapReelInputs.model_validate(payload)
        try:
            check_music(inputs)
            atlas = atlas_for(ctx.config)
            segments = resolve_segments(inputs, atlas, lambda: region_atlas_for(ctx.config))
        except InvalidInput as exc:
            exc.stage = self.id
            raise
        found = lint.check(inputs, estimated_windows(inputs), names=heard_names(inputs, segments, atlas))
        for finding in found:
            if finding.severity == "error":
                raise InvalidInput(finding.message, hint=finding.hint, stage=self.id,
                                   details={"path": "segments[0].narration", "rule": finding.rule})
        return ScriptOutput(inputs=inputs, segments=segments, lint=[asdict(f) for f in found])


def choose_frame(spec: Any, shapes: list[Shape], previous: Frame | None) -> Frame:
    """A segment's landed frame: the previous one when it holds the camera, else its places fitted to the screen.
    A rank sits lower, under its badge and stat card: a held rank whose country the held frame would put under
    them (or off RANK_SAFE at all) is framed afresh inside RANK_SAFE."""
    safe = kinds.RANK_SAFE if spec.kind == "rank" else SAFE
    if spec.camera.hold and previous is not None and spec.kind != "versus":
        if spec.kind != "rank" or all(compose.inside(previous, shape, safe) for shape in shapes):
            return previous
    return compose.segment_frame(shapes, spec.camera.zoom, safe=safe)


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
        look = payload.inputs.look
        world = basemap.world_layer(settings, root, look=look)
        frames, layers, bboxes, panels = {}, {}, {}, {}
        previous = None
        for seg, spec in zip(payload.segments, payload.inputs.segments):
            ctx.cancel.raise_if_cancelled()
            frame = choose_frame(spec, [shape_of(atlas.places[c]) for c in seg.codes], previous)
            # a versus is drawn in its panels; its full-screen union frame is only where the next segment flies from
            frames[seg.id] = frame.to_dict()
            if spec.kind == "versus":
                panels[seg.id] = self._panels(settings, root, atlas, seg.codes, spec.camera.zoom, look)
            else:
                path, bbox = basemap.detail_layer(settings, root, visible_bbox(frame, margin=0.25), look=look)
                layers[seg.id], bboxes[seg.id] = str(path), list(bbox)
            previous = frame
        return GeoOutput(**payload.model_dump(), world=str(world), layers=layers, detail_bboxes=bboxes,
                         frames=frames, panels=panels)

    @staticmethod
    def _panels(settings: Settings, root: Path, atlas: Atlas, codes: list[str], zoom: float,
                look: str = "satellite") -> list[dict]:
        """One panel per country: its own frame for the panel's view, and a sharp layer of what it shows."""
        out = []
        for code, rect in zip(codes, kinds.panel_rects(len(codes))):
            view = (rect[2], rect[3])
            frame = frame_for(shape_of(atlas.places[code]).main, zoom=zoom, view=view, safe=kinds.PANEL_SAFE)
            path, bbox = basemap.detail_layer(settings, root, visible_bbox(frame, margin=0.25, view=view), look=look)
            out.append({"frame": frame.to_dict(), "layer": str(path), "bbox": list(bbox), "rect": list(rect)})
        return out


class _TooLarge(ValueError):
    pass


class _TooManyPixels(ValueError):
    pass


def _download(ref: str, *, user_agent: str = geodata.USER_AGENT) -> tuple[bytes, str]:
    """An http(s) image, streamed and capped at 8 MB: its bytes and content type."""
    with httpx.stream("GET", ref, timeout=30, follow_redirects=True, headers={"User-Agent": user_agent}) as response:
        response.raise_for_status()
        kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if kind not in IMAGE_TYPES:
            raise ValueError(f"not an image ({kind or 'no content type'})")
        chunks, size = [], 0
        deadline = time.monotonic() + IMAGE_DEADLINE_S
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > IMAGE_MAX_BYTES:
                raise _TooLarge("larger than 8 MB")
            if time.monotonic() > deadline:
                raise ValueError(f"the download took too long (over {IMAGE_DEADLINE_S:g} s)")
            chunks.append(chunk)
    return b"".join(chunks), kind


def _is_svg(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:4096].lower())


def _have_pillow() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def _normalise(data: bytes) -> bytes:
    """Any raster image as an RGBA PNG whose longest side is at most IMAGE_MAX_SIDE, alpha kept."""
    if _is_svg(data):
        raise ValueError(SVG_REFUSED)
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # a safety net: Pillow is a base dependency and the stage checks for it first
        raise ValueError(PILLOW_MISSING) from exc
    try:
        with Image.open(io.BytesIO(data)) as img:
            # refused from the header alone: a few KB of PNG can decode to gigabytes
            if img.width * img.height > IMAGE_MAX_PIXELS:
                raise _TooManyPixels(f"{img.width}x{img.height} is over {IMAGE_MAX_PIXELS // 1_000_000} M pixels")
            if img.format == "JPEG":
                img.draft("RGB", (IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))  # decode at a reduced scale where it can
            img.load()
            img = ImageOps.exif_transpose(img).convert("RGBA")
    except _TooManyPixels:
        raise
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(f"not a readable image ({exc})") from exc
    img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _write_atomic(path: Path, data: bytes) -> None:
    """Write through a uniquely named temp file beside it, so concurrent jobs never collide."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
        tmp.write(data)
    try:
        os.replace(tmp.name, path)
    except OSError:
        Path(tmp.name).unlink(missing_ok=True)
        raise


def _write_stable(path: Path, data: bytes) -> Path:
    """Write a content-named file once, atomically, with a fixed mtime so project keys stay stable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        _write_atomic(path, data)
    os.utime(path, (STABLE_MTIME, STABLE_MTIME))
    return path


def _place(png: bytes, dest_dir: Path) -> Path:
    return _write_stable(dest_dir / (hashlib.sha256(png).hexdigest()[:16] + ".png"), png)


def _fetch_image(ref: str, dest_dir: Path) -> Path:
    """A local file or an http(s) URL, normalised to a PNG in the job; raises on anything unusable."""
    if ref.startswith(("http://", "https://")):
        data, kind = _download(ref)
        if kind == "image/svg+xml":
            raise ValueError(SVG_REFUSED)
    else:
        src = Path(ref).expanduser()
        if not src.is_file():
            raise FileNotFoundError(f"no file at {src}")
        if src.suffix.lower() == ".svg":
            raise ValueError(SVG_REFUSED)
        if src.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"{src.suffix or 'no extension'} is not an image type the renderer draws")
        with src.open("rb") as handle:
            data = handle.read(IMAGE_MAX_BYTES + 1)
        if len(data) > IMAGE_MAX_BYTES:
            raise ValueError("larger than 8 MB")
    return _place(_normalise(data), dest_dir)


def _commons_key(ref: str) -> str:
    name = ref.removeprefix("commons:").removeprefix("File:").strip().replace("_", " ")
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def _fetch_commons(ref: str, item_name: str, dest_dir: Path, cache: Path) -> tuple[Path, dict]:
    """A Commons file by id: refused unless its licence is free, cached as a normalised PNG under the shared
    cache so a second reel neither asks Commons nor downloads it again."""
    key = _commons_key(ref)
    png_path, meta_path = cache / f"{key}.png", cache / f"{key}.json"
    if png_path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return _place(png_path.read_bytes(), dest_dir), {"name": item_name, **meta}
        except (OSError, ValueError):
            pass  # a damaged cache entry is fetched again
    hit = commons.info(ref, width=IMAGE_MAX_SIDE)
    if not hit.permitted:
        raise ValueError(f"{hit.title} is not free to use (licence {hit.licence or 'unknown'})")
    if hit.mime == "image/svg+xml" or hit.width > COMMONS_ORIGINAL_MAX:
        data, _ = _download(hit.thumb_url, user_agent=commons.USER_AGENT)  # a PNG rendered at 1024 px
    else:
        try:
            data, _ = _download(hit.url, user_agent=commons.USER_AGENT)
        except _TooLarge:
            data, _ = _download(hit.thumb_url, user_agent=commons.USER_AGENT)
    png = _normalise(data)
    meta = {"source": "commons", "id_or_path": hit.id, "licence": hit.licence, "licence_url": hit.licence_url,
            "attribution": hit.attribution, "restrictions": hit.restrictions}
    _write_stable(png_path, png)
    _write_atomic(meta_path, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    return _place(png, dest_dir), {"name": item_name, **meta}


def _image_cache(ctx: StageContext) -> Path:
    settings = getattr(ctx, "config", None)
    if settings is None:  # outside the engine: keep it with the job
        return ctx.workdir / "commons"
    return geodata.geo_cache(settings) / "images"


class ImagesStage:
    """Item images: Wikimedia Commons ids, URLs and paths, each normalised to a PNG of at most 1024 px and
    credited. Anything that fails becomes a text badge and a warning, never a failed job."""

    id = "images"
    revision = 2
    cacheable = False
    output_model = ImagesOutput

    def run(self, ctx: StageContext, payload: GeoOutput) -> ImagesOutput:
        images: dict[str, str | None] = {}
        credits: dict[str, dict] = {}
        warnings: list[str] = []
        dest = ctx.workdir / "images"
        pillow = _have_pillow()  # checked once, before any network work
        for seg, spec in zip(payload.segments, payload.inputs.segments):
            for j, item in enumerate(getattr(spec, "items", [])):
                key = f"{seg.id}/{j}"
                images[key] = None
                if not item.image:
                    continue
                ctx.cancel.raise_if_cancelled()
                if not pillow:
                    warnings.append(f"{seg.id} {item.name}: image not usable ({PILLOW_MISSING}); shown as a badge")
                    continue
                try:
                    if item.image.startswith("commons:"):
                        path, credit = _fetch_commons(item.image, item.name, dest, _image_cache(ctx))
                    else:
                        path = _fetch_image(item.image, dest)
                        credit = {"name": item.name, "source": "caller", "id_or_path": item.image,
                                  "licence": "caller-supplied", "licence_url": None,
                                  "attribution": "supplied by the script author", "restrictions": None}
                except JobCancelled:
                    raise
                except Exception as exc:  # an unusable image is a badge, never a failed job
                    warnings.append(f"{seg.id} {item.name}: image not usable ({exc}); shown as a badge")
                    continue
                images[key] = str(path)
                credits[key] = credit
        return ImagesOutput(**payload.model_dump(), images=images, image_credits=credits, warnings=warnings)


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


def _long_side(bbox: list[float]) -> float:
    """A detail layer's long side in Mercator units: each layer is at most DETAIL_MAX_PX on that side."""
    return max(bbox[2] - bbox[0], bbox[3] - bbox[1])


class SegmentsStage:
    """Pop times, item layout and one HyperFrames project per segment; script.resolved.json."""

    id = "segments"
    revision = 1
    cacheable = False
    output_model = SegmentsOutput

    def run(self, ctx: StageContext, payload: NarrationOutput) -> SegmentsOutput:
        atlas = atlas_for(ctx.config)
        inputs = payload.inputs
        # the admin-1 data only when a regions segment draws its states
        regions = region_atlas_for(ctx.config) if any(s.kind == "regions" for s in inputs.segments) else None
        warnings = list(payload.warnings)
        warnings.extend(f"{f['rule']}: {f['message']}" for f in payload.lint)
        projects, cues, resolved = [], [], []
        previous_frame, previous_end, previous_id, previous_places = None, None, None, []
        for i, (seg, spec, window) in enumerate(zip(payload.segments, inputs.segments, payload.windows)):
            frame = payload.frames[seg.id]
            versus = spec.kind == "versus"
            # fly or hold is decided on the undrifted frames; the camera itself starts where the previous
            # segment's drift left it, so a cut never snaps the zoom back. A versus cuts in, never flies
            if previous_frame is None or versus:
                start, target, fly = frame, frame, 0
            elif previous_frame == frame:
                start, target, fly = previous_end, previous_end, 0  # held: keep drifting from there
            else:
                start, target, fly = previous_end, frame, FLY_MS
            # the flight takes the short way round: it starts from the copy of the previous camera (and of
            # what it showed) nearest this frame. Shifting the start, not the target, keeps every landed
            # frame in its own world, so x never creeps a world further on each cut
            wrap = round(start["x"] - target["x"]) if fly else 0
            if wrap:
                start = {**start, "x": start["x"] - wrap}
            speech_start = window.speech_offset_ms
            speech_end = window.speech_offset_ms + window.speech_ms
            last_ms = window.duration_ms - POP_TAIL_MS
            landed = Frame(**target)
            built = kinds.build(seg, spec, kinds.KindContext(
                atlas=atlas, landed=landed, fly_ms=fly, speech_start=speech_start, speech_end=speech_end,
                last_ms=last_ms, spoken=payload.spoken[i], images=payload.images,
                panels=payload.panels.get(seg.id, []), regions=regions))
            warnings.extend(built.warnings)
            places, items = built.places, built.items

            words = [{"text": w.text, "start": (w.start_ms - window.start_ms) / 1000,
                      "end": (w.end_ms - window.start_ms) / 1000, "hot": hot}
                     for w, hot in zip(payload.words[i], payload.hot[i])]
            links = {"layers/world.jpg": Path(payload.world)}
            details = []
            if not versus:  # a versus's sharp layers are its panels' (built.links)
                details.append({"url": "layers/detail.jpg", "bbox": payload.detail_bboxes[seg.id]})
                links["layers/detail.jpg"] = Path(payload.layers[seg.id])
            # the flight leaves a sharp country, not the blurry world layer magnified; after a versus there is
            # no one country to leave, so it starts on the world layer
            if fly and previous_id in payload.layers:
                x0, y0, x1, y1 = payload.detail_bboxes[previous_id]
                details.insert(0, {"url": "layers/prev-detail.jpg", "bbox": [x0 - wrap, y0, x1 - wrap, y1]})
                links["layers/prev-detail.jpg"] = Path(payload.layers[previous_id])
                # the sharper of the two on top: a layer's px per Mercator unit only falls as its box grows,
                # so on a zoom out (a country back to the world) the previous country stays above
                details.sort(key=lambda layer: -_long_side(layer["bbox"]))
            layers = [{"url": "layers/world.jpg", "bbox": [0, 0, 1, 1]}, *details]
            camera = {"from": start, "to": target, "flyMs": fly, "drift": 0 if versus else compose.DRIFT}
            doc = compose.segment_document(
                duration_ms=window.duration_ms, camera=camera, layers=layers, places=places,
                previous=[_faded(p, wrap) for p in previous_places] if fly else [],
                items=items, words=words, extras=built.extras, look=inputs.look)
            links.update(built.links)
            directory = compose.write_project(doc, ctx.workdir / "projects" / seg.id, links)
            projects.append(SegmentProject(segment_id=seg.id, index=i, dir=str(directory),
                                           key=compose.project_key(doc, links), nominal_ms=window.duration_ms))
            if fly:
                cues.append({"at_ms": window.start_ms, "sfx": "whoosh"})
            cues.extend({"at_ms": window.start_ms + at, "sfx": sfx} for at, sfx in built.cues)
            resolved.append({"id": seg.id, "kind": spec.kind, "codes": seg.codes, "colours": seg.colours,
                             "window": window.model_dump(), "camera": camera,
                             "places": [{"code": p["code"], "fillMs": p["fillMs"]} for p in places],
                             "items": [{"name": it["name"], "atMs": it["atMs"], "image": it["image"],
                                        "x": it["x"], "y": it["y"]} for it in items], **built.resolved})
            previous_frame, previous_id = frame, seg.id
            previous_places = places if built.shown is None else built.shown
            # the camera never drifts under a versus's panels: the next segment flies from its union frame
            previous_end = dict(frame) if versus else compose.drifted_end(camera, window.duration_ms)
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


def _write_sources(ctx: StageContext, narration: NarrationOutput) -> Path:
    """sources.md for the description, written beside the other artifacts in both modes."""
    inputs = narration.inputs
    licence = narration.licence
    text = sources.render(
        inputs, inputs.segment_ids(), narration.image_credits,
        voice_licence=(licence and (licence.name or licence.attribution or licence.url)) or "licence not stated",
        data=geodata.LICENCES, look=inputs.look, resolved=narration.segments)
    path = _artifacts(ctx) / "sources.md"
    path.write_text(text, encoding="utf-8")
    return path


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
        _write_sources(ctx, payload.narration)
        return PrepOutput(segments=payload, storyboard=str(sheet), renderer=renderer.name, halt_pipeline=True)


def credits(inputs: MapReelInputs) -> list[str]:
    """The reel's credits: the voice and the map data its look draws from (Blue Marble only on satellite)."""
    out = [f"Narration: {inputs.voice}", geodata.NATURAL_EARTH.attribution]
    if inputs.look == "satellite":
        out.append(geodata.BLUE_MARBLE.attribution)
    return out


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
            "credits": credits(inputs)})


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
        if inputs.music == "auto":
            music = synth_music(work / "music.wav", total_ms=timeline.duration_ms)
        else:
            music = Path(inputs.music).expanduser() if inputs.music else None  # checked by the script stage
        audio = mix(ctx.assets.get(narration.audio_asset), cues, music, work / "audio.wav",
                    total_ms=timeline.duration_ms, settings=settings, profile="studio", cancel=ctx.cancel)
        video = mux(joined, audio, outdir / f"{name}.mp4", total_ms=timeline.duration_ms, settings=settings,
                    cancel=ctx.cancel)
        srt = write_srt(timeline.captions, work / f"{name}.srt")
        _write_sources(ctx, narration)
        manifest = {"reel": str(video), "recipe": "mapreel", "mode": "hyperframes",
                    "created": datetime.now(timezone.utc).isoformat(), "durationMs": timeline.duration_ms,
                    "segments": len(prep.tracks), "look": inputs.look, "renderer": prep.renderer,
                    "data": {k: v.model_dump() for k, v in geodata.LICENCES.items()},
                    "credits": options["credits"]}
        manifest_path = outdir / f"{name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return RenderOutput(video=str(video), srt=str(srt), manifest=str(manifest_path),
                            duration_ms=timeline.duration_ms)
