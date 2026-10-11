"""Components the agent writes as code — opt-in, checked three ways before a frame renders.

1. A static lint refuses anything that reaches outside the scene or the clock: network,
   storage, timers, randomness, the wall clock, eval, imports and the usual escape hatches.
2. A dry run in an isolated Node `vm` context (three.js loaded inside it, every call
   time-limited) builds the component and draws its frames forwards and backwards: errors
   come back with their message, and a frame that depends on draw order is refused.
3. At render it runs in Chrome's sandbox under a Content-Security-Policy.

`vm` is not a hard security boundary, so the feature is off unless the deployment sets
REEL_EXPLAINER3D_CUSTOM_CODE — for agents you trust.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.errors import InvalidInput
from .models import ComponentAsset, Explainer3DInputs

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "runtime" / "vendor"
CHECKER = HERE / "runtime" / "tools" / "component_check.mjs"

FORBIDDEN = [
    "fetch", "XMLHttpRequest", "WebSocket", "EventSource", "navigator", "sendBeacon", "Worker", "SharedWorker",
    "importScripts", "require", "eval", "Function", "Date", "performance", "setTimeout", "setInterval",
    "setImmediate", "requestAnimationFrame", "queueMicrotask", "localStorage", "sessionStorage", "indexedDB",
    "document", "window", "self", "globalThis", "global", "process", "location", "postMessage", "crypto",
    "constructor", "__proto__", "prototype", "Reflect", "Proxy", "WebAssembly", "Atomics", "SharedArrayBuffer",
]
_WORD = re.compile(r"(?<![\w$.])(?:" + "|".join(FORBIDDEN) + r")(?![\w$])|(?<=\.)(?:constructor|__proto__|prototype)(?![\w$])")
_RANDOM = re.compile(r"\bMath\s*\.\s*random\b")
_IMPORT = re.compile(r"(?<![\w$.])import\b")
_EXPORT = re.compile(r"\bexport\s+function\s+create\s*\(")


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), code, flags=re.S)
    return re.sub(r"//[^\n]*", "", code)


def lint(code: str) -> list[tuple[str, int]]:
    """(what, line) for everything a component may not use; [] when clean."""
    found: list[tuple[str, int]] = []
    clean = _strip_comments(code)
    for number, line in enumerate(clean.split("\n"), start=1):
        for match in _WORD.finditer(line):
            found.append((match.group(0), number))
        if _RANDOM.search(line):
            found.append(("Math.random", number))
        if _IMPORT.search(line):
            found.append(("import", number))
    if not _EXPORT.search(clean):
        found.append(("export function create", 0))
    return found


@dataclass(frozen=True)
class DryRun:
    ok: bool
    error: str = ""


def dry_run(code: str, params: dict[str, Any], *, seed: int, seconds: float, size: Any = None) -> DryRun:
    node = shutil.which("node")
    if node is None:
        return DryRun(False, "node is not installed, so components cannot be checked")
    times = sorted({round(seconds * k / 6, 3) for k in range(7)})
    payload = json.dumps({
        "threeCore": (VENDOR / "three.core.js").read_text(encoding="utf-8"),
        "threeModule": (VENDOR / "three.module.js").read_text(encoding="utf-8"),
        "code": code, "params": params, "seed": seed, "times": times, "size": list(size) if size else None,
    })
    with tempfile.TemporaryDirectory() as cwd:
        try:
            proc = subprocess.run([node, "--experimental-vm-modules", "--no-warnings", "--max-old-space-size=256", str(CHECKER)],
                                  input=payload, capture_output=True, text=True, timeout=30, cwd=cwd, env={"PATH": "/usr/bin:/bin"})
        except subprocess.TimeoutExpired:
            return DryRun(False, "took too long (the dry run was stopped) — a loop that never ends?")
    try:
        result = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return DryRun(False, (proc.stderr or "the dry run failed").strip()[-400:])
    return DryRun(bool(result.get("ok")), str(result.get("error", "")))


def uses(inputs: Explainer3DInputs) -> list[tuple[str, dict[str, Any], Any]]:
    """(component, params, size) for every place a component is drawn."""
    out = [(m.asset.component, m.asset.params, m.asset.size) for m in inputs.cast.values() if isinstance(m.asset, ComponentAsset)]
    out += [(e.component, e.params, None) for shot in inputs.shots for e in shot.effects]
    return out


def check(inputs: Explainer3DInputs, settings: Any) -> None:
    """Refuse components the deployment does not allow, or that break the rules."""
    if not inputs.components:
        return
    if not getattr(settings, "explainer3d_custom_code", False):
        raise InvalidInput("this deployment does not run agent-written components",
                           hint="set REEL_EXPLAINER3D_CUSTOM_CODE=1 to allow them, or build the object from parts",
                           details={"path": "components"})
    for name, spec in inputs.components.items():
        problems = lint(spec.code)
        if problems:
            what = ", ".join(f"{w} (line {n})" if n else f"no `{w}`" for w, n in problems[:5])
            raise InvalidInput(f"component {name!r} uses what components may not: {what}",
                               hint="a component is a pure function of t: build in create(), move in update(t); "
                                    "use the rng it is given, never Math.random or the clock",
                               details={"path": f"components.{name}.code"})
    seen = set()
    for name, params, size in uses(inputs):
        key = (name, json.dumps(params, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        result = dry_run(inputs.components[name].code, params, seed=1, seconds=4.0, size=size)
        if not result.ok:
            raise InvalidInput(f"component {name!r} failed its dry run: {result.error}",
                               hint="fix the code; the dry run builds it and draws seven frames twice",
                               details={"path": f"components.{name}.code"})
