"""One shot becomes one self-contained HyperFrames project.

Everything the shot needs is in the directory: index.html with the shot document inline,
our runtime, and Three.js. Nothing is fetched at render time, and the directory's key is a
hash of the document plus the runtime and renderer versions, so an unchanged shot is
never rendered twice.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...render.hyperframes import HYPERFRAMES_VERSION
from .geometry import dims
from .models import Explainer3DInputs, ShapeSpec
from .state import ShotPlan
from .timing import ShotWindow
from .world import Ctx

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
LOOKS_DIR = HERE / "looks"
WIDTH, HEIGHT, FPS = 1080, 1920, 30


def _runtime_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(RUNTIME_DIR.rglob("*")):
        if path.is_file() and "tests" not in path.parts:
            digest.update(path.relative_to(RUNTIME_DIR).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


RUNTIME_VERSION = _runtime_version()


@lru_cache(maxsize=None)
def load_look(name: str) -> dict[str, Any]:
    return json.loads((LOOKS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def look_timing(name: str) -> dict[str, int]:
    """Narration padding for a look: tight for cinematic cuts, roomy for diorama."""
    timing = load_look(name).get("timing") or {}
    return {k: int(timing[k]) for k in ("lead_ms", "tail_ms", "min_ms") if k in timing}


def shot_document(
    inputs: Explainer3DInputs,
    plan: ShotPlan,
    window: ShotWindow,
    words: list[dict[str, Any]],
    ctx: Ctx,
    *,
    render_ms: int,
) -> dict[str, Any]:
    actors = {}
    for key, member in inputs.cast.items():
        shape = member.asset
        assert isinstance(shape, ShapeSpec)
        actors[key] = {"shape": shape.model_dump(), "dims": list(dims(shape))}
    shot = next(s for s in inputs.shot_list() if s.id == plan.shot_id)
    return {
        "width": WIDTH, "height": HEIGHT, "fps": FPS,
        "duration": window.duration_ms / 1000.0,
        "renderDuration": render_ms / 1000.0,
        "set": inputs.set.model_dump(),
        "look": load_look(inputs.look),
        "ctx": ctx,
        "actors": actors,
        "world": plan.world,
        "actions": plan.actions,
        "camera": {k: plan.camera[k] for k in ("move", "params", "start", "target", "easeIn", "easeOut")},
        "captions": {"words": words, "label": shot.label, "title": inputs.title},
        "runtime": RUNTIME_VERSION,
    }


def project_key(doc: dict[str, Any]) -> str:
    blob = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{blob}|{RUNTIME_VERSION}|{HYPERFRAMES_VERSION}".encode()).hexdigest()


_CSS = """
html,body{margin:0;background:#000}
#root{position:relative;width:100%;height:100%;overflow:hidden}
#stage3d{position:absolute;inset:0;width:100%;height:100%;display:block}
#overlay{position:absolute;inset:0;pointer-events:none;font-family:sans-serif}
#title{display:none;position:absolute;left:8%;right:8%;top:6%;padding:16px 24px;text-align:center;font-weight:800;font-size:52px;line-height:1.15}
#label{display:none;position:absolute;left:6%;top:16%;padding:10px 24px;border-radius:999px;font-weight:700;font-size:40px}
#captions{position:absolute;left:7%;right:7%;bottom:14%;text-align:center;font-weight:800;line-height:1.25}
#captions span{display:inline-block;margin:0 0.12em}
"""


def write_project(doc: dict[str, Any], dest: Path) -> Path:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "runtime").mkdir(parents=True)
    for path in RUNTIME_DIR.glob("*.mjs"):
        shutil.copy2(path, dest / "runtime" / path.name)
    shutil.copytree(RUNTIME_DIR / "vendor", dest / "vendor")
    data = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={WIDTH}, height={HEIGHT}" />
<style>{_CSS}</style>
<script type="importmap">{{"imports": {{"three": "./vendor/three.module.js"}}}}</script>
</head>
<body>
<div id="root" data-composition-id="shot" data-start="0" data-width="{WIDTH}" data-height="{HEIGHT}" data-duration="{doc['renderDuration']:.3f}" data-no-timeline>
<canvas id="stage3d"></canvas>
<div id="overlay"><div id="title"></div><div id="label"></div><div id="captions"></div></div>
</div>
<script id="shot-data" type="application/json">{data}</script>
<script type="module" src="./runtime/main.mjs"></script>
</body>
</html>
"""
    (dest / "index.html").write_text(html, encoding="utf-8")
    return dest
