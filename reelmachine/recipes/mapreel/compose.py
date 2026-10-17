"""A segment becomes a document the canvas runtime draws, inside a HyperFrames project."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from .geometry import HEIGHT, WIDTH, Frame, Shape, frame_for, item_anchors, merc, simplify_for, to_screen

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
FPS = 30
ITEM_SIZE = 300


def _runtime_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(RUNTIME_DIR.glob("*.mjs")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


RUNTIME_VERSION = _runtime_version()


def segment_frame(shapes: list[Shape], zoom: float) -> Frame:
    boxes = [s.main for s in shapes]
    union = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    return frame_for(union, zoom=zoom)


def _near(frame: Frame, ring: np.ndarray) -> np.ndarray:
    shift = round(frame.x - float(ring[:, 0].mean()))
    return ring + np.array([shift, 0.0]) if shift else ring


def place_entry(code: str, name: str, colour: str, shape: Shape, frame: Frame, fill_ms: int,
                label_lonlat: tuple[float, float]) -> dict[str, Any]:
    rings = []
    for ring in shape.rings:
        slim = simplify_for(frame, _near(frame, ring))
        if len(slim) >= 3:
            rings.append([[round(float(x), 7), round(float(y), 7)] for x, y in slim])
    lx, ly = merc(label_lonlat[0], label_lonlat[1])
    lx = float(lx) + round(frame.x - float(lx))
    return {"code": code, "name": name, "colour": colour, "rings": rings, "fillMs": int(fill_ms),
            "label": {"x": lx, "y": float(ly)}}


def item_entries(items: list[dict[str, Any]], shape: Shape | None, frame: Frame) -> list[dict[str, Any]]:
    if not items:
        return []
    if shape is None:
        anchors = [(WIDTH / 2, HEIGHT * (0.25 + 0.45 * i / max(1, len(items) - 1))) for i in range(len(items))]
    else:
        rings_px = [to_screen(frame, _near(frame, r)) for r in shape.rings]
        anchors = item_anchors(rings_px, len(items))
    gap = min((np.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(anchors) for b in anchors[i + 1:]),
              default=ITEM_SIZE * 2)
    size = int(max(180, min(ITEM_SIZE, gap * 0.95)))
    return [{"name": it["name"], "image": it.get("image"), "emoji": it.get("emoji"), "atMs": int(it["atMs"]),
             "x": round(x, 1), "y": round(y, 1), "size": size} for it, (x, y) in zip(items, anchors)]


def segment_document(*, duration_ms: int, camera: dict[str, Any], layers: list[dict[str, Any]],
                     places: list[dict[str, Any]], previous: list[dict[str, Any]], items: list[dict[str, Any]],
                     words: list[dict[str, Any]], legend: str | None) -> dict[str, Any]:
    return {"width": WIDTH, "height": HEIGHT, "duration": duration_ms / 1000, "camera": camera, "layers": layers,
            "places": places, "previous": previous, "items": items, "words": words, "legend": legend}


_CSS = "html,body{margin:0;background:#000}#root{position:relative;width:100%;height:100%;overflow:hidden}" \
       "#map{position:absolute;inset:0;width:100%;height:100%;display:block}"


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
    data = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={WIDTH}, height={HEIGHT}" />
<style>{_CSS}</style>
<script>window.__hf = window.__hf || {{}}; window.__mapReady = new Promise((r) => {{ window.__mapResolve = r; }}); window.__hf.buildReady = {{ mapreel: window.__mapReady }};</script>
</head>
<body>
<div id="root" data-composition-id="segment" data-start="0" data-width="{WIDTH}" data-height="{HEIGHT}" data-duration="{doc['duration']:.3f}" data-no-timeline>
<canvas id="map" width="{WIDTH}" height="{HEIGHT}"></canvas>
</div>
<script id="segment-data" type="application/json">{data}</script>
<script type="module" src="./runtime/main.mjs"></script>
</body>
</html>
"""
    (dest / "index.html").write_text(html, encoding="utf-8")
    return dest


def project_key(doc: dict[str, Any], links: dict[str, Path]) -> str:
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True).encode())
    digest.update(RUNTIME_VERSION.encode())
    for rel in sorted(links):
        stat = Path(links[rel]).stat()
        digest.update(f"{rel}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()
