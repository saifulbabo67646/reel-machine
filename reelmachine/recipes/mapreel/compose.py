"""A segment becomes a document the canvas runtime draws, inside a HyperFrames project."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from .geometry import BADGE_MIN, HEIGHT, SAFE, WIDTH, Frame, Shape, badge_extent, frame_for, item_layout, merc, simplify, simplify_for, to_screen

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
FPS = 30
ITEM_SIZE = 300
DRIFT = 0.04  # the camera zooms in by this fraction after landing ...
DRIFT_MS = 6000  # ... over this long (runtime/pure.mjs cameraAt)


def _runtime_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(RUNTIME_DIR.glob("*.mjs")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


RUNTIME_VERSION = _runtime_version()


def _short_way(boxes: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Shift boxes by whole worlds so the set spans the shortest arc: the cut goes in the widest
    gap between neighbouring centres on the circle (USA + Japan meet across the Pacific)."""
    centres = [(b[0] + b[2]) / 2 for b in boxes]
    order = sorted(range(len(boxes)), key=lambda i: centres[i] % 1.0)
    ring = [centres[i] % 1.0 for i in order]
    gaps = [ring[k + 1] - ring[k] for k in range(len(ring) - 1)] + [ring[0] + 1.0 - ring[-1]]
    cut = int(np.argmax(gaps))  # the boxes after position `cut` move one world west
    out = list(boxes)
    for k, i in enumerate(order):
        shift = -float(np.floor(centres[i])) - (1.0 if cut < len(ring) - 1 and k > cut else 0.0)
        b = boxes[i]
        out[i] = (b[0] + shift, b[1], b[2] + shift, b[3])
    return out


def segment_frame(shapes: list[Shape], zoom: float, safe: tuple[float, float, float, float] = SAFE) -> Frame:
    boxes = _short_way([s.main for s in shapes])
    union = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    return frame_for(union, zoom=zoom, safe=safe)


def _shift(frame: Frame, shape: Shape) -> int:
    """One world-copy shift for the whole shape, from its main cluster, so no part is torn away."""
    return round(frame.x - (shape.main[0] + shape.main[2]) / 2)


def _near(frame: Frame, shape: Shape, ring: np.ndarray) -> np.ndarray:
    shift = _shift(frame, shape)
    return ring + np.array([shift, 0.0]) if shift else ring


def place_rings(shape: Shape, frame: Frame) -> list[list[list[float]]]:
    """A shape's rings in the world copy nearest the frame, simplified to what the frame can show."""
    rings = []
    for ring in shape.rings:
        slim = simplify_for(frame, _near(frame, shape, ring))
        if len(slim) >= 3:
            rings.append([[round(float(x), 7), round(float(y), 7)] for x, y in slim])
    return rings


def place_entry(code: str, name: str, colour: str, shape: Shape, frame: Frame, fill_ms: int,
                label_lonlat: tuple[float, float]) -> dict[str, Any]:
    rings = place_rings(shape, frame)
    lx, ly = merc(label_lonlat[0], label_lonlat[1])
    centre = (shape.main[0] + shape.main[2]) / 2
    lx = float(lx) + round(centre - float(lx)) + _shift(frame, shape)
    return {"code": code, "name": name, "colour": colour, "rings": rings, "fillMs": int(fill_ms),
            "label": {"x": lx, "y": float(ly)}}


def item_entries(items: list[dict[str, Any]], shape: Shape | None, frame: Frame,
                 safe: tuple[float, float, float, float] = SAFE, max_size: int = ITEM_SIZE,
                 min_size: int = BADGE_MIN) -> list[dict[str, Any]]:
    if not items:
        return []
    if shape is None:
        anchors = [(WIDTH / 2, HEIGHT * (0.25 + 0.45 * i / max(1, len(items) - 1))) for i in range(len(items))]
        gap = min((np.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(anchors) for b in anchors[i + 1:]),
                  default=ITEM_SIZE * 2)
        size = int(max(180, min(ITEM_SIZE, gap * 0.95)))
    else:
        # a pixel of tolerance on screen: the same answer, a fraction of the vertices to test
        rings_px = [simplify(to_screen(frame, _near(frame, shape, r)), 1.0) for r in shape.rings]
        anchors, size = item_layout([r for r in rings_px if len(r) >= 3], len(items), max_size=max_size,
                                   min_size=min_size, safe=safe, extent=lambda px: badge_extent(items, px))
    return [{"name": it["name"], "image": it.get("image"), "emoji": it.get("emoji"), "atMs": int(it["atMs"]),
             "x": round(x, 1), "y": round(y, 1), "size": size} for it, (x, y) in zip(items, anchors)]


def drifted_end(camera: dict[str, Any], duration_ms: int) -> dict[str, float]:
    """Where the runtime's camera is at the segment's last instant: `to`, zoomed in by the drift.
    The same formula as cameraAt in runtime/pure.mjs; the next segment starts here so the cut is invisible."""
    to, fly = camera["to"], camera.get("flyMs") or 0
    after = max(0.0, (duration_ms - fly) / DRIFT_MS)
    grow = 1 + camera.get("drift", 0) * min(1.0, after)
    return {"x": to["x"], "y": to["y"], "s": to["s"] * grow}


def segment_document(*, duration_ms: int, camera: dict[str, Any], layers: list[dict[str, Any]],
                     places: list[dict[str, Any]], previous: list[dict[str, Any]], items: list[dict[str, Any]],
                     words: list[dict[str, Any]], legend: str | None = None,
                     extras: dict[str, Any] | None = None, look: str = "satellite") -> dict[str, Any]:
    """`extras` (a kind's own keys: a legend, a rank badge, a stat card) are merged in last. `look` picks the
    runtime's sea, fill, glow and ghost-name styling (pure.mjs lookStyle)."""
    doc = {"width": WIDTH, "height": HEIGHT, "duration": duration_ms / 1000, "camera": camera, "layers": layers,
           "places": places, "previous": previous, "items": items, "words": words, "legend": legend, "look": look}
    return {**doc, **(extras or {})}


_CSS = "html,body{margin:0;background:#000}#root{position:relative;width:100%;height:100%;overflow:hidden}" \
       "#map{position:absolute;inset:0;width:100%;height:100%;display:block}"


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={w}, height={h}" />
<style>{css}</style>
<script>window.__hf = window.__hf || {{}}; window.__mapReady = new Promise((r) => {{ window.__mapResolve = r; }}); window.__hf.buildReady = {{ mapreel: window.__mapReady }};</script>
</head>
<body>
<div id="root" data-composition-id="segment" data-start="0" data-width="{w}" data-height="{h}" data-duration="{duration}" data-no-timeline>
<canvas id="map" width="{w}" height="{h}"></canvas>
</div>
<script id="segment-data" type="application/json">{data}</script>
<script type="module" src="./runtime/main.mjs"></script>
</body>
</html>
"""


def _template_version() -> str:
    return hashlib.sha256((_TEMPLATE + _CSS).encode()).hexdigest()[:12]


def _link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def write_project(doc: dict[str, Any], dest: Path, links: dict[str, Path]) -> Path:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "runtime").mkdir(parents=True)
    for path in RUNTIME_DIR.glob("*.mjs"):
        shutil.copy2(path, dest / "runtime" / path.name)
    for rel, src in links.items():
        _link(Path(src), dest / rel)
    data = json.dumps(doc, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    html = _TEMPLATE.format(w=WIDTH, h=HEIGHT, css=_CSS, duration=f"{doc['duration']:.3f}", data=data)
    (dest / "index.html").write_text(html, encoding="utf-8")
    return dest


def project_key(doc: dict[str, Any], links: dict[str, Path]) -> str:
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True, allow_nan=False).encode())
    digest.update(RUNTIME_VERSION.encode())
    digest.update(_template_version().encode())
    for rel in sorted(links):
        stat = Path(links[rel]).stat()
        digest.update(f"{rel}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()
