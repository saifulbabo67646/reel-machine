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
#: px per Mercator unit: the whole world never narrower than the screen; never closer than
#: 6° of longitude across it (the 21600-px texture would upscale past 3×)
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


def frame_for(bbox: tuple[float, float, float, float], *, zoom: float = 1.0) -> Frame:
    x0, y0, x1, y1 = bbox
    sx0, sy0, sx1, sy1 = SAFE
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    s = min((sx1 - sx0) * WIDTH / w, (sy1 - sy0) * HEIGHT / h) * zoom
    s = float(min(MAX_S, max(MIN_S, s)))
    # centre the bbox on the safe area's centre, not the screen's
    cx_px, cy_px = (sx0 + sx1) / 2 * WIDTH, (sy0 + sy1) / 2 * HEIGHT
    x = (x0 + x1) / 2 - (cx_px - WIDTH / 2) / s
    y = (y0 + y1) / 2 - (cy_px - HEIGHT / 2) / s
    return Frame(float(x), float(y), s)


def to_screen(frame: Frame, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=float)
    return np.column_stack([(pts[:, 0] - frame.x) * frame.s + WIDTH / 2,
                            (pts[:, 1] - frame.y) * frame.s + HEIGHT / 2])


def visible_bbox(frame: Frame, margin: float = 0.0) -> tuple[float, float, float, float]:
    hw, hh = WIDTH / 2 / frame.s * (1 + margin), HEIGHT / 2 / frame.s * (1 + margin)
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


def item_anchors(rings_px: list[np.ndarray], n: int, *, step: float = 24.0) -> list[tuple[float, float]]:
    """`n` points inside the place and inside the safe area, spread as far apart as possible
    (greedy farthest-point). A place too small to hold them gets a column at its centre."""
    sx0, sy0, sx1, sy1 = SAFE
    gx, gy = np.meshgrid(np.arange(sx0 * WIDTH + 140, sx1 * WIDTH - 140, step),
                         np.arange(sy0 * HEIGHT + 100, sy1 * HEIGHT - 100, step))
    gx, gy = gx.ravel(), gy.ravel()
    mask = inside(rings_px, gx, gy)
    pts = np.column_stack([gx[mask], gy[mask]])
    if len(pts) < n * 4:
        allpts = np.concatenate(rings_px)
        cx = float(np.clip(allpts[:, 0].mean(), sx0 * WIDTH + 180, sx1 * WIDTH - 180))
        cy = float(np.clip(allpts[:, 1].mean(), sy0 * HEIGHT + 200, sy1 * HEIGHT - 200))
        spacing = 220.0
        top = cy - spacing * (n - 1) / 2
        return [(cx, top + i * spacing) for i in range(n)]
    centre = pts.mean(axis=0)
    first = int(np.argmin(np.hypot(*(pts - centre).T)))
    chosen = [pts[first]]
    dist = np.hypot(*(pts - pts[first]).T)
    while len(chosen) < n:
        k = int(np.argmax(dist))
        chosen.append(pts[k])
        dist = np.minimum(dist, np.hypot(*(pts - pts[k]).T))
    chosen.sort(key=lambda p: (p[1], p[0]))  # read top to bottom, as they are spoken
    return [(float(x), float(y)) for x, y in chosen]
