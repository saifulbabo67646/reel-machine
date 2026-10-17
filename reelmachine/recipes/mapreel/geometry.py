# reelmachine/recipes/mapreel/geometry.py
"""Map geometry in Web Mercator's unit square: x, y in [0, 1], y down."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .places import Place


def simplify(points: Sequence[Sequence[float]], tolerance: float) -> np.ndarray:
    """Douglas–Peucker on an (n, 2) polyline; keeps both ends."""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 3 or tolerance <= 0:
        return pts
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        a, b = pts[lo], pts[hi]
        seg = b - a
        span = np.hypot(*seg)
        mid = pts[lo + 1:hi]
        if span == 0:
            dist = np.hypot(*(mid - a).T)
        else:
            dist = np.abs(seg[0] * (mid[:, 1] - a[1]) - seg[1] * (mid[:, 0] - a[0])) / span
        i = int(np.argmax(dist))
        if dist[i] > tolerance:
            k = lo + 1 + i
            keep[k] = True
            stack.extend([(lo, k), (k, hi)])
    return pts[keep]


WIDTH, HEIGHT = 1080, 1920
MAX_LAT = 85.05112878
#: px per Mercator unit: the world fills the screen's height (no sky or floor above and below it)
#: unless the places need more width; then never narrower than the screen. Never closer than
#: 6° of longitude across it (the 21600-px texture would upscale past 3×)
MIN_S_FILL = float(HEIGHT)
MIN_S = float(WIDTH)
MAX_S = WIDTH / (6.0 / 360.0)
#: where a framed place sits on screen: clear of the legend at the top and captions below
SAFE = (0.08, 0.10, 0.92, 0.80)  # x0, y0, x1, y1 as fractions of the screen


def merc(lon, lat):
    lon = np.asarray(lon, dtype=float)
    lat = np.clip(np.asarray(lat, dtype=float), -MAX_LAT, MAX_LAT)
    x = (lon + 180.0) / 360.0
    y = 0.5 - np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) / (2 * np.pi)
    return x, y


def lat_of(y):
    return np.degrees(2 * np.arctan(np.exp((0.5 - np.asarray(y, dtype=float)) * 2 * np.pi)) - np.pi / 2)


def _area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return float(abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2)


def _bbox(rings: list[np.ndarray]) -> tuple[float, float, float, float]:
    pts = np.concatenate(rings)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def _gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return float(np.hypot(dx, dy))


@dataclass
class Shape:
    rings: list[np.ndarray]
    main: tuple[float, float, float, float]
    bbox: tuple[float, float, float, float]
    outers: list[np.ndarray] = field(default_factory=list)


def shape_of(place: Place) -> Shape:
    """Project to Mercator, bring parts across the antimeridian next to the main body,
    and find the main landmass cluster the camera frames."""
    polys = []
    for poly in place.polygons:
        rings = []
        for ring in poly:
            arr = np.asarray(ring, dtype=float)
            x, y = merc(arr[:, 0], arr[:, 1])
            rings.append(np.column_stack([x, y]))
        polys.append(rings)
    areas = [_area(p[0]) for p in polys]
    main = int(np.argmax(areas))
    cx = float(polys[main][0][:, 0].mean())
    for rings in polys:  # unwrap: a part more than half a world away moves by one world
        shift = round(cx - float(rings[0][:, 0].mean()))
        if shift:
            for ring in rings:
                ring[:, 0] += shift
    cluster = _bbox([polys[main][0]])
    order = sorted(range(len(polys)), key=lambda i: -areas[i])
    changed = True
    taken = {main}
    while changed:  # grow the cluster with parts close to it (relative to its own size)
        changed = False
        diag = float(np.hypot(cluster[2] - cluster[0], cluster[3] - cluster[1]))
        for i in order:
            if i in taken or areas[i] < areas[main] * 1e-4:
                continue
            box = _bbox([polys[i][0]])
            if _gap(cluster, box) <= 0.1 * diag:
                taken.add(i)
                cluster = (min(cluster[0], box[0]), min(cluster[1], box[1]),
                           max(cluster[2], box[2]), max(cluster[3], box[3]))
                changed = True
    all_rings = [ring for rings in polys for ring in rings]
    return Shape(rings=all_rings, main=cluster, bbox=_bbox(all_rings), outers=[p[0] for p in polys])


@dataclass(frozen=True)
class Frame:
    x: float
    y: float
    s: float

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "s": self.s}


def frame_for(bbox: tuple[float, float, float, float], *, zoom: float = 1.0,
              view: tuple[int, int] = (WIDTH, HEIGHT),
              safe: tuple[float, float, float, float] = SAFE) -> Frame:
    """The frame that fits `bbox` inside the `safe` part of a `view` (w, h) px."""
    x0, y0, x1, y1 = bbox
    sx0, sy0, sx1, sy1 = safe
    view_w, view_h = view
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    s = min((sx1 - sx0) * view_w / w, (sy1 - sy0) * view_h / h) * zoom
    # the floors scale with the view: MIN_S_FILL and MIN_S for the full screen
    fits_filled = w * view_h <= (sx1 - sx0) * view_w
    s = float(min(MAX_S, max(float(view_h) if fits_filled else float(view_w), s)))
    # centre the bbox on the safe area's centre, not the view's
    cx_px, cy_px = (sx0 + sx1) / 2 * view_w, (sy0 + sy1) / 2 * view_h
    x = (x0 + x1) / 2 - (cx_px - view_w / 2) / s
    y = (y0 + y1) / 2 - (cy_px - view_h / 2) / s
    return clamp_frame(Frame(float(x), float(y), s), view)


def clamp_frame(frame: Frame, view: tuple[int, int] = (WIDTH, HEIGHT)) -> Frame:
    """Keep the world's top and bottom edges off screen; a world shorter than the view sits centred."""
    half = view[1] / 2 / frame.s
    if frame.s >= view[1]:
        y = min(max(frame.y, half), 1 - half)
    else:
        y = 0.5
    return Frame(frame.x, float(y), frame.s)


def to_screen(frame: Frame, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=float)
    return np.column_stack([(pts[:, 0] - frame.x) * frame.s + WIDTH / 2,
                            (pts[:, 1] - frame.y) * frame.s + HEIGHT / 2])


def visible_bbox(frame: Frame, margin: float = 0.0,
                 view: tuple[int, int] = (WIDTH, HEIGHT)) -> tuple[float, float, float, float]:
    hw, hh = view[0] / 2 / frame.s * (1 + margin), view[1] / 2 / frame.s * (1 + margin)
    return frame.x - hw, frame.y - hh, frame.x + hw, frame.y + hh


def simplify_for(frame: Frame, ring: np.ndarray) -> np.ndarray:
    return simplify(ring, 1.5 / frame.s)


def inside(rings_px: list[np.ndarray], x: float | np.ndarray, y: float | np.ndarray) -> np.ndarray | bool:
    """Even-odd point-in-polygon over every ring (holes subtract)."""
    xs, ys = np.atleast_1d(np.asarray(x, dtype=float)), np.atleast_1d(np.asarray(y, dtype=float))
    hit = np.zeros(xs.shape, dtype=bool)
    for ring in rings_px:
        ax, ay = ring[:, 0], ring[:, 1]
        bx, by = np.roll(ax, -1), np.roll(ay, -1)
        for i in range(len(ring)):
            cond = (ay[i] > ys) != (by[i] > ys)
            with np.errstate(divide="ignore", invalid="ignore"):
                cross = ax[i] + (ys - ay[i]) * (bx[i] - ax[i]) / (by[i] - ay[i])
            hit ^= cond & (xs < cross)
    return hit if np.ndim(x) else bool(hit[0])


#: a badge's footprint around its anchor, as fractions of its size: the text may be
#: size * 1.1 wide (draw.mjs) and two lines tall
BADGE_HALF_W, BADGE_HALF_H = 0.55, 0.3
#: how far a badge is drawn above and below its anchor, as fractions of its size: its emoji reaches 0.45 above
#: (draw.mjs drawItem: centred 0.23 up, a 0.34 font whose glyph runs a little taller), two lines of text 0.31 below
BADGE_UP, BADGE_DOWN = 0.45, 0.31
#: px a badge rises after it pops (draw.mjs drawItem: 6 px a second), allowed for over a 5 s hold
BADGE_RISE = 30.0
#: the caption band (runtime/draw.mjs CAPTION_Y, CAPTION_HALF): 60 px words at 71 % of the height, stroke and air
CAPTION_BAND = (HEIGHT * 0.71 - 43, HEIGHT * 0.71 + 43)
BADGE_MAX, BADGE_MIN, BADGE_STEP = 300, 180, 20
COLUMN_SPACING = 220.0


def clear_of_captions(y: np.ndarray, size: float, band: tuple[float, float] = CAPTION_BAND) -> np.ndarray:
    """Which anchors keep a badge of `size`, as drawn (emoji above, text below), off the caption band."""
    y = np.asarray(y, dtype=float)
    return (y + BADGE_DOWN * size <= band[0]) | (y - BADGE_UP * size - BADGE_RISE >= band[1])


def _boxes_overlap(a: tuple[float, float], b: tuple[float, float], size: float) -> bool:
    return abs(a[0] - b[0]) < 2 * BADGE_HALF_W * size and abs(a[1] - b[1]) < 2 * BADGE_HALF_H * size


def _box_points(x: np.ndarray, y: np.ndarray, hw: float, hh: float) -> list[tuple[np.ndarray, np.ndarray]]:
    """Corners and edge midpoints of each box."""
    return [(x + fx * hw, y + fy * hh) for fx in (-1, 0, 1) for fy in (-1, 0, 1) if fx or fy]


def item_layout(rings_px: list[np.ndarray], n: int, *, max_size: int = BADGE_MAX, min_size: int = BADGE_MIN,
                step: float = 24.0, cell: float = 12.0,
                safe: tuple[float, float, float, float] = SAFE) -> tuple[list[tuple[float, float]], int]:
    """`n` badge anchors and one badge size. Every badge box (size*1.1 x size*0.6) lies inside
    the place and the safe area, every badge as drawn stays off the caption band, and no two boxes overlap; anchors spread as far apart as they
    can (greedy farthest-point). The size steps down to `min_size` until they fit. A place too
    small even then gets a column at its centre, which may overhang."""
    sx0, sy0, sx1, sy1 = safe
    # inside-mask on a fine raster over the safe area; a summed-area table answers "is this whole box inside"
    rx = np.arange(sx0 * WIDTH, sx1 * WIDTH + cell, cell)
    ry = np.arange(sy0 * HEIGHT, sy1 * HEIGHT + cell, cell)
    mx, my = np.meshgrid(rx, ry)
    out = (~inside(rings_px, mx.ravel(), my.ravel())).reshape(mx.shape).astype(np.int32)
    table = np.zeros((out.shape[0] + 1, out.shape[1] + 1), dtype=np.int32)
    table[1:, 1:] = out.cumsum(0).cumsum(1)
    for size in range(max_size, min_size - 1, -BADGE_STEP):
        hw, hh = BADGE_HALF_W * size, BADGE_HALF_H * size
        gx, gy = np.meshgrid(np.arange(sx0 * WIDTH + hw, sx1 * WIDTH - hw + 1e-9, step),
                             np.arange(sy0 * HEIGHT + hh, sy1 * HEIGHT - hh + 1e-9, step))
        gx, gy = gx.ravel(), gy.ravel()
        if not len(gx):
            continue
        # raster cells covering the box, one cell wider on every side
        c0 = np.clip(np.floor((gx - hw - rx[0]) / cell).astype(int), 0, len(rx) - 1)
        c1 = np.clip(np.ceil((gx + hw - rx[0]) / cell).astype(int), 0, len(rx) - 1) + 1
        r0 = np.clip(np.floor((gy - hh - ry[0]) / cell).astype(int), 0, len(ry) - 1)
        r1 = np.clip(np.ceil((gy + hh - ry[0]) / cell).astype(int), 0, len(ry) - 1) + 1
        outside = table[r1, c1] - table[r0, c1] - table[r1, c0] + table[r0, c0]
        keep = (outside == 0) & clear_of_captions(gy, size)
        gx, gy = gx[keep], gy[keep]
        if len(gx):  # exact check of the box outline
            ok = np.ones(len(gx), dtype=bool)
            for px, py in _box_points(gx, gy, hw, hh):
                ok &= inside(rings_px, px, py)
            gx, gy = gx[ok], gy[ok]
        if len(gx) < n:
            continue
        pts = np.column_stack([gx, gy])
        centre = pts.mean(axis=0)
        first = int(np.argmin(np.hypot(*(pts - centre).T)))
        chosen = [pts[first]]
        dist = np.hypot(*(pts - pts[first]).T)
        free = ~((np.abs(pts[:, 0] - pts[first][0]) < 2 * hw) & (np.abs(pts[:, 1] - pts[first][1]) < 2 * hh))
        while len(chosen) < n and free.any():
            k = int(np.argmax(np.where(free, dist, -1.0)))
            chosen.append(pts[k])
            dist = np.minimum(dist, np.hypot(*(pts - pts[k]).T))
            free &= ~((np.abs(pts[:, 0] - pts[k][0]) < 2 * hw) & (np.abs(pts[:, 1] - pts[k][1]) < 2 * hh))
        if len(chosen) < n:
            continue
        chosen.sort(key=lambda p: (p[1], p[0]))  # read top to bottom, as they are spoken
        return [(float(x), float(y)) for x, y in chosen], size
    # too narrow for whole badges even at min_size (Italy's boot, Japan): best effort at min_size,
    # anchors inside the place, boxes still disjoint, the least overhang first
    size = min_size
    hw, hh = BADGE_HALF_W * size, BADGE_HALF_H * size
    gx, gy = np.meshgrid(np.arange(sx0 * WIDTH + hw, sx1 * WIDTH - hw + 1e-9, step),
                         np.arange(sy0 * HEIGHT + hh, sy1 * HEIGHT - hh + 1e-9, step))
    gx, gy = gx.ravel(), gy.ravel()
    ok = (inside(rings_px, gx, gy) & clear_of_captions(gy, size)) if len(gx) else np.zeros(0, dtype=bool)
    gx, gy = gx[ok], gy[ok]
    if len(gx) >= n:
        c0 = np.clip(np.floor((gx - hw - rx[0]) / cell).astype(int), 0, len(rx) - 1)
        c1 = np.clip(np.ceil((gx + hw - rx[0]) / cell).astype(int), 0, len(rx) - 1) + 1
        r0 = np.clip(np.floor((gy - hh - ry[0]) / cell).astype(int), 0, len(ry) - 1)
        r1 = np.clip(np.ceil((gy + hh - ry[0]) / cell).astype(int), 0, len(ry) - 1) + 1
        outside = table[r1, c1] - table[r0, c1] - table[r1, c0] + table[r0, c0]
        chosen: list[tuple[float, float]] = []
        for k in np.argsort(outside, kind="stable"):
            p = (float(gx[k]), float(gy[k]))
            if all(not _boxes_overlap(p, q, size) for q in chosen):
                chosen.append(p)
                if len(chosen) == n:
                    chosen.sort(key=lambda q: (q[1], q[0]))
                    return chosen, size
    allpts = np.concatenate(rings_px)
    cx = float(np.clip(allpts[:, 0].mean(), sx0 * WIDTH + 180, sx1 * WIDTH - 180))
    cy = float(np.clip(allpts[:, 1].mean(), sy0 * HEIGHT + 200, sy1 * HEIGHT - 200))
    top = cy - COLUMN_SPACING * (n - 1) / 2
    size = int(max(min_size, min(max_size, COLUMN_SPACING * 0.95)))
    return [(cx, top + i * COLUMN_SPACING) for i in range(n)], size


def item_anchors(rings_px: list[np.ndarray], n: int, **kwargs) -> list[tuple[float, float]]:
    return item_layout(rings_px, n, **kwargs)[0]
