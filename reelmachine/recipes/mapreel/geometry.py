# reelmachine/recipes/mapreel/geometry.py
"""Map geometry in Web Mercator's unit square: x, y in [0, 1], y down."""

from __future__ import annotations

from typing import Sequence

import numpy as np


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
