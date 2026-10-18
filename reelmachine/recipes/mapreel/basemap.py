# reelmachine/recipes/mapreel/basemap.py
"""Map layers in Mercator: one low-res world for flights, one sharp crop per segment.

The satellite look reprojects NASA's Blue Marble. Its texture is equirectangular, so Mercator is a row
remap: each output row is one latitude. ffmpeg crops and scales the columns; numpy picks the rows.
The flat and dark looks draw Natural Earth's countries instead (Pillow, no texture), at the same pixel
geometry, so the runtime draws every look's layers the same way."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np

from ... import ffmpeg
from ...config import Settings
from . import geodata
from .geometry import MAX_LAT, WIDTH, lat_of, merc

WORLD_PX = 2048
DETAIL_MAX_PX = 2160
#: the detail texture's width (blue-marble-21600): a drawn crop has the size the satellite crop would
DETAIL_NATIVE_PX = 21600

#: the drawn looks' colours (runtime/pure.mjs lookStyle holds the same sea and land for the canvas)
VECTOR_LOOKS: dict[str, dict[str, str]] = {
    "flat": {"sea": "#a8d8f0", "land": "#f3efe4", "border": "#9a948a"},
    "dark": {"sea": "#070b14", "land": "#1a2233", "border": "#34425e"},
}
#: bumped when the drawing changes, so cached drawn layers are redrawn
VECTOR_REV = 1
#: borders are this many px wide on a 1024-px-wide world, and the same on screen in a detail crop
BORDER_PX = 2.0
#: a detail crop spans the screen's width plus a quarter (stages: visible_bbox(frame, margin=0.25))
DETAIL_SPAN_PX = WIDTH * 1.25
SUPERSAMPLE = 2  # drawn at twice the size and scaled down: smooth coasts

#: NASA's Blue Marble has a hard edge along this latitude (row 150 of 2700, row 600 of 10800: measured)
SEAM_LAT = 80.0
#: the seam is cross-faded over this many source rows each side of it
SEAM_FADE_PX = 12
#: bumped when the satellite reprojection changes (2: the seam fade), so cached layers are rebuilt
SATELLITE_REV = 2


def _raw(settings: Settings, src: Path, *, x: int, y: int, w: int, h: int, out_w: int, out: Path) -> np.ndarray:
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(src),
                "-vf", f"crop={w}:{h}:{x}:{y},scale={out_w}:{h}:flags=area",
                "-f", "rawvideo", "-pix_fmt", "rgb24", str(out)])
    try:
        return np.fromfile(out, dtype=np.uint8).reshape(h, out_w, 3)
    finally:
        out.unlink(missing_ok=True)


def _encode(settings: Settings, rgb: np.ndarray, dest: Path, scratch: Path) -> Path:
    """Encode in the job's own scratch folder, then move into the shared cache in one step."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = scratch / "layer.rgb"
    np.ascontiguousarray(rgb).tofile(raw)
    h, w = rgb.shape[:2]
    fd, name = tempfile.mkstemp(dir=dest.parent, prefix=dest.stem + ".", suffix=".partial.jpg")
    os.close(fd)
    partial = Path(name)
    try:
        ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", str(raw), "-q:v", "3", str(partial)])
        os.replace(partial, dest)
    finally:
        raw.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    return dest


def _scratch(root: Path) -> Path:
    """A private scratch folder: two jobs building layers at once never share a file name."""
    base = Path(root) / "layers" / "scratch"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base))


def _mercator(settings: Settings, src: Path, lon0: float, lon1: float, y0: float, y1: float,
              out_w: int, out_h: int, scratch: Path) -> np.ndarray:
    """Mercator image of [lon0, lon1] × [y0, y1]; lon may run past ±180 (wraps)."""
    info = ffmpeg.probe(src)
    sw, sh = info.width, info.height
    lat_top, lat_bot = float(lat_of(y0)), float(lat_of(y1))
    row0 = max(0, int(math.floor((90 - lat_top) / 180 * sh)))
    row1 = min(sh, int(math.ceil((90 - lat_bot) / 180 * sh)) + 1)
    seam = int(round((90 - SEAM_LAT) / 180 * sh))
    fade = row0 < seam + SEAM_FADE_PX and row1 > seam - SEAM_FADE_PX
    if fade:  # the fade reads the band's rows on both sides, even past the crop's own rows
        row0, row1 = max(0, min(row0, seam - SEAM_FADE_PX)), min(sh, max(row1, seam + SEAM_FADE_PX))
    pieces, span = [], lon1 - lon0
    lon = lon0
    while lon < lon1 - 1e-9:  # split at the antimeridian
        wrapped = ((lon + 180) % 360) - 180
        end = min(lon1, lon + (180 - wrapped))
        c0 = int(round((wrapped + 180) / 360 * sw))
        c1 = max(c0 + 1, int(round((wrapped + (end - lon) + 180) / 360 * sw)))
        width_px = max(1, int(round(out_w * (end - lon) / span)))
        pieces.append(_raw(settings, src, x=c0, y=row0, w=min(c1, sw) - c0, h=row1 - row0,
                           out_w=width_px, out=scratch / f"piece-{len(pieces)}.rgb"))
        lon = end
    strip = np.concatenate(pieces, axis=1)
    if strip.shape[1] != out_w:  # rounding across pieces
        idx = np.linspace(0, strip.shape[1] - 1, out_w).round().astype(int)
        strip = strip[:, idx]
    if fade:
        strip = _fade_seam(strip, seam - row0)
    ys = y0 + (np.arange(out_h) + 0.5) / out_h * (y1 - y0)
    src_rows = (90 - lat_of(ys)) / 180 * sh - row0 - 0.5
    lo = np.clip(np.floor(src_rows).astype(int), 0, strip.shape[0] - 1)
    hi = np.clip(lo + 1, 0, strip.shape[0] - 1)
    t = np.clip(src_rows - lo, 0, 1)[:, None, None]
    return (strip[lo] * (1 - t) + strip[hi] * t).astype(np.uint8)


def _fade_seam(strip: np.ndarray, seam: int) -> np.ndarray:
    """Cross-fade the rows around `seam` (the first row south of the texture's edge). Each side's texture is
    mirrored across the seam, and the two are blended with a ramp from all-north to all-south over the band:
    the edge goes, and neither side is smeared into a flat gradient."""
    n = SEAM_FADE_PX
    if seam - n < 0 or seam + n > strip.shape[0]:
        return strip
    north = strip[seam - n:seam].astype(np.float32)  # rows seam-n .. seam-1
    south = strip[seam:seam + n].astype(np.float32)  # rows seam .. seam+n-1
    north_ext = np.concatenate([north, north[::-1]])  # the north texture continued south, mirrored
    south_ext = np.concatenate([south[::-1], south])  # the south texture continued north, mirrored
    w = ((np.arange(2 * n) + 0.5) / (2 * n))[:, None, None]
    out = strip.copy()
    out[seam - n:seam + n] = np.round(north_ext * (1 - w) + south_ext * w).astype(np.uint8)
    return out


def layer_name(look: str, bbox: tuple[float, float, float, float] | None) -> str:
    """A layer's file name in the cache: the world (bbox None) or a crop, per look. Satellite names carry
    SATELLITE_REV; a drawn look's carry the look and VECTOR_REV."""
    if bbox is None:
        if look == "satellite":
            return f"world-v{SATELLITE_REV}-{WORLD_PX}.jpg"
        return f"world-{look}-v{VECTOR_REV}-{WORLD_PX}.jpg"
    rounded = tuple(round(v, 5) for v in bbox)
    keyed = (look, SATELLITE_REV, *rounded) if look == "satellite" else (look, VECTOR_REV, *rounded)
    return f"detail-{hashlib.sha256(repr(keyed).encode()).hexdigest()[:16]}.jpg"


def world_layer(settings: Settings, root: Path, look: str = "satellite") -> Path:
    dest = Path(root) / "layers" / layer_name(look, None)
    if dest.is_file():
        return dest
    if look != "satellite":
        return _vector_layer(root, look, 0.0, 0.0, 1.0, 1.0, WORLD_PX, WORLD_PX, dest)
    src = geodata.ensure(root, "world")
    scratch = _scratch(root)
    try:
        rgb = _mercator(settings, src, -180.0, 180.0, 0.0, 1.0, WORLD_PX, WORLD_PX, scratch)
        return _encode(settings, rgb, dest, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _detail_size(native_w: float, x0: float, y0: float, x1: float, y1: float) -> tuple[int, int]:
    aspect = (y1 - y0) / (x1 - x0)
    out_w = int(min(native_w, DETAIL_MAX_PX if aspect <= 1 else DETAIL_MAX_PX / aspect))
    out_w = max(64, out_w)
    return out_w, max(64, int(round(out_w * aspect)))


def detail_layer(settings: Settings, root: Path, bbox: tuple[float, float, float, float],
                 look: str = "satellite") -> tuple[Path, tuple[float, float, float, float]]:
    x0, y0, x1, y1 = bbox
    y0, y1 = max(0.0, y0), min(1.0, y1)
    dest = Path(root) / "layers" / layer_name(look, (x0, y0, x1, y1))
    actual = (x0, y0, x1, y1)
    if dest.is_file():
        return dest, actual
    if look != "satellite":
        out_w, out_h = _detail_size((x1 - x0) * DETAIL_NATIVE_PX, x0, y0, x1, y1)
        return _vector_layer(root, look, x0, y0, x1, y1, out_w, out_h, dest), actual
    src = geodata.ensure(root, "detail")
    info = ffmpeg.probe(src)
    out_w, out_h = _detail_size((x1 - x0) * info.width, x0, y0, x1, y1)  # source px across the crop
    scratch = _scratch(root)
    try:
        rgb = _mercator(settings, src, x0 * 360 - 180, x1 * 360 - 180, y0, y1, out_w, out_h, scratch)
        return _encode(settings, rgb, dest, scratch), actual
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# -- the drawn looks ------------------------------------------------------------------------------------


_CUT = 180.0 - 1e-6  # Natural Earth splits a country at ±180: those edges are cuts, not borders
_POLE = MAX_LAT - 1e-6  # Antarctica closes along the pole, which Mercator clips to the map's bottom edge


@lru_cache(maxsize=2)
def _rings(path: str, _mtime: int) -> tuple[tuple[np.ndarray, np.ndarray, np.ndarray, bool], ...]:
    """Every admin-0 ring in Mercator: (points (n, 2), which edges to stroke (n - 1), x extent, outer?).
    A ring that jumps across the antimeridian (Natural Earth splits its countries there, so only other data
    would) is unwrapped into one piece east of it; the drawing repeats it a world either side."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for feature in data["features"]:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for poly in polys:
            for k, ring in enumerate(poly):
                arr = np.asarray([p[:2] for p in ring], dtype=float)
                if len(arr) < 3:
                    continue
                lon, lat = arr[:, 0], arr[:, 1]
                polar = np.abs(lat) >= _POLE
                jumps = (np.abs(np.diff(lon)) > 180) & ~(polar[:-1] & polar[1:])
                if jumps.any():
                    lon = np.unwrap(lon, period=360.0)
                cut = np.abs(arr[:, 0]) >= _CUT
                stroke = ~((cut[:-1] & cut[1:]) | (polar[:-1] & polar[1:]))
                x, y = merc(lon, lat)
                pts = np.column_stack([x, y])
                out.append((pts, stroke, (float(x.min()), float(x.max())), k == 0))
    return tuple(out)


def _runs(px: np.ndarray, stroke: np.ndarray) -> list[list[tuple[float, float]]]:
    """The ring's strokeable edges as polylines."""
    runs, current = [], []
    for i, keep in enumerate(stroke):
        if keep:
            if not current:
                current = [tuple(px[i])]
            current.append(tuple(px[i + 1]))
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _vector_layer(root: Path, look: str, x0: float, y0: float, x1: float, y1: float,
                  out_w: int, out_h: int, dest: Path | None = None) -> Path:
    """Natural Earth's countries over the look's sea, in Mercator [x0, x1] × [y0, y1] at out_w × out_h px: the
    pixel geometry of the satellite layer of that box. Holes (Lesotho in South Africa) are other countries, so
    every outer ring is land and every ring is a border."""
    from PIL import Image, ImageDraw

    colours = VECTOR_LOOKS[look]
    countries = geodata.ensure(root, "countries")
    rings = _rings(str(countries), countries.stat().st_mtime_ns)
    ss = SUPERSAMPLE
    w, h = out_w * ss, out_h * ss
    kx, ky = w / (x1 - x0), h / (y1 - y0)
    # px per screen px: the world as if 1024 px wide; a crop as stretched over DETAIL_SPAN_PX of screen
    ref = kx / ss / 1024 if x1 - x0 >= 1 else out_w / DETAIL_SPAN_PX
    width = max(1, int(round(BORDER_PX * ref * ss)))
    land = Image.new("L", (w, h), 0)
    lines = Image.new("L", (w, h), 0)
    fill, edge = ImageDraw.Draw(land), ImageDraw.Draw(lines)
    pad = width / kx  # a border just outside the box still reaches into it
    for pts, stroke, (rx0, rx1), outer in rings:
        for shift in (-1.0, 0.0, 1.0):  # the world repeats east and west; a crop may run past ±180
            if rx1 + shift < x0 - pad or rx0 + shift > x1 + pad:
                continue
            px = np.column_stack([(pts[:, 0] + shift - x0) * kx, (pts[:, 1] - y0) * ky])
            if px[:, 1].max() < -width or px[:, 1].min() > h + width:
                continue
            # one point per supersampled px: a 10m coast has far more vertices than a layer has px
            snapped = np.round(px)
            keep = np.ones(len(px), dtype=bool)
            keep[1:] = (snapped[1:] != snapped[:-1]).any(axis=1)
            idx = np.flatnonzero(keep)
            if len(idx) < 2:
                continue
            slim = snapped[idx]
            # an edge between kept points is a border unless every original edge it stands for is a cut
            slim_stroke = np.array([stroke[a:b].any() for a, b in zip(idx[:-1], idx[1:])], dtype=bool)
            if outer and len(slim) >= 3:
                fill.polygon([tuple(p) for p in slim], fill=255)
            for run in _runs(slim, slim_stroke):
                edge.line(run, fill=255, width=width, joint="curve")
    image = Image.new("RGB", (w, h), colours["sea"])
    image.paste(Image.new("RGB", (w, h), colours["land"]), mask=land)
    image.paste(Image.new("RGB", (w, h), colours["border"]), mask=lines)
    image = image.resize((out_w, out_h), Image.LANCZOS)
    if dest is None:
        dest = Path(root) / "layers" / layer_name(look, (x0, y0, x1, y1))
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=dest.parent, prefix=dest.stem + ".", suffix=".partial.jpg")
    os.close(fd)
    partial = Path(name)
    try:
        image.save(partial, "JPEG", quality=92, subsampling=0)
        os.replace(partial, dest)
    finally:
        partial.unlink(missing_ok=True)
    return dest
