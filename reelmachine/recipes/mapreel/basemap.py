# reelmachine/recipes/mapreel/basemap.py
"""Satellite layers in Mercator: one low-res world for flights, one sharp crop per segment.

The source texture is equirectangular, so Mercator is a row remap: each output row is one
latitude. ffmpeg crops and scales the columns; numpy picks the rows."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

from ... import ffmpeg
from ...config import Settings
from . import geodata
from .geometry import lat_of

WORLD_PX = 2048
DETAIL_MAX_PX = 2160


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
    ys = y0 + (np.arange(out_h) + 0.5) / out_h * (y1 - y0)
    src_rows = (90 - lat_of(ys)) / 180 * sh - row0 - 0.5
    lo = np.clip(np.floor(src_rows).astype(int), 0, strip.shape[0] - 1)
    hi = np.clip(lo + 1, 0, strip.shape[0] - 1)
    t = np.clip(src_rows - lo, 0, 1)[:, None, None]
    return (strip[lo] * (1 - t) + strip[hi] * t).astype(np.uint8)


def world_layer(settings: Settings, root: Path) -> Path:
    dest = Path(root) / "layers" / f"world-{WORLD_PX}.jpg"
    if dest.is_file():
        return dest
    src = geodata.ensure(root, "world")
    scratch = _scratch(root)
    try:
        rgb = _mercator(settings, src, -180.0, 180.0, 0.0, 1.0, WORLD_PX, WORLD_PX, scratch)
        return _encode(settings, rgb, dest, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def detail_layer(settings: Settings, root: Path,
                 bbox: tuple[float, float, float, float]) -> tuple[Path, tuple[float, float, float, float]]:
    x0, y0, x1, y1 = bbox
    y0, y1 = max(0.0, y0), min(1.0, y1)
    key = hashlib.sha256(repr(tuple(round(v, 5) for v in (x0, y0, x1, y1))).encode()).hexdigest()[:16]
    dest = Path(root) / "layers" / f"detail-{key}.jpg"
    actual = (x0, y0, x1, y1)
    if dest.is_file():
        return dest, actual
    src = geodata.ensure(root, "detail")
    info = ffmpeg.probe(src)
    native_w = (x1 - x0) * info.width  # source px across the crop
    long_side = DETAIL_MAX_PX
    aspect = (y1 - y0) / (x1 - x0)
    out_w = int(min(native_w, long_side if aspect <= 1 else long_side / aspect))
    out_w = max(64, out_w)
    out_h = max(64, int(round(out_w * aspect)))
    scratch = _scratch(root)
    try:
        rgb = _mercator(settings, src, x0 * 360 - 180, x1 * 360 - 180, y0, y1, out_w, out_h, scratch)
        return _encode(settings, rgb, dest, scratch), actual
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
