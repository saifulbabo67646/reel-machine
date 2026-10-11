"""One shot becomes one self-contained HyperFrames project.

Everything the shot needs is in the directory: index.html with the shot document inline,
our runtime, and Three.js. Nothing is fetched at render time, and the directory's key is a
hash of the document plus the runtime and renderer versions, so an unchanged shot is
never rendered twice.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...render.hyperframes import HYPERFRAMES_VERSION
from .geometry import dims
from .assets import compound_dims
from .models import CompoundSpec, Explainer3DInputs, ShapeSpec
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


def _model_url(source: str, main: str) -> str:
    provider, _, ident = source.partition(":")
    return f"assets/models/{provider}/{ident}/{main}"


def shot_document(
    inputs: Explainer3DInputs,
    plan: ShotPlan,
    window: ShotWindow,
    words: list[dict[str, Any]],
    ctx: Ctx,
    *,
    render_ms: int,
    models: dict[str, Any] | None = None,
    env: Any = None,
    resolved_set: Any = None,
    card: str | None = None,
    hot: list[bool] | None = None,
    total_ms: int | None = None,
) -> dict[str, Any]:
    actors = {}
    for key, member in inputs.cast.items():
        asset, resolved = member.asset, (models or {}).get(key)
        if isinstance(asset, ShapeSpec):
            actors[key] = {"shape": asset.model_dump(), "dims": list(dims(asset)), "count": asset.count}
        elif isinstance(asset, CompoundSpec):
            actors[key] = {"compound": asset.model_dump(mode="json"), "dims": list(compound_dims(asset)),
                           "count": asset.count}
        else:
            measured = list(resolved.dims) if resolved else [asset.size or asset.height or 0.3] * 3
            actors[key] = {
                "model": {"url": _model_url(asset.model, resolved.main if resolved else ""),
                          "bounds": [list(b) for b in resolved.bounds] if resolved and resolved.bounds else None,
                          "container": asset.container},
                "dims": measured, "count": asset.count, "colour": asset.colour,
            }
    shot = next(s for s in inputs.shot_list() if s.id == plan.shot_id)
    if resolved_set is None:
        from .sets import reel_sets

        resolved_set = reel_sets(inputs)[0]
    key = resolved_set.key
    env_doc = None
    if env is not None:
        env_doc = {
            "files": {rel: f"assets/sets/{key}/{rel}" for rel in sorted(env.files)},
            "dressing": [{"url": _model_url(d["source"], d["main"]), "dims": d["dims"],
                          "at": list(ctx["anchors"][d["at"]]) if isinstance(d["at"], str) else list(d["at"]),
                          "yaw": d["yaw"]} for d in env.dressing],
        }
    return {
        "width": WIDTH, "height": HEIGHT, "fps": FPS,
        "duration": window.duration_ms / 1000.0,
        "renderDuration": render_ms / 1000.0,
        "set": {**resolved_set.model_dump(mode="json"), "environment": resolved_set.name},
        "env": env_doc,
        "look": load_look(inputs.look),
        "ctx": ctx,
        "actors": actors,
        "world": plan.world,
        "actions": plan.actions,
        "camera": {k: plan.camera.get(k) for k in ("move", "params", "start", "target", "easeIn", "easeOut", "track")},
        "captions": {"words": words, "hot": list(hot or []), "label": shot.label, "title": inputs.title,
                     "card": card},
        "reel": {"startMs": window.start_ms, "totalMs": total_ms or window.end_ms, "index": plan.index},
        "runtime": RUNTIME_VERSION,
    }


def project_links(models: dict[str, Any] | None, set_key: str, env: Any) -> dict[str, Path]:
    """Files a shot project needs from the asset cache: published path → cached file."""
    links: dict[str, Path] = {}
    for resolved in (models or {}).values():
        if resolved.kind == "model":
            provider, _, ident = resolved.model.model.partition(":")
            for rel in resolved.files:
                links[f"assets/models/{provider}/{ident}/{rel}"] = Path(resolved.root) / rel
    if env is not None:
        for rel, path in env.files.items():
            links[f"assets/sets/{set_key}/{rel}"] = Path(path)
        for d in env.dressing:
            provider, _, ident = d["source"].partition(":")
            for rel in d["files"]:
                links[f"assets/models/{provider}/{ident}/{rel}"] = Path(d["root"]) / rel
    return links


def _link(src: Path, dest: Path) -> None:
    """Hard-link from the cache (no copy: a sky image is 25 MB and every shot needs it)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


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
@font-face{font-family:"ReelBlack";src:local("Arial Black"),local("Arial-Black");font-weight:900}
#overlay.cine{font-family:"ReelBlack",sans-serif;font-weight:900}
.cine #title{left:50%;right:auto;top:150px;transform:translateX(-50%);background:#fff;color:#111;padding:18px 34px;border-radius:22px;font-size:54px;font-weight:900;white-space:nowrap;box-shadow:0 8px 30px rgba(0,0,0,.35)}
.cine #title b{color:#e8261b}
.cine #label{top:270px;left:50%;transform:translateX(-50%);background:#ffe14a;color:#111;font-size:44px;padding:8px 26px}
.cine #captions{top:1180px;bottom:auto;left:40px;right:40px;line-height:1.05}
.cine #captions span{text-transform:uppercase;margin:0 14px;font-size:104px;color:#fff;-webkit-text-stroke:14px #000;paint-order:stroke fill;text-shadow:0 10px 0 rgba(0,0,0,.55);transform-origin:50% 70%}
.cine #captions span.hot{color:#ffe14a;font-size:128px}
#card{position:absolute;left:0;right:0;top:330px;text-align:center;opacity:0;font-size:150px;color:#ffe14a;-webkit-text-stroke:16px #000;paint-order:stroke fill;text-shadow:0 12px 0 rgba(0,0,0,.5)}
#flash{position:absolute;inset:0;background:#fff;opacity:0}
#bar{position:absolute;left:0;bottom:0;height:12px;width:0}
"""


def write_project(doc: dict[str, Any], dest: Path, *, links: dict[str, Path] | None = None) -> Path:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "runtime").mkdir(parents=True)
    for path in RUNTIME_DIR.glob("*.mjs"):  # every runtime module, both renderers
        shutil.copy2(path, dest / "runtime" / path.name)
    shutil.copytree(RUNTIME_DIR / "vendor", dest / "vendor")
    for rel, src in (links or {}).items():
        _link(Path(src), dest / rel)
    data = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={WIDTH}, height={HEIGHT}" />
<style>{_CSS}</style>
<script>window.__hf = window.__hf || {{}}; window.__e3dReady = new Promise((r) => {{ window.__e3dResolve = r; }}); window.__hf.buildReady = {{ explainer3d: window.__e3dReady }};</script>
<script type="importmap">{{"imports": {{"three": "./vendor/three.module.js", "three/addons/": "./vendor/jsm/"}}}}</script>
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
