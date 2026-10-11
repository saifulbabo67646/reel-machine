# explainer3d — Plan 1: Foundation (end to end) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `explainer3d` recipe that turns a validated scene script (shapes only) into a narrated 1080×1920 reel rendered shot-by-shot by HyperFrames + Three.js, with a `storyboard` mode, persistent scene state, camera continuation, in-shot captions, synthetic SFX, a music bed and a verifier.

**Architecture:** Python validates and resolves the script, replays scene state and plans camera chains, compiles each shot into a self-contained HyperFrames project (inline `shot.json`, our ES-module runtime, vendored Three.js), and renders the projects in parallel with a per-shot content-hash cache. ffmpeg joins the shots (crossfade or hard cut), mixes narration + SFX + ducked music and muxes the reel. Every motion is a pure function of time; Python mirrors the end state of every move and the camera pose so state can flow between shots, and a cross-language test keeps the two in lockstep.

**Tech Stack:** Python 3.11+, pydantic 2, ffmpeg 8, Node ≥ 22, `hyperframes@0.8.146` (npx), Three.js 0.181.2 (vendored), `node --test`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-explainer3d-design.md`

## Scope of this plan (1 of 4)

The spec is delivered in four plans; each ends in working software.

| plan | delivers |
|---|---|
| **1 (this)** | recipe, script schema, timing, moves (core set), state + camera chains, runtime, shapes, looks, captions, renderer, storyboard/build, audio, verifier, probe |
| 2 | `reelmachine.models3d`: pack, Poly Pizza, Meshy, Tripo, normalisation, licences/credits, `find_models`, string `asset` values |
| 3 | full move vocabulary, blueprints + retention lint, `list_moves`, topic-only writer, custom code, playbook, ADR-0014 |
| 4 | `conversation` blueprint: speakers, ruby captions, romaji, recap, `vocab_card`, environments, JP font |

In plan 1 a cast `asset` must be a shape object; a string asset is refused with a hint that names plan 2's providers. `topic` without `shots` is not accepted yet (plan 3).

## Global Constraints

- Output 1080×1920, 30 fps, SAR 1:1, H.264 yuv420p, AAC 48 kHz stereo.
- HyperFrames pinned: `HYPERFRAMES_VERSION = "0.8.146"`, invoked as `npx --yes hyperframes@0.8.146 …`, with `DO_NOT_TRACK=1`, `HYPERFRAMES_FFMPEG_PATH`, `HYPERFRAMES_FFPROBE_PATH` set from `Settings.ffmpeg` / `Settings.ffprobe`.
- Three.js 0.181.2 vendored under `runtime/vendor/` (MIT notice in THIRD_PARTY_NOTICES.md); no network at render time.
- Composition root: `data-composition-id="shot"`, `data-width="1080"`, `data-height="1920"`, `data-duration="<render seconds>"`, `data-no-timeline`. The runtime sets `window.THREE`.
- Determinism: runtime code never uses `Date`, `performance.now`, unseeded `Math.random`, `setTimeout`, `setInterval`, `requestAnimationFrame` or `fetch`; all motion is computed from HyperFrames time on `hf-seek`.
- Timing: `LEAD_MS=250`, `TAIL_MS=350`, `MIN_SHOT_MS=1500`, crossfade `XFADE_MS=300`.
- Limits: 1–40 shots, narration 1–240 chars, ≤ 30 cast members, cast ids `^[a-z][a-z0-9_-]{0,31}$`.
- Error payloads stay `{code, message, hint, stage, details}`; new codes `RENDERER_UNAVAILABLE`, `SHOT_RENDER_FAILED` are additive.
- The default install stays Node- and browser-free: nothing imports the renderer at engine import time; `missing()` reports problems instead of raising.
- Every failure a user can cause names a path in `details.path` (e.g. `shots[3].actions[1].into`).

## Review Focus

1. **A shot that does not move** (static camera, no actions) — the verifier would refuse it after a long render; validation must refuse it up front (`INVALID_INPUT`, path `shots[i]`). Test in Task 4.
2. **Two actions on one actor overlapping in time** — the fold would be ambiguous; refuse with the shot and both indices. Test in Task 4.
3. **`camera.continue` on the first shot** — refuse with path `shots[0].camera.continue`. Test in Task 4.
4. **A container filled past full** (many `drop_into` / `pour`) — level is clamped at 1.0 in Python and JS; the cross-check case covers it. Test in Task 6.
5. **Cancel during a long render** — the HyperFrames process group must die; the fake-`npx` renderer test asserts the process is registered with the cancel token and killed on timeout. Test in Task 8.

---

## File Structure

```
reelmachine/core/errors.py                         + RENDERER_UNAVAILABLE, SHOT_RENDER_FAILED
reelmachine/engine/executor.py                     skip empty render.ass
reelmachine/render/hyperframes.py                  HyperframesRenderer (provider "hyperframes")
reelmachine/recipes/explainer3d/
  __init__.py
  models.py        inputs + stage payloads
  geometry.py      units, stage tops, anchors, shape dims, fill height
  timing.py        shot windows, auto layout, speech estimate
  world.py         world/ctx helpers shared by moves and state (point, top, interior, mix_hex)
  moves.py         object/process move catalogue: params + end states + SFX
  camera.py        camera move catalogue: params + pose(p) + default start
  validate.py      script → ResolvedShot list (all INVALID_INPUT paths live here)
  state.py         initial world, replay, camera chains → ShotPlan list
  compile.py       ShotPlan → HyperFrames project dir; RUNTIME_VERSION; project key
  audio.py         SFX synthesis, speech padding, final audio mix
  video.py         shot join (xfade / cut), mux
  fake.py          FakeShotRenderer (provider "explainer3d-fake") for tests
  stages.py        script → assets → narration → shots → prep → compose → render
  verify.py        verifier
  recipe.py        RecipeSpec + probe + verify
  looks/diorama-day.json, looks/studio-clean.json
  runtime/
    math.mjs poses.mjs camera.mjs particles.mjs captions.mjs   (pure, no THREE import)
    shapes.mjs look.mjs                                        (take THREE as an argument)
    main.mjs                                                   (the only `import "three"`)
    vendor/three.module.js vendor/three.core.js vendor/LICENSE
    tools/evaluate.mjs                                         (cross-check entry)
    tests/pure.test.mjs tests/shapes.test.mjs
tests/test_explainer3d.py            fast unit tests
tests/test_explainer3d_engine.py     engine runs with the fake renderer (slow: ffmpeg)
tests/test_explainer3d_render.py     real HyperFrames renders (slow, skip without renderer)
tests/test_hyperframes_renderer.py   renderer with a fake npx
```

---

### Task 1: Error codes, artifact fix, package skeleton and input models

**Files:**
- Modify: `reelmachine/core/errors.py`
- Modify: `reelmachine/engine/executor.py` (`_collect_artifacts`)
- Create: `reelmachine/recipes/explainer3d/__init__.py`, `reelmachine/recipes/explainer3d/models.py`
- Test: `tests/test_explainer3d.py`

**Interfaces:**
- Produces: `RendererUnavailable(ProviderUnavailable)`, `ShotRenderFailed(RenderFailed)`; `ShapeSpec`, `CastMember`, `Action`, `Camera`, `Shot`, `SetSpec`, `Explainer3DInputs` (with `.shot_list()`), `Action.params() -> dict`, `Camera.params() -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_explainer3d.py
"""explainer3d: fast tests (no Node, no ffmpeg)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reelmachine.core.errors import ErrorCode, RendererUnavailable, ShotRenderFailed
from reelmachine.recipes.explainer3d.models import Explainer3DInputs


def _script(**over):
    base = {
        "title": "Bait",
        "cast": {"bowl": {"asset": {"shape": "bowl", "material": "stone"}, "at": "stage.center"}},
        "shots": [{"narration": "A bowl.", "camera": {"move": "orbit", "target": "bowl"}}],
    }
    base.update(over)
    return base


def test_new_error_codes_are_additive():
    assert RendererUnavailable("x").to_job_error().code == ErrorCode.RENDERER_UNAVAILABLE
    assert ShotRenderFailed("x").to_job_error().code == ErrorCode.SHOT_RENDER_FAILED


def test_inputs_parse_and_ids_fill_in():
    inputs = Explainer3DInputs.model_validate(_script())
    shots = inputs.shot_list()
    assert shots[0].id == "shot-1"
    assert inputs.cast["bowl"].asset.shape == "bowl"
    assert inputs.shots[0].camera.params() == {}


def test_camera_continue_alias_and_extra_params():
    inputs = Explainer3DInputs.model_validate(
        _script(shots=[{"narration": "x", "camera": {"move": "orbit", "continue": True, "to": 90}}])
    )
    cam = inputs.shots[0].camera
    assert cam.continue_ is True
    assert cam.params() == {"to": 90}


@pytest.mark.parametrize(
    "bad",
    [
        {"cast": {"Bowl": {"asset": {"shape": "bowl"}}}},
        {"shots": []},
        {"shots": [{"narration": ""}]},
        {"shots": [{"narration": "x", "actions": [{"move": "appear", "actor": "bowl", "at": [0.5, 0.5]}]}]},
        {"cast": {"bowl": {"asset": {"shape": "bowl", "colour": "red"}}}},
    ],
)
def test_inputs_reject_bad_shapes(bad):
    with pytest.raises(ValidationError):
        Explainer3DInputs.model_validate(_script(**bad))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_explainer3d.py -q`
Expected: FAIL — `ImportError: cannot import name 'RendererUnavailable'`.

- [ ] **Step 3: Add the error codes**

In `reelmachine/core/errors.py`, add to `ErrorCode` (after `RENDER_FAILED`):

```python
    RENDERER_UNAVAILABLE = "RENDERER_UNAVAILABLE"
    SHOT_RENDER_FAILED = "SHOT_RENDER_FAILED"
```

and after `class RenderFailed`:

```python
class RendererUnavailable(ProviderUnavailable):
    code = ErrorCode.RENDERER_UNAVAILABLE


class ShotRenderFailed(RenderFailed):
    code = ErrorCode.SHOT_RENDER_FAILED
```

(`RenderFailed` is defined after `ProviderUnavailable`; place both new classes after `RenderFailed`.)

- [ ] **Step 4: Skip an empty `ass` artifact**

In `reelmachine/engine/executor.py::_collect_artifacts`, replace the `captions.ass` line with:

```python
        if getattr(render, "ass", ""):
            artifacts.append(_artifact("captions.ass", Path(render.ass), AssetKind.SUBTITLE))
```

- [ ] **Step 5: Write the models**

`reelmachine/recipes/explainer3d/__init__.py`:

```python
"""explainer3d: narrated 3D process and fact reels, rendered from code by HyperFrames."""
```

`reelmachine/recipes/explainer3d/models.py`:

```python
"""Inputs and stage payloads for the explainer3d recipe.

The script is what an agent writes. Move parameters travel as extra fields on an action
or a camera and are validated against the move catalogue in `validate.py`, so the schema
stays small and every refusal can name the exact field.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Mode = Literal["storyboard", "build"]
ShapeKind = Literal["bowl", "cup", "pot", "jar", "bottle", "ball", "box", "sack", "pile", "plinth"]
Material = Literal[
    "stone", "wood", "ceramic", "metal", "glass", "dough", "salt", "grain", "water", "cloth"
]
Environment = Literal["meadow-day", "studio"]
StageKind = Literal["stone-slab", "table", "none"]

CAST_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def hex_colour(value: str | None) -> str | None:
    if value is None:
        return None
    if not HEX.match(value):
        raise ValueError("colours are #rrggbb")
    return value.lower()


class ShapeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: ShapeKind
    height: float = Field(0.3, gt=0, le=5)
    width: float | None = Field(None, gt=0, le=5)
    material: Material = "ceramic"
    colour: str | None = None

    @field_validator("colour")
    @classmethod
    def check_colour(cls, value: str | None) -> str | None:
        return hex_colour(value)


class CastMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: ShapeSpec | str
    at: str | None = None  # an anchor; None keeps the actor hidden until a move brings it


class Action(BaseModel):
    model_config = ConfigDict(extra="allow")

    move: str
    actor: str | None = None
    at: tuple[float, float] | None = None

    @model_validator(mode="after")
    def _window(self) -> "Action":
        if self.at is not None:
            start, end = self.at
            if not (0.0 <= start < end <= 1.0):
                raise ValueError("at is [start, end] with 0 <= start < end <= 1")
        return self

    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Camera(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    move: str = "static"
    target: str | None = None
    continue_: bool = Field(False, alias="continue")

    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    narration: str = Field(min_length=1, max_length=240)
    label: str | None = Field(None, max_length=40)
    camera: Camera | None = None
    actions: list[Action] | Literal["auto"] = Field(default_factory=list)
    auto: list[Action] = Field(default_factory=list)


class SetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: Environment = "meadow-day"
    stage: StageKind = "stone-slab"


class Explainer3DInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = "build"
    title: str = Field("", max_length=80)
    language: str = "en"
    voice: str = "fake:default"
    look: str = "diorama-day"
    music: str | None = None  # a local audio file; pack loops arrive with plan 2
    set: SetSpec = Field(default_factory=SetSpec)
    cast: dict[str, CastMember] = Field(default_factory=dict)
    shots: list[Shot] = Field(min_length=1, max_length=40)
    allow_fallback: bool = False
    audio_profile: str = "studio"
    name: str | None = None
    outdir: str | None = None

    @field_validator("cast")
    @classmethod
    def _cast_ids(cls, value: dict[str, CastMember]) -> dict[str, CastMember]:
        if len(value) > 30:
            raise ValueError("at most 30 cast members")
        bad = sorted(key for key in value if not CAST_ID.match(key))
        if bad:
            raise ValueError(f"cast ids match [a-z][a-z0-9_-]{{0,31}}: {bad}")
        return value

    def shot_list(self) -> list[Shot]:
        return [
            shot.model_copy(update={"id": shot.id or f"shot-{index}"})
            for index, shot in enumerate(self.shots, start=1)
        ]
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_explainer3d.py -q && uv run pytest tests/test_engine.py tests/test_mcp.py -q`
Expected: PASS (the second run proves the executor change and new codes break nothing).

- [ ] **Step 7: Commit**

```bash
git add reelmachine/core/errors.py reelmachine/engine/executor.py reelmachine/recipes/explainer3d tests/test_explainer3d.py
git commit -m "explainer3d: input models, two additive error codes, and no phantom captions.ass"
```

---

### Task 2: Geometry and timing

**Files:**
- Create: `reelmachine/recipes/explainer3d/geometry.py`, `reelmachine/recipes/explainer3d/timing.py`
- Test: `tests/test_explainer3d.py` (append)

**Interfaces:**
- Produces: `Vec3 = tuple[float, float, float]`; `STAGE_TOP`, `CONTAINERS`, `FLOOR`, `dims(spec) -> Vec3`, `anchors(stage) -> dict[str, Vec3]`, `fill_y(height, level) -> float`; `LEAD_MS, TAIL_MS, MIN_SHOT_MS, XFADE_MS`, `ShotWindow(index, start_ms, duration_ms, speech_offset_ms, speech_ms)` with `.end_ms`, `shot_windows(list[int]) -> list[ShotWindow]`, `auto_layout(n) -> list[tuple[float,float]]`, `estimate_speech_ms(text, language) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_explainer3d.py
from reelmachine.recipes.explainer3d.geometry import anchors, dims, fill_y
from reelmachine.recipes.explainer3d.models import ShapeSpec
from reelmachine.recipes.explainer3d.timing import (
    LEAD_MS, MIN_SHOT_MS, TAIL_MS, auto_layout, estimate_speech_ms, shot_windows,
)


def test_dims_follow_kind_ratio_unless_width_given():
    assert dims(ShapeSpec(shape="bowl", height=0.2)) == (0.44, 0.2, 0.44)
    assert dims(ShapeSpec(shape="bowl", height=0.2, width=0.3)) == (0.3, 0.2, 0.3)


def test_anchors_sit_on_the_stage_top():
    a = anchors("stone-slab")
    assert a["stage.center"] == (0.0, 0.9, 0.0)
    assert a["sky"][1] > a["stage.center"][1]
    assert anchors("none")["stage.center"][1] == 0.0


def test_fill_rises_inside_the_rim():
    assert fill_y(1.0, 0.0) == pytest.approx(0.15)
    assert fill_y(1.0, 1.0) < 1.0


def test_shot_windows_pad_and_floor():
    windows = shot_windows([2000, 100])
    assert windows[0].duration_ms == LEAD_MS + 2000 + TAIL_MS
    assert windows[1].start_ms == windows[0].end_ms
    assert windows[1].duration_ms == MIN_SHOT_MS


def test_auto_layout_is_sequential_and_inside():
    spans = auto_layout(3)
    assert spans[0][0] == pytest.approx(0.1) and spans[-1][1] == pytest.approx(0.95)
    assert all(a[1] == pytest.approx(b[0]) for a, b in zip(spans, spans[1:]))


def test_speech_estimates():
    assert estimate_speech_ms("one two three four five six", "en") > 1500
    assert estimate_speech_ms("駅[えき]はどこですか", "ja") == int(len("駅はどこですか") / 7.0 * 1000)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_explainer3d.py -q`
Expected: FAIL — `ModuleNotFoundError: ...geometry`.

- [ ] **Step 3: Implement**

`geometry.py`:

```python
"""Units, the stage, anchors and shape sizes. Metres, Y up; an object's origin is its
bottom centre. The JS runtime receives every number from here through shot.json, so the
two sides cannot disagree about where something is."""

from __future__ import annotations

from .models import ShapeSpec

Vec3 = tuple[float, float, float]

STAGE_TOP = {"stone-slab": 0.9, "table": 0.75, "none": 0.0}
WIDTH_RATIO = {
    "bowl": 2.2, "cup": 0.8, "pot": 1.3, "jar": 0.7, "bottle": 0.32,
    "ball": 1.0, "box": 1.0, "sack": 0.75, "pile": 2.5, "plinth": 2.0,
}
CONTAINERS = frozenset({"bowl", "cup", "pot", "jar"})
FLOOR = 0.15  # a container's floor, as a fraction of its height
SKY_HEIGHT = 3.0


def dims(spec: ShapeSpec) -> Vec3:
    width = spec.width if spec.width is not None else spec.height * WIDTH_RATIO[spec.shape]
    return (round(width, 4), round(spec.height, 4), round(width, 4))


def anchors(stage: str) -> dict[str, Vec3]:
    top = STAGE_TOP[stage]
    return {
        "stage.center": (0.0, top, 0.0),
        "stage.left": (-0.45, top, 0.0),
        "stage.right": (0.45, top, 0.0),
        "ground": (0.0, 0.0, 0.9),
        "sky": (0.0, top + SKY_HEIGHT, 0.0),
    }


def fill_y(height: float, level: float) -> float:
    """Height of a container's fill surface above the container's origin."""
    return height * (FLOOR + (1.0 - FLOOR) * 0.9 * level)
```

`timing.py`:

```python
"""Narration sets the clock: a shot lasts as long as its line plus padding."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

LEAD_MS = 250
TAIL_MS = 350
MIN_SHOT_MS = 1500
XFADE_MS = 300

_WORDS_PER_S = 2.6
_CJK_CHARS_PER_S = 7.0
_READING = re.compile(r"\[[^\]]*\]")


@dataclass(frozen=True)
class ShotWindow:
    index: int
    start_ms: int
    duration_ms: int
    speech_offset_ms: int
    speech_ms: int

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


def shot_windows(speech_ms: Sequence[int]) -> list[ShotWindow]:
    windows: list[ShotWindow] = []
    cursor = 0
    for index, ms in enumerate(speech_ms):
        duration = max(MIN_SHOT_MS, LEAD_MS + int(ms) + TAIL_MS)
        windows.append(ShotWindow(index, cursor, duration, LEAD_MS, int(ms)))
        cursor += duration
    return windows


def auto_layout(count: int, lo: float = 0.1, hi: float = 0.95) -> list[tuple[float, float]]:
    if count <= 0:
        return []
    step = (hi - lo) / count
    return [(round(lo + i * step, 6), round(lo + (i + 1) * step, 6)) for i in range(count)]


def estimate_speech_ms(text: str, language: str) -> int:
    if language.split("-")[0].lower() in {"ja", "zh", "ko"}:
        chars = len(re.sub(r"\s", "", _READING.sub("", text)))
        return int(chars / _CJK_CHARS_PER_S * 1000)
    return int(len(text.split()) / _WORDS_PER_S * 1000)
```

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_explainer3d.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add reelmachine/recipes/explainer3d/geometry.py reelmachine/recipes/explainer3d/timing.py tests/test_explainer3d.py
git commit -m "explainer3d: stage geometry, anchors, shape sizes and the narration clock"
```

---

### Task 3: World helpers, move catalogue and camera catalogue (Python side)

**Files:**
- Create: `reelmachine/recipes/explainer3d/world.py`, `moves.py`, `camera.py`
- Test: `tests/test_explainer3d.py` (append)

**Interfaces:**
- Consumes: `geometry.fill_y`, `geometry.Vec3`, `models.hex_colour`.
- Produces:
  - `world.py`: `World = dict[str, dict]` (actor → `{"position": [x,y,z], "visible": bool, "rotationY": float, "scale": float, "fill": {"level","colour","surface"} | None}`), `Ctx = dict` (`{"anchors", "dims", "homes", "colours"}`), `point(ctx, world, ref)`, `top(ctx, world, ref)`, `center(ctx, world, ref)`, `interior(ctx, world, ref, level)`, `mix_hex(a, b, t)`, `add(a, b)`, `scale3(a, k)`, `lerp3(a, b, t)`.
  - `moves.py`: `Move` dataclass (`name, category, description, params, example, sfx, actor, refs, container_refs, actor_container, end`), `MOVES: dict[str, Move]`, `OFFSETS`.
  - `camera.py`: `CameraMove` dataclass (`name, description, params, example`), `CAMERA_MOVES`, `pose(move, params, start, target, p) -> dict`, `default_start(move, params, target) -> dict`, `ease(p, ease_in, ease_out) -> float`. A pose is `{"position": [..], "target": [..], "fov": float}`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_explainer3d.py
import math

from reelmachine.recipes.explainer3d.camera import CAMERA_MOVES, default_start, ease, pose
from reelmachine.recipes.explainer3d.moves import MOVES
from reelmachine.recipes.explainer3d.world import mix_hex

CTX = {
    "anchors": {"stage.center": [0.0, 0.9, 0.0], "sky": [0.0, 3.9, 0.0]},
    "dims": {"bowl": [0.44, 0.2, 0.44], "potato": [0.12, 0.1, 0.12]},
    "homes": {"bowl": [0.0, 0.9, 0.0], "potato": [0.3, 0.9, 0.0]},
    "colours": {"bowl": "#8f877c", "potato": "#d9b44a"},
}


def _world():
    return {
        "bowl": {"position": [0.0, 0.9, 0.0], "visible": True, "rotationY": 0.0, "scale": 1.0,
                 "fill": {"level": 0.0, "colour": "#d9d27a", "surface": "liquid"}},
        "potato": {"position": [0.3, 0.9, 0.0], "visible": False, "rotationY": 0.0, "scale": 1.0, "fill": None},
    }


def test_mix_hex_endpoints():
    assert mix_hex("#000000", "#FFFFFF", 0.0) == "#000000"
    assert mix_hex("#000000", "#ffffff", 1.0) == "#ffffff"
    assert mix_hex("#000000", "#ffffff", 0.5) == "#808080"


def test_fly_in_lands_home_and_is_visible():
    move = MOVES["fly_in"]
    params = move.params.model_validate({}).model_dump(by_alias=True)
    world = move.end(_world(), {"actor": "potato", "params": params}, CTX)
    assert world["potato"]["position"] == [0.3, 0.9, 0.0]
    assert world["potato"]["visible"] is True


def test_drop_into_hides_actor_and_fills_container():
    move = MOVES["drop_into"]
    params = move.params.model_validate({"into": "bowl"}).model_dump(by_alias=True)
    world = move.end(_world(), {"actor": "potato", "params": params}, CTX)
    assert world["potato"]["visible"] is False and world["potato"]["scale"] == 0.0
    assert world["bowl"]["fill"]["level"] == pytest.approx(0.15)
    assert world["bowl"]["fill"]["colour"] == "#d9b44a"  # an empty bowl takes the colour


def test_fill_clamps_at_full():
    move = MOVES["pour"]
    params = move.params.model_validate({"into": "bowl", "fill_add": 0.8}).model_dump(by_alias=True)
    world = _world()
    for _ in range(3):
        world = move.end(world, {"actor": "potato", "params": params}, CTX)
    assert world["bowl"]["fill"]["level"] == 1.0


def test_every_move_has_an_example_that_validates():
    for move in MOVES.values():
        move.params.model_validate(move.example.get("params", {}))
    for move in CAMERA_MOVES.values():
        move.params.model_validate(move.example)


def test_orbit_pose_keeps_radius_and_reaches_angle():
    params = CAMERA_MOVES["orbit"].params.model_validate({"from": 0, "to": 90}).model_dump(by_alias=True)
    target = [0.0, 1.0, 0.0]
    start = default_start("orbit", params, target)
    end = pose("orbit", params, start, target, 1.0)
    assert end["position"][0] == pytest.approx(2.2) and end["position"][2] == pytest.approx(0.0, abs=1e-9)


def test_push_in_and_ease():
    params = CAMERA_MOVES["push_in"].params.model_validate({"factor": 0.5}).model_dump(by_alias=True)
    target = [0.0, 1.0, 0.0]
    start = default_start("push_in", params, target)
    end = pose("push_in", params, start, target, 1.0)
    assert math.dist(end["position"], target) == pytest.approx(math.dist(start["position"], target) * 0.5)
    assert ease(0.0, True, True) == 0.0 and ease(1.0, True, False) == pytest.approx(1.0)
    assert ease(0.5, False, False) == 0.5
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_explainer3d.py -q` → FAIL (`ModuleNotFoundError: ...camera`).

- [ ] **Step 3: Implement `world.py`**

```python
"""The world shared by Python and the JS runtime: plain JSON-shaped dicts.

Python only ever needs a move's end state (p = 1) — to carry the scene from one shot to
the next — while the runtime evaluates every frame. Both use these exact formulas; the
cross-check test in Task 6 runs the runtime and compares.
"""

from __future__ import annotations

from typing import Any

from .geometry import fill_y

World = dict[str, dict[str, Any]]
Ctx = dict[str, Any]


def add(a, b) -> list[float]:
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def scale3(a, k: float) -> list[float]:
    return [a[0] * k, a[1] * k, a[2] * k]


def lerp3(a, b, t: float) -> list[float]:
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]


def point(ctx: Ctx, world: World, ref: str) -> list[float]:
    if ref in ctx["anchors"]:
        return list(ctx["anchors"][ref])
    return list(world[ref]["position"])


def top(ctx: Ctx, world: World, ref: str) -> list[float]:
    if ref in ctx["anchors"]:
        return list(ctx["anchors"][ref])
    actor = world[ref]
    height = ctx["dims"][ref][1] * actor["scale"]
    return [actor["position"][0], actor["position"][1] + height, actor["position"][2]]


def center(ctx: Ctx, world: World, ref: str) -> list[float]:
    if ref in ctx["anchors"]:
        return list(ctx["anchors"][ref])
    actor = world[ref]
    half = ctx["dims"][ref][1] * actor["scale"] / 2.0
    return [actor["position"][0], actor["position"][1] + half, actor["position"][2]]


def interior(ctx: Ctx, world: World, ref: str, level: float) -> list[float]:
    actor = world[ref]
    return [
        actor["position"][0],
        actor["position"][1] + fill_y(ctx["dims"][ref][1], level),
        actor["position"][2],
    ]


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def mix_hex(a: str, b: str, t: float) -> str:
    """Linear sRGB-space mix, rounded half up — the runtime uses Math.round, same result."""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    channels = [int((x + (y - x) * t) + 0.5) for x, y in ((ra, rb), (ga, gb), (ba, bb))]
    return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in channels)


def set_actor(world: World, actor: str, **patch: Any) -> World:
    new = dict(world)
    new[actor] = {**world[actor], **patch}
    return new


def set_fill(world: World, actor: str, **patch: Any) -> World:
    return set_actor(world, actor, fill={**world[actor]["fill"], **patch})


def pour_fill(world: World, container: str, add_level: float, colour: str) -> World:
    fill = world[container]["fill"]
    level0 = fill["level"]
    level = min(1.0, level0 + add_level)
    if level0 <= 0:
        mixed = colour
    else:
        mixed = mix_hex(fill["colour"], colour, add_level / (level0 + add_level))
    return set_fill(world, container, level=level, colour=mixed)
```

- [ ] **Step 4: Implement `moves.py`**

```python
"""Object and process moves: parameters, SFX cue, and the end state each one leaves.

The runtime (`runtime/poses.mjs`) animates the in-between; this file defines where every
move ends, so the next shot can start from it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import hex_colour
from .world import Ctx, World, add, interior, point, pour_fill, set_actor, set_fill, top

OFFSETS = {
    "sky": (0.0, 3.0, 0.0),
    "left": (-2.5, 0.5, 0.0),
    "right": (2.5, 0.5, 0.0),
    "front": (0.0, 0.5, 2.5),
}


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NoParams(_Params):
    pass


class FlyInParams(_Params):
    from_: Literal["sky", "left", "right", "front"] = Field("sky", alias="from")
    to: str | None = None


class DropIntoParams(_Params):
    into: str
    fill_add: float = Field(0.15, ge=0, le=1)
    colour: str | None = None
    @field_validator("colour")
    @classmethod
    def check_colour(cls, value):
        return hex_colour(value)


class PlaceOnParams(_Params):
    on: str


class SpinParams(_Params):
    turns: float = Field(1.0, gt=0, le=4)


class ExitParams(_Params):
    to: Literal["sky", "left", "right"] = "right"


class PourParams(_Params):
    into: str
    fill_add: float = Field(0.2, ge=0, le=1)
    colour: str = "#f2efe6"
    @field_validator("colour")
    @classmethod
    def check_colour(cls, value):
        return hex_colour(value)


class SprinkleParams(_Params):
    into: str
    fill_add: float = Field(0.03, ge=0, le=1)
    colour: str = "#ffffff"
    @field_validator("colour")
    @classmethod
    def check_colour(cls, value):
        return hex_colour(value)


class MashParams(_Params):
    colour: str | None = None
    @field_validator("colour")
    @classmethod
    def check_colour(cls, value):
        return hex_colour(value)


class SteamParams(_Params):
    intensity: float = Field(1.0, gt=0, le=3)


EndFn = Callable[[World, dict[str, Any], Ctx], World]


@dataclass(frozen=True)
class Move:
    name: str
    category: str
    description: str
    params: type[_Params]
    example: dict[str, Any]
    sfx: tuple[str, float] | None
    actor: Literal["required", "forbidden"]
    end: EndFn
    refs: tuple[str, ...] = ()            # params naming a cast member or an anchor
    container_refs: tuple[str, ...] = ()  # params that must name a container
    actor_container: bool = False         # the actor itself must be a container


def _appear(world, action, ctx):
    return set_actor(world, action["actor"], visible=True, scale=1.0)


def _fly_in(world, action, ctx):
    actor, to = action["actor"], action["params"].get("to")
    if to is None:
        dest = list(ctx["homes"][actor])
    elif to in ctx["anchors"]:
        dest = point(ctx, world, to)
    else:
        dest = top(ctx, world, to)
    return set_actor(world, actor, position=dest, visible=True)


def _drop_into(world, action, ctx):
    actor, params = action["actor"], action["params"]
    into = params["into"]
    inside = interior(ctx, world, into, world[into]["fill"]["level"])
    world = set_actor(world, actor, position=inside, visible=False, scale=0.0)
    return pour_fill(world, into, params["fill_add"], params.get("colour") or ctx["colours"][actor])


def _place_on(world, action, ctx):
    return set_actor(world, action["actor"], position=top(ctx, world, action["params"]["on"]), visible=True)


def _spin(world, action, ctx):
    actor = action["actor"]
    turned = world[actor]["rotationY"] + action["params"]["turns"] * 2 * math.pi
    return set_actor(world, actor, rotationY=turned)


def _exit(world, action, ctx):
    actor = action["actor"]
    gone = add(world[actor]["position"], OFFSETS[action["params"]["to"]])
    return set_actor(world, actor, position=gone, visible=False)


def _pour(world, action, ctx):
    params = action["params"]
    return pour_fill(world, params["into"], params["fill_add"], params["colour"])


def _sprinkle(world, action, ctx):
    params = action["params"]
    fill = world[params["into"]]["fill"]
    return set_fill(world, params["into"], level=min(1.0, fill["level"] + params["fill_add"]))


def _mash(world, action, ctx):
    actor = action["actor"]
    colour = action["params"].get("colour") or world[actor]["fill"]["colour"]
    return set_fill(world, actor, surface="paste", colour=colour)


def _steam(world, action, ctx):
    return world


MOVES: dict[str, Move] = {
    move.name: move
    for move in (
        Move("appear", "object", "Fade/scale an actor into view where it stands.", NoParams,
             {"move": "appear", "actor": "bowl"}, ("pop", 0.05), "required", _appear),
        Move("scale_pop", "object", "Pop an actor into view with a small overshoot.", NoParams,
             {"move": "scale_pop", "actor": "bowl"}, ("pop", 0.0), "required", _appear),
        Move("fly_in", "object", "Fly an actor in from the sky or a side and land it (on `to`, or home).",
             FlyInParams, {"move": "fly_in", "actor": "potato", "params": {"from": "sky"}},
             ("whoosh", 0.0), "required", _fly_in, refs=("to",)),
        Move("drop_into", "object", "Lift an actor over a container and drop it in; the fill rises.",
             DropIntoParams, {"move": "drop_into", "actor": "potato", "params": {"into": "bowl"}},
             ("thud", 0.8), "required", _drop_into, refs=("into",), container_refs=("into",)),
        Move("place_on", "object", "Arc an actor onto the top of another actor or an anchor.",
             PlaceOnParams, {"move": "place_on", "actor": "bowl", "params": {"on": "stage.center"}},
             ("thud", 0.9), "required", _place_on, refs=("on",)),
        Move("spin_showcase", "object", "Turn an actor in place to show it off.", SpinParams,
             {"move": "spin_showcase", "actor": "bowl", "params": {"turns": 1}},
             None, "required", _spin),
        Move("exit", "object", "Send an actor out of frame.", ExitParams,
             {"move": "exit", "actor": "bowl", "params": {"to": "right"}},
             ("whoosh", 0.0), "required", _exit),
        Move("pour", "process", "Tilt a vessel over a container and pour a stream into it.",
             PourParams, {"move": "pour", "actor": "bottle", "params": {"into": "bowl", "colour": "#f2efe6"}},
             ("pour", 0.3), "required", _pour, refs=("into",), container_refs=("into",)),
        Move("sprinkle", "process", "Sprinkle grains or powder into a container from above.",
             SprinkleParams, {"move": "sprinkle", "params": {"into": "bowl"}},
             ("sprinkle", 0.15), "forbidden", _sprinkle, refs=("into",), container_refs=("into",)),
        Move("mash", "process", "Pound a container's contents with a pestle into a paste.",
             MashParams, {"move": "mash", "actor": "bowl", "params": {"colour": "#d9d27a"}},
             ("thud", 0.3), "required", _mash, actor_container=True),
        Move("steam", "process", "Steam rises from an actor.", SteamParams,
             {"move": "steam", "actor": "bowl"}, ("sizzle", 0.0), "required", _steam),
    )
}
```

- [ ] **Step 5: Implement `camera.py`**

```python
"""Camera moves. A pose is {"position", "target", "fov"}; every move is a pure function
of a start pose, the target point and progress p. `default_start` is used only when a
shot does not continue the previous camera."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .world import lerp3

FOV = 40.0
DEFAULT_OFFSET = (0.0, 0.9, 2.4)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class StaticParams(_Params):
    pass


class OrbitParams(_Params):
    from_: float = Field(20.0, alias="from", ge=-720, le=720)
    to: float = Field(45.0, ge=-720, le=720)
    radius: float = Field(2.2, gt=0.2, le=20)
    height: float = Field(0.9, ge=-5, le=10)


class PushInParams(_Params):
    factor: float = Field(0.6, gt=0.1, lt=1.0)


class DollyParams(_Params):
    offset: tuple[float, float, float] = (0.6, 0.0, 0.0)


@dataclass(frozen=True)
class CameraMove:
    name: str
    description: str
    params: type[_Params]
    example: dict[str, Any]


CAMERA_MOVES: dict[str, CameraMove] = {
    move.name: move
    for move in (
        CameraMove("static", "Hold the camera still.", StaticParams, {}),
        CameraMove("orbit", "Circle the target from angle `from` to `to` (degrees).", OrbitParams,
                   {"from": 20, "to": 45}),
        CameraMove("push_in", "Move toward the target; `factor` is the final distance ratio.",
                   PushInParams, {"factor": 0.6}),
        CameraMove("dolly", "Slide the camera and its aim by `offset` metres.", DollyParams,
                   {"offset": [0.6, 0, 0]}),
    )
}


def ease(p: float, ease_in: bool, ease_out: bool) -> float:
    p = min(1.0, max(0.0, p))
    if ease_in and ease_out:
        return 4 * p * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2
    if ease_in:
        return 1 - math.cos(p * math.pi / 2)
    if ease_out:
        return math.sin(p * math.pi / 2)
    return p


def default_start(move: str, params: dict[str, Any], target) -> dict[str, Any]:
    if move == "orbit":
        angle = math.radians(params["from"])
        position = [
            target[0] + params["radius"] * math.sin(angle),
            target[1] + params["height"],
            target[2] + params["radius"] * math.cos(angle),
        ]
    else:
        position = [target[0] + DEFAULT_OFFSET[0], target[1] + DEFAULT_OFFSET[1], target[2] + DEFAULT_OFFSET[2]]
    return {"position": position, "target": list(target), "fov": FOV}


def pose(move: str, params: dict[str, Any], start: dict[str, Any], target, p: float) -> dict[str, Any]:
    sp, st = start["position"], start["target"]
    if move == "static":
        return {"position": list(sp), "target": list(st), "fov": start["fov"]}
    if move == "orbit":
        dx, dy, dz = sp[0] - target[0], sp[1] - target[1], sp[2] - target[2]
        a0 = math.atan2(dx, dz)
        radius = math.hypot(dx, dz)
        a1 = math.radians(params["to"])
        a = a0 + (a1 - a0) * p
        position = [target[0] + radius * math.sin(a), target[1] + dy, target[2] + radius * math.cos(a)]
        return {"position": position, "target": lerp3(st, target, p), "fov": start["fov"]}
    if move == "push_in":
        k = 1 + (params["factor"] - 1) * p
        position = [target[i] + (sp[i] - target[i]) * k for i in range(3)]
        return {"position": position, "target": lerp3(st, target, p), "fov": start["fov"]}
    if move == "dolly":
        o = params["offset"]
        return {
            "position": [sp[i] + o[i] * p for i in range(3)],
            "target": [st[i] + o[i] * p for i in range(3)],
            "fov": start["fov"],
        }
    raise KeyError(move)
```

- [ ] **Step 6: Run tests** — `uv run pytest tests/test_explainer3d.py -q` → PASS.

- [ ] **Step 7: Commit**

```bash
git add reelmachine/recipes/explainer3d/world.py reelmachine/recipes/explainer3d/moves.py reelmachine/recipes/explainer3d/camera.py tests/test_explainer3d.py
git commit -m "explainer3d: the move and camera catalogues, and where every move ends"
```

---

### Task 4: Validation and resolution

**Files:**
- Create: `reelmachine/recipes/explainer3d/validate.py`
- Modify: `reelmachine/recipes/explainer3d/models.py` (append resolved models)
- Test: `tests/test_explainer3d.py` (append)

**Interfaces:**
- Consumes: `MOVES`, `CAMERA_MOVES`, `anchors`, `CONTAINERS`, `auto_layout`.
- Produces (in `models.py`): `ResolvedAction(move, actor, params, at, index)`, `ResolvedCamera(move, target, params, continues)`, `ResolvedShot(id, index, narration, label, actions, camera)`. In `validate.py`: `resolve_script(inputs) -> list[ResolvedShot]` raising `InvalidInput` with `details["path"]`; `LOOKS: frozenset[str]` = `{"diorama-day", "studio-clean"}`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_explainer3d.py
from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.explainer3d.validate import resolve_script


def _resolve(**over):
    return resolve_script(Explainer3DInputs.model_validate(_script(**over)))


def _path(**over) -> str:
    with pytest.raises(InvalidInput) as info:
        _resolve(**over)
    return info.value.details["path"]


def test_resolve_fills_windows_and_aliases():
    shots = _resolve(
        cast={
            "bowl": {"asset": {"shape": "bowl"}, "at": "stage.center"},
            "potato": {"asset": {"shape": "ball", "height": 0.1}},
        },
        shots=[{
            "narration": "Drop it.",
            "actions": [
                {"move": "fly_in", "actor": "potato", "from": "left"},
                {"move": "drop_into", "actor": "potato", "into": "bowl"},
            ],
        }],
    )
    a0, a1 = shots[0].actions
    assert a0.params["from"] == "left" and a0.at == pytest.approx((0.1, 0.525))
    assert a1.at[0] == pytest.approx(a0.at[1])
    assert shots[0].camera.move == "static" and shots[0].camera.continues is False


def test_auto_actions():
    shots = _resolve(shots=[{"narration": "x", "actions": "auto",
                             "auto": [{"move": "spin_showcase", "actor": "bowl"}]}])
    assert shots[0].actions[0].at == pytest.approx((0.1, 0.95))


@pytest.mark.parametrize(
    "over, path",
    [
        ({"cast": {"bowl": {"asset": "pack:stone-bowl"}}}, "cast.bowl.asset"),
        ({"cast": {"bowl": {"asset": {"shape": "bowl"}, "at": "moon"}}}, "cast.bowl.at"),
        ({"look": "neon"}, "look"),
        ({"shots": [{"narration": "x", "actions": [{"move": "teleport", "actor": "bowl"}]}]},
         "shots[0].actions[0].move"),
        ({"shots": [{"narration": "x", "actions": [{"move": "appear", "actor": "ghost"}]}]},
         "shots[0].actions[0].actor"),
        ({"shots": [{"narration": "x", "actions": [{"move": "appear"}]}]}, "shots[0].actions[0].actor"),
        ({"shots": [{"narration": "x", "actions": [{"move": "sprinkle", "actor": "bowl", "into": "bowl"}]}]},
         "shots[0].actions[0].actor"),
        ({"shots": [{"narration": "x", "actions": [{"move": "spin_showcase", "actor": "bowl", "turns": 9}]}]},
         "shots[0].actions[0].turns"),
        ({"cast": {"bowl": {"asset": {"shape": "bowl"}}, "ball": {"asset": {"shape": "ball"}}},
          "shots": [{"narration": "x", "actions": [{"move": "drop_into", "actor": "bowl", "into": "ball"}]}]},
         "shots[0].actions[0].into"),
        ({"shots": [{"narration": "x", "actions": [
            {"move": "spin_showcase", "actor": "bowl", "at": [0.0, 0.6]},
            {"move": "exit", "actor": "bowl", "at": [0.5, 1.0]}]}]},
         "shots[0].actions[1].at"),
        ({"shots": [{"narration": "x"}]}, "shots[0]"),
        ({"shots": [{"narration": "x", "camera": {"move": "orbit", "continue": True}}]},
         "shots[0].camera.continue"),
        ({"shots": [{"narration": "x", "camera": {"move": "orbit", "target": "nowhere"}}]},
         "shots[0].camera.target"),
        ({"shots": [{"narration": "x", "camera": {"move": "zoom"}}]}, "shots[0].camera.move"),
        ({"shots": [{"narration": "x", "actions": "auto",
                     "auto": [{"move": "appear", "actor": "bowl", "at": [0, 1]}]}]},
         "shots[0].auto[0].at"),
    ],
)
def test_refusals_name_the_field(over, path):
    assert _path(**over) == path
```

- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError: ...validate`).

- [ ] **Step 3: Append resolved models to `models.py`**

```python
class ResolvedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    move: str
    actor: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    at: tuple[float, float]
    index: int


class ResolvedCamera(BaseModel):
    model_config = ConfigDict(extra="forbid")

    move: str = "static"
    target: str = "stage.center"
    params: dict[str, Any] = Field(default_factory=dict)
    continues: bool = False


class ResolvedShot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    index: int
    narration: str
    label: str | None = None
    actions: list[ResolvedAction] = Field(default_factory=list)
    camera: ResolvedCamera = Field(default_factory=ResolvedCamera)
```

- [ ] **Step 4: Implement `validate.py`**

```python
"""Turn a script into resolved shots, or refuse with the exact field that is wrong."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ...core.errors import InvalidInput
from .camera import CAMERA_MOVES
from .geometry import CONTAINERS, anchors
from .models import (
    Action, Explainer3DInputs, ResolvedAction, ResolvedCamera, ResolvedShot, ShapeSpec,
)
from .moves import MOVES
from .timing import auto_layout

LOOKS = frozenset({"diorama-day", "studio-clean"})


def _refuse(path: str, message: str, hint: str = "") -> InvalidInput:
    return InvalidInput(message, hint=hint, details={"path": path})


def _params(model, raw: dict[str, Any], path: str) -> dict[str, Any]:
    try:
        return model.model_validate(raw).model_dump(by_alias=True)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(str(part) for part in error["loc"]) if error["loc"] else ""
        raise _refuse(f"{path}.{field}" if field else path, error["msg"]) from None


def resolve_script(inputs: Explainer3DInputs) -> list[ResolvedShot]:
    if inputs.look not in LOOKS:
        raise _refuse("look", f"unknown look {inputs.look!r}", hint=f"one of {sorted(LOOKS)}")
    marks = anchors(inputs.set.stage)
    for key, member in inputs.cast.items():
        if isinstance(member.asset, str):
            raise _refuse(
                f"cast.{key}.asset",
                f"model sources like {member.asset!r} are not available yet",
                hint='use a shape, e.g. {"shape": "bowl", "material": "stone"}',
            )
        if member.at is not None and member.at not in marks:
            raise _refuse(f"cast.{key}.at", f"unknown anchor {member.at!r}", hint=f"one of {sorted(marks)}")

    def is_container(ref: str) -> bool:
        asset = inputs.cast[ref].asset
        return isinstance(asset, ShapeSpec) and asset.shape in CONTAINERS

    def check_ref(ref: Any, path: str, *, container: bool = False) -> None:
        if ref is None:
            return
        if container:
            if ref not in inputs.cast:
                raise _refuse(path, f"{ref!r} is not a cast member")
            if not is_container(ref):
                raise _refuse(path, f"{ref!r} is not a container", hint=f"containers: {sorted(CONTAINERS)}")
        elif ref not in inputs.cast and ref not in marks:
            raise _refuse(path, f"{ref!r} is neither a cast member nor an anchor")

    resolved: list[ResolvedShot] = []
    for index, shot in enumerate(inputs.shot_list()):
        base = f"shots[{index}]"
        if shot.actions == "auto":
            for j, action in enumerate(shot.auto):
                if action.at is not None:
                    raise _refuse(f"{base}.auto[{j}].at", "auto actions are laid out for you; drop `at`")
            raw_actions, key = list(shot.auto), "auto"
        else:
            raw_actions, key = list(shot.actions), "actions"

        missing = [j for j, action in enumerate(raw_actions) if action.at is None]
        layout = dict(zip(missing, auto_layout(len(missing))))
        actions: list[ResolvedAction] = []
        for j, action in enumerate(raw_actions):
            path = f"{base}.{key}[{j}]"
            actions.append(_resolve_action(action, j, path, layout.get(j), inputs, check_ref, is_container))

        for j, later in enumerate(actions):
            for earlier in actions[:j]:
                if later.actor and later.actor == earlier.actor and later.at[0] < earlier.at[1] and earlier.at[0] < later.at[1]:
                    raise _refuse(
                        f"{base}.{key}[{j}].at",
                        f"{later.actor!r} is already moving in action {earlier.index} at this time",
                        hint="one move per actor at a time; shift `at` so they do not overlap",
                    )

        camera = shot.camera
        if camera is None:
            cam = ResolvedCamera(move="static", target="stage.center", continues=index > 0)
        else:
            if camera.move not in CAMERA_MOVES:
                raise _refuse(f"{base}.camera.move", f"unknown camera move {camera.move!r}",
                              hint=f"one of {sorted(CAMERA_MOVES)}")
            if camera.continue_ and index == 0:
                raise _refuse(f"{base}.camera.continue", "the first shot has no camera to continue")
            target = camera.target or "stage.center"
            check_ref(target, f"{base}.camera.target")
            params = _params(CAMERA_MOVES[camera.move].params, camera.params(), f"{base}.camera")
            cam = ResolvedCamera(move=camera.move, target=target, params=params, continues=camera.continue_)

        if not actions and cam.move == "static":
            raise _refuse(base, "this shot does not move", hint="give it an action or a camera move")
        resolved.append(ResolvedShot(id=shot.id, index=index, narration=shot.narration,
                                     label=shot.label, actions=actions, camera=cam))
    return resolved


def _resolve_action(action: Action, j, path, laid_out, inputs, check_ref, is_container) -> ResolvedAction:
    move = MOVES.get(action.move)
    if move is None:
        raise _refuse(f"{path}.move", f"unknown move {action.move!r}", hint=f"one of {sorted(MOVES)}")
    if move.actor == "required":
        if action.actor is None:
            raise _refuse(f"{path}.actor", f"{move.name} needs an actor")
        if action.actor not in inputs.cast:
            raise _refuse(f"{path}.actor", f"{action.actor!r} is not a cast member")
        if move.actor_container and not is_container(action.actor):
            raise _refuse(f"{path}.actor", f"{move.name} needs a container actor")
    elif action.actor is not None:
        raise _refuse(f"{path}.actor", f"{move.name} takes no actor")
    params = _params(move.params, action.params(), path)
    for name in move.refs:
        check_ref(params.get(name), f"{path}.{name}", container=name in move.container_refs)
    at = action.at if action.at is not None else laid_out
    return ResolvedAction(move=move.name, actor=action.actor, params=params, at=tuple(at), index=j)
```

- [ ] **Step 5: Run tests** — `uv run pytest tests/test_explainer3d.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add reelmachine/recipes/explainer3d/validate.py reelmachine/recipes/explainer3d/models.py tests/test_explainer3d.py
git commit -m "explainer3d: resolve a script into shots, refusing with the exact field"
```

---

### Task 5: Scene state replay and camera chains

**Files:**
- Create: `reelmachine/recipes/explainer3d/state.py`
- Test: `tests/test_explainer3d.py` (append)

**Interfaces:**
- Consumes: `ResolvedShot`, `ShotWindow`, `MOVES`, `camera.pose/default_start`, `geometry`, `world.center`.
- Produces: `build_ctx(inputs) -> Ctx`; `initial_world(inputs) -> World`; `ShotPlan` (pydantic: `shot_id, index, world, actions: list[dict], camera: dict, join: Literal["none","xfade","cut"], cues: list[dict]`); `plan_shots(inputs, shots, windows) -> list[ShotPlan]`. A planned action is `{"move","actor","params","t0","t1","seed","index"}` in **seconds local to the shot**; the camera is `{"move","params","start","target","end","easeIn","easeOut"}`; a cue is `{"at_ms": int (absolute), "sfx": str}`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_explainer3d.py
from reelmachine.recipes.explainer3d.state import build_ctx, initial_world, plan_shots
from reelmachine.recipes.explainer3d.timing import shot_windows


def _plans(over, speech=(2000, 2000, 2000)):
    inputs = Explainer3DInputs.model_validate(_script(**over))
    shots = resolve_script(inputs)
    return plan_shots(inputs, shots, shot_windows(list(speech)[: len(shots)]))


CAST2 = {
    "bowl": {"asset": {"shape": "bowl", "height": 0.2}, "at": "stage.center"},
    "potato": {"asset": {"shape": "ball", "height": 0.1, "colour": "#d9b44a"}},
}


def test_initial_world_hides_unplaced_actors_and_gives_containers_a_fill():
    inputs = Explainer3DInputs.model_validate(_script(cast=CAST2))
    world = initial_world(inputs)
    assert world["bowl"]["visible"] and world["bowl"]["fill"]["level"] == 0.0
    assert not world["potato"]["visible"] and world["potato"]["fill"] is None
    assert build_ctx(inputs)["colours"]["potato"] == "#d9b44a"


def test_state_carries_to_the_next_shot():
    plans = _plans({
        "cast": CAST2,
        "shots": [
            {"narration": "in", "actions": [{"move": "fly_in", "actor": "potato"},
                                            {"move": "drop_into", "actor": "potato", "into": "bowl"}]},
            {"narration": "mash", "actions": [{"move": "mash", "actor": "bowl"}]},
        ],
    })
    assert plans[0].world["bowl"]["fill"]["level"] == 0.0
    assert plans[1].world["bowl"]["fill"]["level"] == pytest.approx(0.15)
    assert plans[1].world["potato"]["visible"] is False


def test_actions_become_local_seconds_with_seeds_and_cues():
    plans = _plans({"cast": CAST2, "shots": [{"narration": "x", "actions": [
        {"move": "fly_in", "actor": "potato", "at": [0.0, 0.5]}]}]}, speech=(2000,))
    action = plans[0].actions[0]
    duration_s = (250 + 2000 + 350) / 1000
    assert action["t0"] == 0.0 and action["t1"] == pytest.approx(duration_s * 0.5)
    assert isinstance(action["seed"], int)
    assert plans[0].cues == [{"at_ms": 0, "sfx": "whoosh"}]


def test_camera_chain_continues_and_eases_only_at_the_ends():
    plans = _plans({"shots": [
        {"narration": "a", "camera": {"move": "orbit", "target": "bowl", "from": 0, "to": 30}},
        {"narration": "b", "camera": {"move": "orbit", "target": "bowl", "to": 60, "continue": True}},
        {"narration": "c", "camera": {"move": "push_in", "target": "bowl", "continue": True}},
        {"narration": "d", "camera": {"move": "orbit", "target": "bowl"}},
    ]}, speech=(2000, 2000, 2000, 2000))
    assert plans[1].camera["start"] == plans[0].camera["end"]
    assert plans[2].camera["start"] == plans[1].camera["end"]
    assert [p.join for p in plans] == ["none", "cut", "cut", "xfade"]
    assert [(p.camera["easeIn"], p.camera["easeOut"]) for p in plans] == [
        (True, False), (False, False), (False, True), (True, True)]


def test_shot_without_camera_holds_the_previous_pose():
    plans = _plans({"shots": [
        {"narration": "a", "camera": {"move": "orbit", "target": "bowl"}},
        {"narration": "b", "actions": [{"move": "spin_showcase", "actor": "bowl"}]},
    ]}, speech=(2000, 2000))
    assert plans[1].camera["move"] == "static"
    assert plans[1].camera["start"] == plans[0].camera["end"] and plans[1].join == "cut"
```

- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError: ...state`).

- [ ] **Step 3: Implement `state.py`**

```python
"""Scene state flows from shot to shot, and cameras chain across cuts.

Each shot starts from the world the previous shots left (every move applied to its end)
and from either a fresh camera or the previous shot's final pose. A run of continuing
shots is one chain: it eases in at its first shot and out at its last, and keeps moving
through the joins between, which are hard cuts.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .camera import default_start, pose
from .geometry import CONTAINERS, anchors, dims
from .models import Explainer3DInputs, ResolvedShot, ShapeSpec
from .moves import MOVES
from .timing import ShotWindow
from .world import Ctx, World, center

MATERIAL_COLOURS = {
    "stone": "#8f877c", "wood": "#8a5a33", "ceramic": "#c9b8a3", "metal": "#9aa3ab",
    "glass": "#cfe8ef", "dough": "#e2c35a", "salt": "#f4f4f0", "grain": "#c79a4a",
    "water": "#5aa6c8", "cloth": "#c8b48a",
}
DEFAULT_FILL = "#d9d27a"


class ShotPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    index: int
    world: dict[str, Any]
    actions: list[dict[str, Any]] = Field(default_factory=list)
    camera: dict[str, Any]
    join: Literal["none", "xfade", "cut"]
    cues: list[dict[str, Any]] = Field(default_factory=list)


def _shape(inputs: Explainer3DInputs, key: str) -> ShapeSpec:
    asset = inputs.cast[key].asset
    assert isinstance(asset, ShapeSpec)  # validate.py refuses anything else in plan 1
    return asset


def build_ctx(inputs: Explainer3DInputs) -> Ctx:
    marks = anchors(inputs.set.stage)
    ctx: Ctx = {"anchors": {k: list(v) for k, v in marks.items()}, "dims": {}, "homes": {}, "colours": {}}
    for key, member in inputs.cast.items():
        shape = _shape(inputs, key)
        ctx["dims"][key] = list(dims(shape))
        ctx["homes"][key] = list(marks[member.at or "stage.center"])
        ctx["colours"][key] = shape.colour or MATERIAL_COLOURS[shape.material]
    return ctx


def initial_world(inputs: Explainer3DInputs) -> World:
    marks = anchors(inputs.set.stage)
    world: World = {}
    for key, member in inputs.cast.items():
        shape = _shape(inputs, key)
        world[key] = {
            "position": list(marks[member.at or "stage.center"]),
            "visible": member.at is not None,
            "rotationY": 0.0,
            "scale": 1.0,
            "fill": {"level": 0.0, "colour": DEFAULT_FILL, "surface": "liquid"} if shape.shape in CONTAINERS else None,
        }
    return world


def _seed(shot_id: str, actor: str | None, index: int) -> int:
    return int(hashlib.sha256(f"{shot_id}|{actor}|{index}".encode()).hexdigest()[:8], 16)


def plan_shots(
    inputs: Explainer3DInputs, shots: list[ResolvedShot], windows: list[ShotWindow]
) -> list[ShotPlan]:
    ctx = build_ctx(inputs)
    world = initial_world(inputs)
    plans: list[ShotPlan] = []
    previous_end: dict[str, Any] | None = None
    for shot, window in zip(shots, windows):
        seconds = window.duration_ms / 1000.0
        actions = sorted(
            (
                {
                    "move": a.move, "actor": a.actor, "params": a.params,
                    "t0": round(a.at[0] * seconds, 6), "t1": round(a.at[1] * seconds, 6),
                    "seed": _seed(shot.id, a.actor, a.index), "index": a.index,
                }
                for a in shot.actions
            ),
            key=lambda item: (item["t0"], item["index"]),
        )
        cues = []
        for action in actions:
            sfx = MOVES[action["move"]].sfx
            if sfx is not None:
                at = action["t0"] + (action["t1"] - action["t0"]) * sfx[1]
                cues.append({"at_ms": window.start_ms + int(round(at * 1000)), "sfx": sfx[0]})

        cam = shot.camera
        target = center(ctx, world, cam.target)
        if cam.continues and previous_end is not None:
            start = previous_end
        else:
            start = default_start(cam.move, cam.params, target)
        end = pose(cam.move, cam.params, start, target, 1.0)
        plans.append(ShotPlan(
            shot_id=shot.id, index=shot.index, world=world, actions=actions,
            camera={"move": cam.move, "params": cam.params, "start": start, "target": target,
                    "end": end, "easeIn": True, "easeOut": True},
            join="none" if shot.index == 0 else ("cut" if cam.continues else "xfade"),
            cues=cues,
        ))
        for action in actions:
            world = MOVES[action["move"]].end(world, action, ctx)
        previous_end = end

    # a chain eases in at its first shot and out at its last; joins inside keep moving
    for i, plan in enumerate(plans):
        continued_from = plan.join == "cut"
        continues_into = i + 1 < len(plans) and plans[i + 1].join == "cut"
        plan.camera["easeIn"] = not continued_from
        plan.camera["easeOut"] = not continues_into
    return plans
```

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_explainer3d.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add reelmachine/recipes/explainer3d/state.py tests/test_explainer3d.py
git commit -m "explainer3d: scene state carries across shots; cameras chain across cuts"
```

---

### Task 6: Runtime — pure modules, Node tests and the Python cross-check

**Files:**
- Create: `reelmachine/recipes/explainer3d/runtime/math.mjs`, `poses.mjs`, `camera.mjs`, `particles.mjs`, `captions.mjs`, `tools/evaluate.mjs`, `tests/pure.test.mjs`
- Test: `tests/test_explainer3d.py` (append the cross-check)

**Interfaces:**
- Produces (JS): `applyAction(world, action, ctx, p) -> {world, fx}`; `cameraPose(move, params, start, target, p)`; `ease(p, easeIn, easeOut)`; `stream/grains/steam(seed, …, t)` → `[[x,y,z,size,alpha], …]`; `captionState(words, t) -> {active, speaking}`; `stateAt(shot, t) -> {world, fx}`. `fx` items: `{type:"tilt", actor, angle}`, `{type:"stream", seed, from, to, t, colour}`, `{type:"grains", seed, origin, floorY, t, colour}`, `{type:"steam", seed, origin, t, intensity}`, `{type:"pestle", position}`.
- `tools/evaluate.mjs` reads `{"cases":[…]}` on stdin and prints `{"results":[…]}`; case kinds `"action"` (`world, action, ctx, p` → world) and `"camera"` (`move, params, start, target, p` → pose).

- [ ] **Step 1: Write the failing cross-check (Python)**

```python
# append to tests/test_explainer3d.py
import json
import shutil
import subprocess
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "reelmachine/recipes/explainer3d/runtime"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _evaluate(cases):
    proc = subprocess.run(["node", str(RUNTIME / "tools/evaluate.mjs")], input=json.dumps({"cases": cases}),
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)["results"]


def _close(a, b, path="$"):
    if isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            _close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a == pytest.approx(b, abs=1e-6), path
    else:
        assert a == b, path


@node
def test_runtime_end_states_match_python():
    world = _world()
    world["bottle"] = {"position": [-0.4, 0.9, 0.0], "visible": True, "rotationY": 0.0, "scale": 1.0, "fill": None}
    ctx = {**CTX, "dims": {**CTX["dims"], "bottle": [0.06, 0.2, 0.06]},
           "homes": {**CTX["homes"], "bottle": [-0.4, 0.9, 0.0]}, "colours": {**CTX["colours"], "bottle": "#cfe8ef"}}
    world["bowl"]["fill"]["level"] = 0.9  # pours past full must clamp
    cases, expected = [], []
    for name, actor, raw in [
        ("appear", "potato", {}), ("scale_pop", "potato", {}), ("fly_in", "potato", {"from": "left"}),
        ("fly_in", "potato", {"to": "bowl"}), ("drop_into", "potato", {"into": "bowl", "colour": "#ff0000"}),
        ("place_on", "bottle", {"on": "bowl"}), ("spin_showcase", "bowl", {"turns": 1.5}),
        ("exit", "bowl", {"to": "sky"}), ("pour", "bottle", {"into": "bowl", "fill_add": 0.5}),
        ("sprinkle", None, {"into": "bowl"}), ("mash", "bowl", {"colour": "#aabbcc"}), ("steam", "bowl", {}),
    ]:
        move = MOVES[name]
        action = {"move": name, "actor": actor, "params": move.params.model_validate(raw).model_dump(by_alias=True),
                  "t0": 0.0, "t1": 1.0, "seed": 7, "index": 0}
        cases.append({"kind": "action", "world": world, "action": action, "ctx": ctx, "p": 1.0})
        expected.append(move.end(world, action, ctx))
    for move, raw in [("static", {}), ("orbit", {"from": 10, "to": 80}), ("push_in", {"factor": 0.4}),
                      ("dolly", {"offset": [0.5, 0.1, 0]})]:
        params = CAMERA_MOVES[move].params.model_validate(raw).model_dump(by_alias=True)
        target = [0.1, 1.0, 0.0]
        start = default_start(move, params, target)
        for p in (0.0, 0.3, 1.0):
            cases.append({"kind": "camera", "move": move, "params": params, "start": start, "target": target, "p": p})
            expected.append(pose(move, params, start, target, p))
    for got, want in zip(_evaluate(cases), expected):
        _close(got, want)


@node
def test_runtime_node_unit_tests_pass():
    proc = subprocess.run(["node", "--test", str(RUNTIME / "tests")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_explainer3d.py -q -k runtime` → FAIL (`evaluate.mjs` missing).

- [ ] **Step 3: Write `runtime/math.mjs`**

```js
// Pure helpers. Nothing in the runtime reads a clock: every value is a function of p or t.
export const clamp01 = (x) => Math.min(1, Math.max(0, x));
export const lerp = (a, b, t) => a + (b - a) * t;
export const lerp3 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
export const add3 = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const easeInCubic = (p) => p * p * p;
export const easeOutCubic = (p) => 1 - Math.pow(1 - p, 3);
export const easeInOutCubic = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
// back ease with c1 = 0.8 overshoots by ~4% before settling exactly on 1
export const easeOutBack = (p) => { const c1 = 0.8, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); };
// a critically damped spring, normalised so it lands exactly on 1 at p = 1
export function spring(p, omega = 8) {
  const raw = (x) => 1 - (1 + omega * x) * Math.exp(-omega * x);
  return raw(clamp01(p)) / raw(1);
}
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rgb = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
export function mixHex(a, b, t) {
  const x = rgb(a), y = rgb(b);
  return "#" + [0, 1, 2].map((i) => Math.max(0, Math.min(255, Math.floor(x[i] + (y[i] - x[i]) * t + 0.5)))
    .toString(16).padStart(2, "0")).join("");
}
export const FLOOR = 0.15;
export const fillY = (h, level) => h * (FLOOR + (1 - FLOOR) * 0.9 * level);
```

- [ ] **Step 4: Write `runtime/poses.mjs`**

```js
// Object and process moves: (world, action, ctx, p) -> { world, fx }.
// The end state at p = 1 must equal moves.py exactly; tests/test_explainer3d.py checks it.
import { add3, clamp01, easeInCubic, easeInOutCubic, easeOutBack, easeOutCubic, fillY, lerp, lerp3, mixHex, spring } from "./math.mjs";

export const OFFSETS = { sky: [0, 3, 0], left: [-2.5, 0.5, 0], right: [2.5, 0.5, 0], front: [0, 0.5, 2.5] };

const point = (ctx, w, ref) => (ref in ctx.anchors ? [...ctx.anchors[ref]] : [...w[ref].position]);
function top(ctx, w, ref) {
  if (ref in ctx.anchors) return [...ctx.anchors[ref]];
  const a = w[ref];
  return [a.position[0], a.position[1] + ctx.dims[ref][1] * a.scale, a.position[2]];
}
function interior(ctx, w, ref, level) {
  const a = w[ref];
  return [a.position[0], a.position[1] + fillY(ctx.dims[ref][1], level), a.position[2]];
}
const setActor = (w, id, patch) => ({ ...w, [id]: { ...w[id], ...patch } });
const setFill = (w, id, patch) => setActor(w, id, { fill: { ...w[id].fill, ...patch } });
function pourFill(w, id, add, colour, q) {
  const f = w[id].fill, level0 = f.level;
  const level = Math.min(1, level0 + add * q);
  const mixed = level0 <= 0 ? colour : mixHex(f.colour, colour, (add / (level0 + add)) * q);
  return setFill(w, id, { level, colour: q > 0 ? mixed : f.colour });
}
// a parabolic hop from a to b; lift at the midpoint
const hop = (a, b, t, lift) => { const p = lerp3(a, b, t); p[1] += Math.sin(Math.PI * t) * lift; return p; };

const MOVES = {
  appear(w, a, ctx, p) { return { world: setActor(w, a.actor, { visible: true, scale: p >= 1 ? 1 : easeOutCubic(p) }), fx: [] }; },
  scale_pop(w, a, ctx, p) { return { world: setActor(w, a.actor, { visible: true, scale: p >= 1 ? 1 : easeOutBack(p) }), fx: [] }; },
  fly_in(w, a, ctx, p) {
    const to = a.params.to == null ? [...ctx.homes[a.actor]] : (a.params.to in ctx.anchors ? point(ctx, w, a.params.to) : top(ctx, w, a.params.to));
    const from = add3(to, OFFSETS[a.params.from]);
    return { world: setActor(w, a.actor, { visible: true, position: p >= 1 ? to : lerp3(from, to, spring(p)) }), fx: [] };
  },
  drop_into(w, a, ctx, p) {
    const into = a.params.into, start = w[a.actor].position;
    const level0 = w[into].fill.level;
    const above = add3(top(ctx, w, into), [0, 0.45, 0]);
    const inside = interior(ctx, w, into, level0);
    let position, scale = 1, visible = true;
    if (p < 0.55) position = hop(start, above, easeInOutCubic(p / 0.55), 0.3);
    else if (p < 0.8) position = lerp3(above, inside, easeInCubic((p - 0.55) / 0.25));
    else { const q = (p - 0.8) / 0.2; position = inside; scale = 1 - q; visible = q < 1; }
    if (p >= 1) { position = inside; scale = 0; visible = false; }
    const colour = a.params.colour || ctx.colours[a.actor];
    let world = pourFill(w, into, a.params.fill_add, colour, clamp01((p - 0.75) / 0.25));
    world = setActor(world, a.actor, { position, scale, visible });
    return { world, fx: [] };
  },
  place_on(w, a, ctx, p) {
    const to = top(ctx, w, a.params.on);
    return { world: setActor(w, a.actor, { visible: true, position: p >= 1 ? to : hop(w[a.actor].position, to, spring(p, 7), 0.3) }), fx: [] };
  },
  spin_showcase(w, a, ctx, p) {
    const r0 = w[a.actor].rotationY, pos = [...w[a.actor].position];
    if (p < 1) pos[1] += Math.sin(Math.PI * p) * 0.08;
    return { world: setActor(w, a.actor, { rotationY: r0 + a.params.turns * 2 * Math.PI * (p >= 1 ? 1 : easeInOutCubic(p)), position: pos }), fx: [] };
  },
  exit(w, a, ctx, p) {
    const start = w[a.actor].position, off = OFFSETS[a.params.to];
    return { world: setActor(w, a.actor, { position: p >= 1 ? add3(start, off) : lerp3(start, add3(start, off), easeInCubic(p)), visible: p < 1 }), fx: [] };
  },
  pour(w, a, ctx, p) {
    const home = w[a.actor].position, into = a.params.into;
    const spot = add3(top(ctx, w, into), [-0.22, 0.35, 0]);
    const TILT = 1.9;
    let position, tilt, fx = [];
    if (p < 0.25) { const q = easeInOutCubic(p / 0.25); position = lerp3(home, spot, q); tilt = q * TILT; }
    else if (p < 0.75) {
      position = spot; tilt = TILT;
      const level = Math.min(1, w[into].fill.level + a.params.fill_add * clamp01((p - 0.3) / 0.45));
      fx.push({ type: "stream", seed: a.seed, from: add3(spot, [0.08, 0.02, 0]), to: interior(ctx, w, into, level), t: (p - 0.25) * (a.t1 - a.t0), colour: a.params.colour });
    } else { const q = easeInOutCubic((p - 0.75) / 0.25); position = lerp3(spot, home, q); tilt = TILT * (1 - q); }
    if (p >= 1) { position = [...home]; tilt = 0; }
    let world = pourFill(w, into, a.params.fill_add, a.params.colour, clamp01((p - 0.3) / 0.45));
    world = setActor(world, a.actor, { position });
    if (tilt > 0) fx.push({ type: "tilt", actor: a.actor, angle: tilt });
    return { world, fx };
  },
  sprinkle(w, a, ctx, p) {
    const into = a.params.into, f = w[into].fill;
    const world = setFill(w, into, { level: Math.min(1, f.level + a.params.fill_add * clamp01((p - 0.2) / 0.7)) });
    const fx = p > 0.1 && p < 0.9 ? [{ type: "grains", seed: a.seed, origin: add3(top(ctx, w, into), [0, 0.6, 0]), floorY: interior(ctx, w, into, f.level)[1], t: (p - 0.1) * (a.t1 - a.t0), colour: a.params.colour }] : [];
    return { world, fx };
  },
  mash(w, a, ctx, p) {
    const f = w[a.actor].fill;
    const colour = a.params.colour ? mixHex(f.colour, a.params.colour, p) : f.colour;
    const world = setFill(w, a.actor, { surface: p >= 0.15 ? "paste" : f.surface, colour });
    const fx = [];
    if (p > 0.05 && p < 0.95) {
      const base = interior(ctx, w, a.actor, f.level);
      fx.push({ type: "pestle", position: [base[0], base[1] + 0.02 + Math.abs(Math.sin(6 * Math.PI * p)) * 0.18, base[2]] });
    }
    return { world, fx };
  },
  steam(w, a, ctx, p) {
    return { world: w, fx: p < 1 ? [{ type: "steam", seed: a.seed, origin: top(ctx, w, a.actor), t: p * (a.t1 - a.t0), intensity: a.params.intensity }] : [] };
  },
};

export function applyAction(world, action, ctx, p) {
  const move = MOVES[action.move];
  if (!move) throw new Error(`unknown move ${action.move}`);
  return move(world, action, ctx, clamp01(p));
}

// The scene at local time t: every started action applied in start order, each at its own progress.
export function stateAt(shot, t) {
  let world = shot.world, fx = [];
  for (const action of shot.actions) {
    if (t < action.t0) continue;
    const p = action.t1 > action.t0 ? Math.min(1, (t - action.t0) / (action.t1 - action.t0)) : 1;
    const out = applyAction(world, action, shot.ctx, p);
    world = out.world;
    if (p < 1) fx = fx.concat(out.fx);
  }
  return { world, fx };
}
```

- [ ] **Step 5: Write `runtime/camera.mjs`, `particles.mjs`, `captions.mjs`**

```js
// runtime/camera.mjs — mirrors camera.py
import { lerp3 } from "./math.mjs";

export function ease(p, easeIn, easeOut) {
  p = Math.min(1, Math.max(0, p));
  if (easeIn && easeOut) return p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
  if (easeIn) return 1 - Math.cos((p * Math.PI) / 2);
  if (easeOut) return Math.sin((p * Math.PI) / 2);
  return p;
}

export function cameraPose(move, params, start, target, p) {
  const sp = start.position, st = start.target;
  switch (move) {
    case "static":
      return { position: [...sp], target: [...st], fov: start.fov };
    case "orbit": {
      const dx = sp[0] - target[0], dy = sp[1] - target[1], dz = sp[2] - target[2];
      const a0 = Math.atan2(dx, dz), r = Math.hypot(dx, dz), a1 = (params.to * Math.PI) / 180;
      const a = a0 + (a1 - a0) * p;
      return { position: [target[0] + r * Math.sin(a), target[1] + dy, target[2] + r * Math.cos(a)], target: lerp3(st, target, p), fov: start.fov };
    }
    case "push_in": {
      const k = 1 + (params.factor - 1) * p;
      return { position: [0, 1, 2].map((i) => target[i] + (sp[i] - target[i]) * k), target: lerp3(st, target, p), fov: start.fov };
    }
    case "dolly": {
      const o = params.offset;
      return { position: [0, 1, 2].map((i) => sp[i] + o[i] * p), target: [0, 1, 2].map((i) => st[i] + o[i] * p), fov: start.fov };
    }
    default:
      throw new Error(`unknown camera move ${move}`);
  }
}
```

```js
// runtime/particles.mjs — closed-form in t: a particle's position never depends on the previous frame
import { mulberry32 } from "./math.mjs";

export function stream(seed, from, to, t, count = 90) {
  const rnd = mulberry32(seed), out = [];
  for (let i = 0; i < count; i++) {
    const phase = rnd(), jx = (rnd() - 0.5) * 0.025, jz = (rnd() - 0.5) * 0.025;
    const age = (t / 0.45 + phase) % 1;
    out.push([from[0] + (to[0] - from[0]) * age + jx, from[1] + (to[1] - from[1]) * age * age, from[2] + (to[2] - from[2]) * age + jz, 0.012, 1]);
  }
  return out;
}

export function grains(seed, origin, floorY, t, count = 70) {
  const rnd = mulberry32(seed), out = [];
  for (let i = 0; i < count; i++) {
    const phase = rnd(), r = rnd() * 0.12, a = rnd() * Math.PI * 2;
    const age = (t / 0.7 + phase) % 1;
    out.push([origin[0] + Math.cos(a) * r * age, origin[1] - (origin[1] - floorY) * age * age, origin[2] + Math.sin(a) * r * age, 0.008, 1]);
  }
  return out;
}

export function steam(seed, origin, t, intensity, count = 40) {
  const rnd = mulberry32(seed), out = [];
  for (let i = 0; i < count; i++) {
    const phase = rnd(), jx = (rnd() - 0.5) * 0.15, jz = (rnd() - 0.5) * 0.15;
    const age = (t / 2.2 + phase) % 1;
    out.push([origin[0] + jx + Math.sin((age * 3 + phase) * 6.283) * 0.04, origin[1] + age * 0.9 * intensity, origin[2] + jz, 0.03 + age * 0.09, (1 - age) * 0.6]);
  }
  return out;
}
```

```js
// runtime/captions.mjs — which word is being spoken at t (seconds, shot-local)
export function captionState(words, t) {
  let active = -1;
  for (let i = 0; i < words.length; i++) {
    if (words[i].start <= t) active = i;
    else break;
  }
  const speaking = active >= 0 && t <= words[active].end + 0.25;
  return { active, speaking };
}
```

- [ ] **Step 6: Write `runtime/tools/evaluate.mjs` and `runtime/tests/pure.test.mjs`**

```js
// runtime/tools/evaluate.mjs — the cross-check entry used by tests/test_explainer3d.py
import { applyAction } from "../poses.mjs";
import { cameraPose } from "../camera.mjs";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", () => {
  const { cases } = JSON.parse(input);
  const results = cases.map((c) =>
    c.kind === "action" ? applyAction(c.world, c.action, c.ctx, c.p).world : cameraPose(c.move, c.params, c.start, c.target, c.p),
  );
  process.stdout.write(JSON.stringify({ results }));
});
```

```js
// runtime/tests/pure.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { easeOutBack, mixHex, mulberry32, spring } from "../math.mjs";
import { applyAction, stateAt } from "../poses.mjs";
import { cameraPose, ease } from "../camera.mjs";
import { grains, steam, stream } from "../particles.mjs";
import { captionState } from "../captions.mjs";

const ctx = { anchors: { "stage.center": [0, 0.9, 0] }, dims: { bowl: [0.44, 0.2, 0.44], b: [0.1, 0.1, 0.1] }, homes: { b: [0.3, 0.9, 0] }, colours: { b: "#d9b44a" } };
const world = () => ({
  bowl: { position: [0, 0.9, 0], visible: true, rotationY: 0, scale: 1, fill: { level: 0, colour: "#d9d27a", surface: "liquid" } },
  b: { position: [0.3, 0.9, 0], visible: false, rotationY: 0, scale: 1, fill: null },
});

test("springs and eases hit their ends exactly", () => {
  assert.equal(spring(0), 0);
  assert.equal(spring(1), 1);
  assert.ok(Math.max(...Array.from({ length: 101 }, (_, i) => easeOutBack(i / 100))) <= 1.05);
  assert.equal(ease(1, true, true), 1);
});

test("the prng is seeded", () => {
  const a = mulberry32(42), b = mulberry32(42);
  assert.deepEqual([a(), a(), a()], [b(), b(), b()]);
});

test("mixHex rounds like Python", () => assert.equal(mixHex("#000000", "#ffffff", 0.5), "#808080"));

test("moves are deterministic and start where the world is", () => {
  const action = { move: "fly_in", actor: "b", params: { from: "sky", to: null }, t0: 0, t1: 1, seed: 1 };
  assert.deepEqual(applyAction(world(), action, ctx, 0.37), applyAction(world(), action, ctx, 0.37));
  const start = applyAction(world(), action, ctx, 0).world.b.position;
  assert.deepEqual(start, [0.3, 3.9, 0]);
});

test("stateAt applies earlier actions to their end", () => {
  const shot = { world: world(), ctx, actions: [
    { move: "fly_in", actor: "b", params: { from: "sky", to: null }, t0: 0, t1: 1, seed: 1 },
    { move: "drop_into", actor: "b", params: { into: "bowl", fill_add: 0.15, colour: null }, t0: 1, t1: 2, seed: 2 },
  ] };
  const done = stateAt(shot, 5).world;
  assert.equal(done.b.visible, false);
  assert.ok(Math.abs(done.bowl.fill.level - 0.15) < 1e-9);
  assert.equal(stateAt(shot, 0.5).world.bowl.fill.level, 0);
});

test("particles are pure in t", () => {
  assert.deepEqual(stream(3, [0, 1, 0], [0, 0, 0], 0.4), stream(3, [0, 1, 0], [0, 0, 0], 0.4));
  assert.deepEqual(grains(3, [0, 1, 0], 0.5, 0.4), grains(3, [0, 1, 0], 0.5, 0.4));
  assert.equal(steam(3, [0, 1, 0], 0.4, 1).length, 40);
});

test("orbit keeps its radius", () => {
  const start = { position: [0, 1, 2], target: [0, 0, 0], fov: 40 };
  const end = cameraPose("orbit", { to: 90 }, start, [0, 0, 0], 1);
  assert.ok(Math.abs(Math.hypot(end.position[0], end.position[2]) - 2) < 1e-9);
});

test("captions follow the words", () => {
  const words = [{ start: 0.25, end: 0.5 }, { start: 0.6, end: 1.0 }];
  assert.equal(captionState(words, 0.1).active, -1);
  assert.equal(captionState(words, 0.7).active, 1);
  assert.equal(captionState(words, 2.0).speaking, false);
});
```

- [ ] **Step 7: Run tests**

Run: `node --test reelmachine/recipes/explainer3d/runtime/tests && uv run pytest tests/test_explainer3d.py -q`
Expected: PASS (the Python cross-check skips only where `node` is absent).

- [ ] **Step 8: Commit**

```bash
git add reelmachine/recipes/explainer3d/runtime tests/test_explainer3d.py
git commit -m "explainer3d: the pure runtime — moves, camera, particles, captions — checked against Python"
```

---

### Task 7: Runtime scene, vendored Three.js, looks, and the shot compiler

**Files:**
- Create: `runtime/shapes.mjs`, `runtime/look.mjs`, `runtime/main.mjs`, `runtime/vendor/{three.module.js,three.core.js,LICENSE}`, `runtime/tests/shapes.test.mjs`
- Create: `looks/diorama-day.json`, `looks/studio-clean.json`, `compile.py`
- Modify: `pyproject.toml` (package-data), `THIRD_PARTY_NOTICES.md`
- Test: `tests/test_explainer3d.py` (append)

**Interfaces:**
- Consumes: `ShotPlan`, `ShotWindow`, `build_ctx`, `Explainer3DInputs`, `dims`.
- Produces: `load_look(name) -> dict`; `RUNTIME_VERSION: str`; `shot_document(inputs, plan, window, words, ctx, render_ms) -> dict`; `write_project(doc, dest) -> Path`; `project_key(doc) -> str`. JS: `buildShape(THREE, spec, dims) -> {group, fill, innerRadius}`, `innerRadius(kind, w, h, y)`, `buildSet(THREE, scene, look, set, anchors)`.

- [ ] **Step 1: Vendor Three.js 0.181.2**

```bash
D=reelmachine/recipes/explainer3d/runtime/vendor && mkdir -p $D && T=$(mktemp -d) \
  && (cd $T && npm pack three@0.181.2 --silent && tar xzf three-0.181.2.tgz) \
  && cp $T/package/build/three.module.js $T/package/build/three.core.js $T/package/LICENSE $D/ \
  && head -c 200 $D/three.module.js && rm -rf $T
```

Append to `THIRD_PARTY_NOTICES.md`:

```markdown
## three.js 0.181.2 (MIT)

`reelmachine/recipes/explainer3d/runtime/vendor/three.module.js` and `three.core.js` are
the unmodified build files of three.js 0.181.2 (https://github.com/mrdoob/three.js),
copyright © 2010–2025 three.js authors, MIT licence (`vendor/LICENSE`). They are shipped
so a shot renders with no network access.
```

In `pyproject.toml` `[tool.setuptools.package-data]` `reelmachine = [...]` add:

```toml
    "recipes/explainer3d/looks/*.json",
    "recipes/explainer3d/runtime/*.mjs",
    "recipes/explainer3d/runtime/tools/*.mjs",
    "recipes/explainer3d/runtime/vendor/*",
```

- [ ] **Step 2: Write the failing tests**

```js
// runtime/tests/shapes.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "../vendor/three.module.js";
import { buildShape, innerRadius, KINDS } from "../shapes.mjs";

test("every shape's bounding box is its declared size", () => {
  for (const kind of KINDS) {
    const dims = [0.4, 0.3, 0.4];
    const { group } = buildShape(THREE, { shape: kind, material: "stone", colour: null }, dims);
    const size = new THREE.Box3().setFromObject(group).getSize(new THREE.Vector3());
    assert.ok(Math.abs(size.x - 0.4) < 0.01, `${kind} width ${size.x}`);
    assert.ok(Math.abs(size.y - 0.3) < 0.01, `${kind} height ${size.y}`);
  }
});

test("containers have an inside narrower than the outside", () => {
  for (const kind of ["bowl", "cup", "pot", "jar"]) {
    const r = innerRadius(kind, 0.4, 0.3, 0.2);
    assert.ok(r > 0 && r < 0.2, `${kind} ${r}`);
  }
});
```

```python
# append to tests/test_explainer3d.py
from reelmachine.recipes.explainer3d.compile import (
    RUNTIME_VERSION, load_look, project_key, shot_document, write_project,
)


def _doc(tmp_path=None):
    inputs = Explainer3DInputs.model_validate(_script(cast=CAST2, shots=[{"narration": "In it goes.", "label": "Step 1",
        "actions": [{"move": "fly_in", "actor": "potato"}], "camera": {"move": "orbit", "target": "bowl"}}]))
    shots = resolve_script(inputs)
    windows = shot_windows([1500])
    plan = plan_shots(inputs, shots, windows)[0]
    words = [{"text": "In", "start": 0.25, "end": 0.5}]
    return shot_document(inputs, plan, windows[0], words, build_ctx(inputs), render_ms=windows[0].duration_ms + 300)


def test_shot_document_is_complete_and_keyed():
    doc = _doc()
    assert doc["width"] == 1080 and doc["height"] == 1920 and doc["fps"] == 30
    assert doc["duration"] == pytest.approx(2.1) and doc["renderDuration"] == pytest.approx(2.4)
    assert doc["look"]["id"] == "diorama-day" and doc["captions"]["label"] == "Step 1"
    assert doc["actors"]["potato"]["dims"] == [0.1, 0.1, 0.1]
    assert project_key(doc) == project_key(_doc()) and len(RUNTIME_VERSION) == 16


def test_write_project_is_self_contained(tmp_path):
    out = write_project(_doc(), tmp_path / "shot-1")
    html = (out / "index.html").read_text()
    assert 'data-composition-id="shot"' in html and "data-no-timeline" in html
    assert 'data-duration="2.400"' in html and '"three": "./vendor/three.module.js"' in html
    assert (out / "runtime/main.mjs").is_file() and (out / "vendor/three.module.js").is_file()
    assert "</script>" not in html.split('id="shot-data"')[1].split("</script>")[0]


def test_looks_load():
    assert load_look("studio-clean")["id"] == "studio-clean"
```

- [ ] **Step 3: Run to verify failure** — `node --test …/runtime/tests` fails (`shapes.mjs` missing); `uv run pytest tests/test_explainer3d.py -q -k "document or project or looks"` fails (`compile` missing).

- [ ] **Step 4: Write `runtime/shapes.mjs`**

```js
// Shapes built from parameters. Origin = bottom centre; bbox = declared dims (checked by tests).
export const KINDS = ["bowl", "cup", "pot", "jar", "bottle", "ball", "box", "sack", "pile", "plinth"];
const MATERIALS = {
  stone: { colour: "#8f877c", roughness: 0.95 }, wood: { colour: "#8a5a33", roughness: 0.8 },
  ceramic: { colour: "#c9b8a3", roughness: 0.5 }, metal: { colour: "#9aa3ab", roughness: 0.35, metalness: 0.9 },
  glass: { colour: "#cfe8ef", roughness: 0.1, opacity: 0.5 }, dough: { colour: "#e2c35a", roughness: 0.85 },
  salt: { colour: "#f4f4f0", roughness: 0.9 }, grain: { colour: "#c79a4a", roughness: 0.9 },
  water: { colour: "#5aa6c8", roughness: 0.2, opacity: 0.8 }, cloth: { colour: "#c8b48a", roughness: 1.0 },
};
export const SURFACES = { liquid: 0.15, paste: 0.7, grain: 0.95, dough: 0.85 };
// outer profiles as [r/w, y/h]; containers continue down their inner wall to the floor
const PROFILES = {
  bowl: [[0, 0], [0.3, 0], [0.42, 0.35], [0.5, 1], [0.47, 1], [0.4, 0.4], [0.26, 0.15], [0, 0.15]],
  cup: [[0, 0], [0.4, 0], [0.5, 1], [0.45, 1], [0.36, 0.15], [0, 0.15]],
  pot: [[0, 0], [0.45, 0], [0.5, 0.15], [0.5, 1], [0.46, 1], [0.46, 0.15], [0, 0.15]],
  jar: [[0, 0], [0.45, 0], [0.5, 0.2], [0.5, 0.8], [0.35, 0.92], [0.35, 1], [0.31, 1], [0.31, 0.92], [0.46, 0.8], [0.46, 0.2], [0, 0.15]],
  bottle: [[0, 0], [0.5, 0], [0.5, 0.62], [0.18, 0.8], [0.15, 0.94], [0, 0.94]],
  sack: [[0, 0], [0.42, 0], [0.5, 0.3], [0.48, 0.75], [0.3, 0.92], [0.12, 1], [0, 1]],
  pile: [[0, 0], [0.5, 0], [0.3, 0.45], [0, 1]],
};
const INNER = {
  bowl: [[0.15, 0.26], [0.4, 0.4], [1, 0.47]], cup: [[0.15, 0.36], [1, 0.45]],
  pot: [[0.15, 0.46], [1, 0.46]], jar: [[0.15, 0.46], [0.8, 0.46], [0.92, 0.31], [1, 0.31]],
};

export function innerRadius(kind, w, h, y) {
  const table = INNER[kind], f = y / h;
  if (f <= table[0][0]) return table[0][1] * w;
  for (let i = 1; i < table.length; i++) {
    const [y0, r0] = table[i - 1], [y1, r1] = table[i];
    if (f <= y1) return (r0 + ((f - y0) / (y1 - y0)) * (r1 - r0)) * w;
  }
  return table[table.length - 1][1] * w;
}

export function material(THREE, name, colour) {
  const m = MATERIALS[name];
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color(colour || m.colour), roughness: m.roughness, metalness: m.metalness || 0,
    transparent: m.opacity !== undefined, opacity: m.opacity ?? 1,
  });
}

export function buildShape(THREE, spec, dims) {
  const [w, h, d] = dims, group = new THREE.Group(), mat = material(THREE, spec.material, spec.colour);
  let mesh;
  if (spec.shape === "ball") {
    mesh = new THREE.Mesh(new THREE.SphereGeometry(0.5, 40, 24), mat);
    mesh.scale.set(w, h, d); mesh.position.y = h / 2;
  } else if (spec.shape === "box" || spec.shape === "plinth") {
    mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); mesh.position.y = h / 2;
  } else {
    const points = PROFILES[spec.shape].map(([r, y]) => new THREE.Vector2(r * w, y * h));
    mesh = new THREE.Mesh(new THREE.LatheGeometry(points, 48), mat);
    mesh.material.side = THREE.DoubleSide;
    if (spec.shape === "bottle") {
      const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.16 * w, 0.16 * w, 0.06 * h, 24), material(THREE, "metal", "#c0392b"));
      cap.position.y = 0.97 * h; group.add(cap);
    }
  }
  mesh.castShadow = true; mesh.receiveShadow = true; group.add(mesh);
  let fill = null;
  if (spec.shape in INNER) {
    fill = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 0.006, 48), new THREE.MeshStandardMaterial({ color: 0xd9d27a, roughness: 0.15 }));
    // tiny and on the floor until the first frame sizes it, so it never widens the bbox
    fill.scale.set(1e-3, 1, 1e-3); fill.position.y = 0.15 * h; fill.visible = false; group.add(fill);
  }
  return { group, fill, innerRadius: spec.shape in INNER ? (y) => innerRadius(spec.shape, w, h, y) : null };
}
```

- [ ] **Step 5: Write `runtime/look.mjs`**

```js
// The set: sky, fog, ground, scenery, the stage and the lights — all from the look's numbers.
import { mulberry32 } from "./math.mjs";
import { material } from "./shapes.mjs";

export function buildSet(THREE, scene, look, set, anchors) {
  const sky = new THREE.Mesh(
    new THREE.SphereGeometry(60, 32, 16),
    new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false,
      uniforms: { top: { value: new THREE.Color(look.sky.top) }, bottom: { value: new THREE.Color(look.sky.bottom) } },
      vertexShader: "varying float h; void main(){ h = normalize(position).y; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
      fragmentShader: "uniform vec3 top; uniform vec3 bottom; varying float h; void main(){ gl_FragColor = vec4(mix(bottom, top, clamp(h*1.6,0.0,1.0)),1.0); }",
    }),
  );
  scene.add(sky);
  scene.fog = new THREE.Fog(new THREE.Color(look.fog.colour), look.fog.near, look.fog.far);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(80, 80), new THREE.MeshStandardMaterial({ color: new THREE.Color(look.ground.colour), roughness: 1 }));
  ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; scene.add(ground);

  if (set.environment === "meadow-day") {
    const rnd = mulberry32(1234);
    const trunk = new THREE.MeshStandardMaterial({ color: 0x6b4a2b, roughness: 1 });
    const leaves = [new THREE.MeshStandardMaterial({ color: 0x2f6b34, roughness: 1 }), new THREE.MeshStandardMaterial({ color: 0x3f7f3a, roughness: 1 })];
    for (let i = 0; i < 46; i++) {
      const a = rnd() * Math.PI * 2, r = 7 + rnd() * 12, s = 0.8 + rnd() * 0.9;
      const tree = new THREE.Group();
      const t = new THREE.Mesh(new THREE.CylinderGeometry(0.08 * s, 0.12 * s, 0.8 * s, 8), trunk); t.position.y = 0.4 * s;
      const c = new THREE.Mesh(new THREE.ConeGeometry(0.7 * s, 2.6 * s, 10), leaves[i % 2]); c.position.y = 0.8 * s + 1.3 * s;
      tree.add(t, c); tree.position.set(Math.sin(a) * r, 0, Math.cos(a) * r - 4); tree.traverse((o) => (o.castShadow = true));
      scene.add(tree);
    }
  } else {
    const backdrop = new THREE.Mesh(new THREE.CylinderGeometry(14, 14, 20, 48, 1, true, -Math.PI / 2, Math.PI), new THREE.MeshStandardMaterial({ color: new THREE.Color(look.sky.bottom), side: THREE.BackSide, roughness: 1 }));
    backdrop.position.set(0, 10, 0); scene.add(backdrop);
  }

  const top = anchors["stage.center"][1];
  if (set.stage === "stone-slab") {
    const slab = new THREE.Mesh(new THREE.BoxGeometry(1.6, top, 1.0), material(THREE, "stone", "#a39b8f"));
    slab.position.y = top / 2; slab.castShadow = true; slab.receiveShadow = true; scene.add(slab);
  } else if (set.stage === "table") {
    const wood = material(THREE, "wood", null);
    const board = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.06, 0.9), wood); board.position.y = top - 0.03;
    board.receiveShadow = true; board.castShadow = true; scene.add(board);
    for (const [x, z] of [[-0.62, -0.38], [0.62, -0.38], [-0.62, 0.38], [0.62, 0.38]]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.06, top - 0.06, 0.06), wood); leg.position.set(x, (top - 0.06) / 2, z); scene.add(leg);
    }
  }

  const L = look.lights;
  scene.add(new THREE.HemisphereLight(new THREE.Color(L.hemi.sky), new THREE.Color(L.hemi.ground), L.hemi.intensity));
  const key = new THREE.DirectionalLight(new THREE.Color(L.key.colour), L.key.intensity);
  key.position.set(...L.key.position); key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -4, right: 4, top: 4, bottom: -4, near: 0.5, far: 30 });
  key.shadow.bias = -0.0005; scene.add(key);
  const fill = new THREE.DirectionalLight(new THREE.Color(L.fill.colour), L.fill.intensity);
  fill.position.set(...L.fill.position); scene.add(fill);
}
```

- [ ] **Step 6: Write `runtime/main.mjs`**

```js
// The shot. The only module that imports "three"; everything it draws is a function of t.
import * as THREE from "three";
import { stateAt } from "./poses.mjs";
import { cameraPose, ease } from "./camera.mjs";
import { buildShape, material, SURFACES } from "./shapes.mjs";
import { buildSet } from "./look.mjs";
import { grains, steam, stream } from "./particles.mjs";
import { captionState } from "./captions.mjs";

window.THREE = THREE; // the HyperFrames three adapter waits on THREE.DefaultLoadingManager
const shot = JSON.parse(document.getElementById("shot-data").textContent);
const W = shot.width, H = shot.height;

const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById("stage3d"), antialias: true, preserveDrawingBuffer: true });
renderer.setSize(W, H, false); renderer.setPixelRatio(1);
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = shot.look.exposure;
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
buildSet(THREE, scene, shot.look, shot.set, shot.ctx.anchors);
const camera = new THREE.PerspectiveCamera(shot.camera.start.fov, W / H, 0.05, 120);

const actors = {};
for (const [id, actor] of Object.entries(shot.actors)) {
  const built = buildShape(THREE, actor.shape, actor.dims);
  scene.add(built.group); actors[id] = built;
}
const pestle = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.04, 0.35, 16), material(THREE, "stone", "#7d756a"));
pestle.geometry.translate(0, 0.175, 0); pestle.castShadow = true; pestle.visible = false; scene.add(pestle);
const drops = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 8, 6), new THREE.MeshStandardMaterial({ roughness: 0.3 }), 400);
drops.count = 0; scene.add(drops);
const puffs = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 10, 8), new THREE.MeshStandardMaterial({ color: 0xffffff, transparent: true, opacity: 0.35, roughness: 1, depthWrite: false }), 200);
puffs.count = 0; scene.add(puffs);
const m4 = new THREE.Matrix4(), tint = new THREE.Color();

// overlay: title banner, step label, captions
const overlay = { title: document.getElementById("title"), label: document.getElementById("label"), captions: document.getElementById("captions") };
const L = shot.look;
if (shot.captions.title) {
  Object.assign(overlay.title.style, { background: L.banner.background, color: L.banner.text, border: `4px solid ${L.banner.border}`, display: "block" });
  overlay.title.textContent = shot.captions.title;
}
if (shot.captions.label) {
  Object.assign(overlay.label.style, { background: L.label.background, color: L.label.text, display: "block" });
  overlay.label.textContent = shot.captions.label;
}
overlay.captions.style.fontSize = `${L.caption.size}px`;
overlay.captions.style.textShadow = `0 4px 12px ${L.caption.shadow}, 0 0 3px ${L.caption.shadow}`;
const spans = shot.captions.words.map((w) => {
  const s = document.createElement("span"); s.textContent = w.text; overlay.captions.appendChild(s); return s;
});

function renderAt(t) {
  const { world, fx } = stateAt(shot, t);
  for (const [id, built] of Object.entries(actors)) {
    const a = world[id];
    built.group.visible = a.visible && a.scale > 0;
    built.group.position.set(...a.position);
    built.group.rotation.set(0, a.rotationY, 0);
    built.group.scale.setScalar(Math.max(a.scale, 1e-4));
    if (built.fill) {
      const f = a.fill, y = shot.actors[id].dims[1] * (0.15 + 0.85 * 0.9 * f.level);
      built.fill.visible = f.level > 0;
      const r = built.innerRadius(y);
      built.fill.position.y = y; built.fill.scale.set(r, 1, r);
      built.fill.material.color.set(f.colour); built.fill.material.roughness = SURFACES[f.surface];
    }
  }
  pestle.visible = false; drops.count = 0; puffs.count = 0;
  for (const e of fx) {
    if (e.type === "tilt") actors[e.actor].group.rotation.z = e.angle;
    else if (e.type === "pestle") { pestle.visible = true; pestle.position.set(...e.position); }
    else if (e.type === "stream" || e.type === "grains") {
      const pts = e.type === "stream" ? stream(e.seed, e.from, e.to, e.t) : grains(e.seed, e.origin, e.floorY, e.t);
      tint.set(e.colour);
      for (const [x, y, z, s] of pts) {
        if (drops.count >= 400) break;
        m4.makeScale(s, s, s).setPosition(x, y, z); drops.setMatrixAt(drops.count, m4); drops.setColorAt(drops.count, tint); drops.count++;
      }
    } else if (e.type === "steam") {
      for (const [x, y, z, s] of steam(e.seed, e.origin, e.t, e.intensity)) {
        if (puffs.count >= 200) break;
        m4.makeScale(s, s, s).setPosition(x, y, z); puffs.setMatrixAt(puffs.count++, m4);
      }
    }
  }
  drops.instanceMatrix.needsUpdate = true; if (drops.instanceColor) drops.instanceColor.needsUpdate = true;
  puffs.instanceMatrix.needsUpdate = true;

  const c = shot.camera, p = ease(t / shot.duration, c.easeIn, c.easeOut);
  const pose = cameraPose(c.move, c.params, c.start, c.target, p);
  camera.position.set(...pose.position); camera.fov = pose.fov; camera.updateProjectionMatrix(); camera.lookAt(...pose.target);

  const { active, speaking } = captionState(shot.captions.words, t);
  overlay.captions.style.opacity = active >= 0 || t < 0.4 ? "1" : "0";
  spans.forEach((s, i) => (s.style.color = i === active && speaking ? L.caption.active : L.caption.text));

  renderer.render(scene, camera);
}

window.addEventListener("hf-seek", (event) => renderAt(event.detail.time));
renderAt(window.__hfThreeTime || 0);
```

- [ ] **Step 7: Write the looks**

`looks/diorama-day.json`:

```json
{
  "id": "diorama-day",
  "sky": {"top": "#4f93dc", "bottom": "#cfe6f5"},
  "fog": {"colour": "#cfe6f5", "near": 9, "far": 32},
  "ground": {"colour": "#b9a35a"},
  "lights": {
    "hemi": {"sky": "#ffffff", "ground": "#5b6b3a", "intensity": 1.1},
    "key": {"colour": "#fff3df", "intensity": 2.6, "position": [3, 5, 2.5]},
    "fill": {"colour": "#cfe0ff", "intensity": 0.5, "position": [-3, 2, -2]}
  },
  "exposure": 1.0,
  "banner": {"background": "#e0121b", "text": "#ffffff", "border": "#ffffff"},
  "label": {"background": "rgba(0,0,0,0.55)", "text": "#ffffff"},
  "caption": {"text": "#ffffff", "active": "#ffd400", "shadow": "rgba(0,0,0,0.85)", "size": 64}
}
```

`looks/studio-clean.json`:

```json
{
  "id": "studio-clean",
  "sky": {"top": "#dfe6ee", "bottom": "#f7f8fa"},
  "fog": {"colour": "#f7f8fa", "near": 12, "far": 40},
  "ground": {"colour": "#e3e7ec"},
  "lights": {
    "hemi": {"sky": "#ffffff", "ground": "#9aa3ad", "intensity": 1.3},
    "key": {"colour": "#ffffff", "intensity": 2.2, "position": [2.5, 5, 3]},
    "fill": {"colour": "#e8f0ff", "intensity": 0.7, "position": [-3, 2.5, 1]}
  },
  "exposure": 1.0,
  "banner": {"background": "#1c2b4a", "text": "#ffffff", "border": "#1c2b4a"},
  "label": {"background": "#1c2b4a", "text": "#ffffff"},
  "caption": {"text": "#16202f", "active": "#d94f30", "shadow": "rgba(255,255,255,0.9)", "size": 62}
}
```

- [ ] **Step 8: Write `compile.py`**

```python
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
    for key in inputs.cast:
        shape = inputs.cast[key].asset
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
#captions span{margin:0 0.12em}
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
```

`compile.py` imports `HYPERFRAMES_VERSION` from Task 8's module; create that module's constant now so this task stands alone:

```python
# reelmachine/render/hyperframes.py (Task 8 fills in the renderer)
"""The HyperFrames renderer: one HTML project in, one MP4 (or one still) out."""

HYPERFRAMES_VERSION = "0.8.146"
```

- [ ] **Step 9: Run tests**

Run: `node --test reelmachine/recipes/explainer3d/runtime/tests && uv run pytest tests/test_explainer3d.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add reelmachine/recipes/explainer3d reelmachine/render/hyperframes.py pyproject.toml THIRD_PARTY_NOTICES.md tests/test_explainer3d.py
git commit -m "explainer3d: shapes, sets, looks and the shot compiler; three.js vendored"
```

---

### Task 8: The HyperFrames renderer and a fake for tests

**Files:**
- Modify: `reelmachine/render/hyperframes.py`
- Create: `reelmachine/recipes/explainer3d/fake.py`
- Modify: `pyproject.toml` (entry points)
- Test: `tests/test_hyperframes_renderer.py`

**Interfaces:**
- Produces: `HyperframesRenderer(settings)` with `name="hyperframes"`, `missing() -> list[str]`, `describe() -> dict`, `lint(project, *, cancel=None) -> tuple[bool, str]`, `snapshot(project, at_s, dest, *, cancel=None) -> Path`, `render(project, dest, *, fps=30, cancel=None) -> Path`. `FakeShotRenderer(settings)` with `name="explainer3d-fake"` and the same `missing/snapshot/render` (draws a moving test pattern with ffmpeg; reads the duration from `index.html`).
- Entry points: `reelmachine.renderers`: `hyperframes`, `explainer3d-fake`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hyperframes_renderer.py
"""The HyperFrames renderer, driven through a fake `npx` so the logic is tested without Node."""

from __future__ import annotations

import dataclasses
import os
import stat
from pathlib import Path

import pytest

from reelmachine.config import get_settings
from reelmachine.core.errors import ShotRenderFailed
from reelmachine.core.stage import CancellationToken
from reelmachine.render.hyperframes import HyperframesRenderer

FAKE_NPX = """#!/bin/sh
# argv: --yes hyperframes@V <command> ...
echo "$@" >> "$HF_LOG"
cmd="$3"; shift 3
case "$cmd" in
  lint) if [ -n "$HF_LINT_FAIL" ]; then echo "✗ missing_timeline_registry"; echo "◇  1 error(s), 0 warning(s)"; exit 1; fi; echo "◇  0 errors, 0 warnings";;
  render) while [ "$1" != "--output" ]; do shift; done; [ -n "$HF_SLEEP" ] && sleep "$HF_SLEEP"; printf 'mp4' > "$2";;
  snapshot) while [ "$1" != "--output" ]; do shift; done; mkdir -p "$2"; printf 'png' > "$2/frame-00-at-1s.png";;
esac
"""


@pytest.fixture
def fake_npx(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text(FAKE_NPX)
    npx.chmod(npx.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "npx.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HF_LOG", str(log))
    return log


def _renderer(tmp_path):
    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "work")
    return HyperframesRenderer(settings)


def test_render_lints_then_renders_pinned_with_telemetry_off(tmp_path, fake_npx, monkeypatch):
    project = tmp_path / "shot"
    project.mkdir()
    out = _renderer(tmp_path).render(project, tmp_path / "out.mp4")
    assert out.read_text() == "mp4"
    calls = fake_npx.read_text().splitlines()
    assert calls[0].startswith("--yes hyperframes@0.8.146 lint")
    assert "render" in calls[1] and "--fps 30" in calls[1]


def test_lint_failure_is_a_shot_render_failure(tmp_path, fake_npx, monkeypatch):
    monkeypatch.setenv("HF_LINT_FAIL", "1")
    project = tmp_path / "shot"
    project.mkdir()
    with pytest.raises(ShotRenderFailed) as info:
        _renderer(tmp_path).render(project, tmp_path / "out.mp4")
    assert "missing_timeline_registry" in info.value.details["lint"]


def test_snapshot_returns_the_frame(tmp_path, fake_npx):
    project = tmp_path / "shot"
    project.mkdir()
    still = _renderer(tmp_path).snapshot(project, 1.0, tmp_path / "still.png")
    assert still.read_text() == "png"
    assert "--at 1.000 --no-end" in fake_npx.read_text()


def test_timeout_kills_the_process_and_registers_it(tmp_path, fake_npx, monkeypatch):
    monkeypatch.setenv("HF_SLEEP", "30")
    project = tmp_path / "shot"
    project.mkdir()
    token = CancellationToken()
    renderer = _renderer(tmp_path)
    renderer.timeout_floor_s = 1
    with pytest.raises(ShotRenderFailed) as info:
        renderer.render(project, tmp_path / "out.mp4", seconds=0.01, cancel=token)
    assert "timed out" in info.value.message
    assert not token._processes  # unregistered after the kill


def test_missing_reports_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # nothing on PATH
    settings = dataclasses.replace(get_settings(), ffmpeg="ffmpeg", ffprobe="ffprobe")  # ignore REEL_FFPROBE
    problems = HyperframesRenderer(settings).missing()
    assert any("node" in p for p in problems) and any("ffprobe" in p for p in problems)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_hyperframes_renderer.py -q` → FAIL (`ImportError: HyperframesRenderer`).

- [ ] **Step 3: Implement the renderer**

Replace `reelmachine/render/hyperframes.py` with:

```python
"""The HyperFrames renderer: one HTML project in, one MP4 (or one still) out.

HyperFrames seeks every frame of a composition in headless Chrome and encodes with
FFmpeg (https://github.com/heygen-com/hyperframes, Apache-2.0). It runs through `npx`
at a pinned version, with telemetry off and our own ffmpeg/ffprobe, in its own process
group so a cancelled job takes Chrome down with it.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..core.errors import ShotRenderFailed

HYPERFRAMES_VERSION = "0.8.146"
_ERRORS = re.compile(r"(\d+)\s+errors?")


def _node_major(node: str) -> int:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except Exception:  # noqa: BLE001 - an unreadable version is "too old"
        return 0


class HyperframesRenderer:
    name = "hyperframes"
    timeout_floor_s = 120

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ----------------------------------------------------------------- readiness
    def missing(self) -> list[str]:
        problems: list[str] = []
        node = shutil.which("node")
        if node is None:
            problems.append("node is not on PATH (HyperFrames needs Node.js >= 22)")
        elif _node_major(node) < 22:
            problems.append("Node.js >= 22 is required by HyperFrames")
        if shutil.which("npx") is None:
            problems.append("npx is not on PATH (it ships with Node.js)")
        if shutil.which(self.settings.ffmpeg) is None:
            problems.append(f"ffmpeg not found ({self.settings.ffmpeg!r})")
        if shutil.which(self.settings.ffprobe) is None:
            problems.append(
                f"ffprobe not found ({self.settings.ffprobe!r}) — HyperFrames will not run without it; "
                "install ffmpeg-full or point REEL_FFPROBE at an ffprobe binary"
            )
        return problems

    def describe(self) -> dict[str, Any]:
        problems = self.missing()
        return {
            "name": self.name,
            "group": "renderers",
            "description": f"Three.js shots rendered by HyperFrames {HYPERFRAMES_VERSION} (Node + headless Chrome)",
            "missing": problems,
        }

    # ------------------------------------------------------------------ running
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["DO_NOT_TRACK"] = "1"
        env["HYPERFRAMES_FFMPEG_PATH"] = shutil.which(self.settings.ffmpeg) or self.settings.ffmpeg
        env["HYPERFRAMES_FFPROBE_PATH"] = shutil.which(self.settings.ffprobe) or self.settings.ffprobe
        return env

    def _run(self, args: list[str], project: Path, *, timeout_s: float, cancel: Any = None) -> tuple[int, str]:
        npx = shutil.which("npx") or "npx"
        log = tempfile.TemporaryFile(mode="w+")
        proc = subprocess.Popen(
            [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", *args],
            cwd=project, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=self._env(), start_new_session=True, text=True,
        )
        if cancel is not None:
            cancel.register_process(proc)
        try:
            try:
                code = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                log.seek(0)
                raise ShotRenderFailed(
                    f"HyperFrames timed out after {timeout_s:.0f}s",
                    hint="the shot may be too heavy, or Chrome hung; re-run the job",
                    details={"project": str(project), "log": log.read()[-2000:]},
                ) from None
        finally:
            if cancel is not None:
                cancel.unregister_process(proc)
        if cancel is not None:
            cancel.raise_if_cancelled()
        log.seek(0)
        return code, log.read()

    def lint(self, project: Path, *, cancel: Any = None) -> tuple[bool, str]:
        code, out = self._run(["lint"], Path(project), timeout_s=120, cancel=cancel)
        match = _ERRORS.search(out)
        errors = int(match.group(1)) if match else (0 if code == 0 else 1)
        return errors == 0, out

    def snapshot(self, project: Path, at_s: float, dest: Path, *, cancel: Any = None) -> Path:
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(
                ["snapshot", "--at", f"{at_s:.3f}", "--no-end", "--output", tmp],
                Path(project), timeout_s=max(60, self.timeout_floor_s), cancel=cancel,
            )
            frames = sorted(Path(tmp).glob("frame-*.png"))
            if code != 0 or not frames:
                raise ShotRenderFailed(
                    "HyperFrames could not capture a still",
                    details={"project": str(project), "log": out[-2000:]},
                )
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(frames[0], dest)
        return dest

    def render(
        self, project: Path, dest: Path, *, fps: int = 30, seconds: float = 0.0, cancel: Any = None
    ) -> Path:
        project, dest = Path(project), Path(dest)
        ok, lint_out = self.lint(project, cancel=cancel)
        if not ok:
            raise ShotRenderFailed(
                "the shot did not pass HyperFrames lint",
                hint="this is a compiler bug in explainer3d; the lint output names the rule",
                details={"project": str(project), "lint": lint_out[-2000:]},
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        timeout_s = max(self.timeout_floor_s, 20 * seconds)
        code, out = self._run(
            ["render", "--output", str(dest), "--fps", str(fps), "--quality", "looks", "--strict"],
            project, timeout_s=timeout_s, cancel=cancel,
        )
        (project / "render.log").write_text(out, encoding="utf-8")
        if code != 0 or not dest.is_file() or dest.stat().st_size == 0:
            raise ShotRenderFailed(
                "HyperFrames did not produce the shot",
                details={"project": str(project), "log": out[-2000:]},
            )
        return dest
```

- [ ] **Step 4: Implement the fake renderer**

`reelmachine/recipes/explainer3d/fake.py`:

```python
"""A stand-in renderer for tests: a moving pattern of the right size and length.

It honours the same contract as HyperFrames (project dir in, MP4 or PNG out, duration
from the root's data-duration) so the engine, compose and verify paths run without Node.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings, get_settings

_DURATION = re.compile(r'data-duration="([\d.]+)"')


def _duration(project: Path) -> float:
    return float(_DURATION.search((Path(project) / "index.html").read_text(encoding="utf-8")).group(1))


class FakeShotRenderer:
    name = "explainer3d-fake"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "group": "renderers", "description": "test pattern shots", "missing": []}

    def render(self, project: Path, dest: Path, *, fps: int = 30, seconds: float = 0.0, cancel: Any = None) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.run([
            self.settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate={fps}:duration={_duration(project):.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dest),
        ], cancel=cancel)
        return dest

    def snapshot(self, project: Path, at_s: float, dest: Path, *, cancel: Any = None) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.run([
            self.settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=1:duration=1",
            "-frames:v", "1", str(dest),
        ], cancel=cancel)
        return dest
```

In `pyproject.toml` `[project.entry-points."reelmachine.renderers"]` add:

```toml
hyperframes = "reelmachine.render.hyperframes:HyperframesRenderer"
explainer3d-fake = "reelmachine.recipes.explainer3d.fake:FakeShotRenderer"
```

Then `uv sync` so the entry points are registered.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_hyperframes_renderer.py tests/test_registry_providers.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add reelmachine/render/hyperframes.py reelmachine/recipes/explainer3d/fake.py pyproject.toml uv.lock tests/test_hyperframes_renderer.py
git commit -m "The hyperframes renderer: pinned, telemetry off, its own process group, lint first"
```

---

### Task 9: Audio and video assembly

**Files:**
- Create: `reelmachine/recipes/explainer3d/audio.py`, `reelmachine/recipes/explainer3d/video.py`
- Test: `tests/test_explainer3d_engine.py` (slow; ffmpeg)

**Interfaces:**
- Produces: `audio.SFX_NAMES`, `audio.synth_sfx(name, dest, *, settings) -> Path`, `audio.pad_speech(src, dest, *, lead_ms, total_ms, settings, cancel=None) -> Path`, `audio.mix(narration, cues, music, dest, *, total_ms, settings, profile, cancel=None) -> Path` (`cues` = list of `(at_ms, sfx_path)`); `video.join_shots(tracks, joins, nominal_ms, dest, *, settings, fps=30, cancel=None) -> Path` (`joins[0]` is ignored; `joins[i]` ∈ `{"xfade","cut"}`; track *i* must be `nominal_ms[i] + (XFADE_MS if joins[i+1]=="xfade" else 0)` long); `video.mux(video, audio, dest, *, total_ms, settings, cancel=None) -> Path`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_explainer3d_engine.py
"""explainer3d with ffmpeg: audio/video assembly and full engine runs on the fake renderer."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.recipes.explainer3d.audio import SFX_NAMES, mix, pad_speech, synth_sfx
from reelmachine.recipes.explainer3d.timing import XFADE_MS
from reelmachine.recipes.explainer3d.video import join_shots

pytestmark = pytest.mark.slow


def _settings(tmp_path):
    return dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out", tts="fake")


def _clip(settings, path: Path, seconds: float) -> Path:
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=1080x1920:rate=30:duration={seconds:.3f}", "-pix_fmt", "yuv420p", str(path)])
    return path


def test_every_sfx_synthesises(tmp_path):
    settings = _settings(tmp_path)
    for name in SFX_NAMES:
        info = ffmpeg.probe(synth_sfx(name, tmp_path / f"{name}.wav", settings=settings))
        assert info.has_audio and 0.05 < info.duration_s < 1.5


def test_pad_speech_is_exactly_the_window(tmp_path):
    settings = _settings(tmp_path)
    src = synth_sfx("ding", tmp_path / "speech.wav", settings=settings)
    out = pad_speech(src, tmp_path / "padded.wav", lead_ms=250, total_ms=2000, settings=settings)
    assert ffmpeg.probe(out).duration_s == pytest.approx(2.0, abs=0.03)


def test_mix_is_the_reel_length_with_and_without_music(tmp_path):
    settings = _settings(tmp_path)
    narration = pad_speech(synth_sfx("ding", tmp_path / "s.wav", settings=settings), tmp_path / "n.wav",
                           lead_ms=0, total_ms=3000, settings=settings)
    cue = synth_sfx("whoosh", tmp_path / "w.wav", settings=settings)
    for music in (None, synth_sfx("pour", tmp_path / "m.wav", settings=settings)):
        out = mix(narration, [(500, cue)], music, tmp_path / "mix.wav", total_ms=3000, settings=settings, profile="studio")
        assert ffmpeg.probe(out).duration_s == pytest.approx(3.0, abs=0.05)


def test_join_crossfades_and_cuts_to_the_nominal_length(tmp_path):
    settings = _settings(tmp_path)
    nominal = [1500, 1500, 1500]
    joins = ["none", "xfade", "cut"]
    tracks = [
        _clip(settings, tmp_path / "a.mp4", (1500 + XFADE_MS) / 1000),  # followed by a crossfade
        _clip(settings, tmp_path / "b.mp4", 1.5),                         # followed by a cut
        _clip(settings, tmp_path / "c.mp4", 1.5),
    ]
    out = join_shots(tracks, joins, nominal, tmp_path / "joined.mp4", settings=settings)
    info = ffmpeg.probe(out)
    assert info.duration_s == pytest.approx(4.5, abs=0.05) and (info.width, info.height) == (1080, 1920)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_explainer3d_engine.py -q` → FAIL (`ModuleNotFoundError: ...audio`).

- [ ] **Step 3: Implement `audio.py`**

```python
"""Sound for a reel: synthetic SFX (no licences to track), padded speech, and the mix.

SFX are made by ffmpeg from seeded noise and tones, so they are identical on every run.
Music, when given, sits under the voice and ducks while someone speaks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ... import ffmpeg
from ...config import Settings
from ...render.audio import audio_filter

_SFX = {
    "whoosh": "anoisesrc=d=0.45:c=pink:a=0.5:s=7,highpass=f=400,lowpass=f=3000,afade=t=in:d=0.2,afade=t=out:st=0.2:d=0.25",
    "thud": "sine=f=70:d=0.35,afade=t=out:st=0.02:d=0.33,volume=1.5",
    "pop": "sine=f=880:d=0.08,afade=t=out:st=0:d=0.08",
    "pour": "anoisesrc=d=1.2:c=brown:a=0.4:s=11,lowpass=f=1200,afade=t=in:d=0.2,afade=t=out:st=0.9:d=0.3",
    "sprinkle": "anoisesrc=d=0.8:c=white:a=0.15:s=13,highpass=f=4000,afade=t=in:d=0.1,afade=t=out:st=0.5:d=0.3",
    "sizzle": "anoisesrc=d=1.0:c=white:a=0.12:s=17,bandpass=f=6000,afade=t=in:d=0.2,afade=t=out:st=0.7:d=0.3",
    "ding": "sine=f=1320:d=0.6,afade=t=out:st=0.05:d=0.55",
}
SFX_NAMES = tuple(_SFX)
_RATE = ["-ar", "48000", "-ac", "2"]


def synth_sfx(name: str, dest: Path, *, settings: Settings) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", _SFX[name],
                *_RATE, str(dest)])
    return dest


def pad_speech(src: Path, dest: Path, *, lead_ms: int, total_ms: int, settings: Settings, cancel: Any = None) -> Path:
    """Place a line `lead_ms` into a window of exactly `total_ms`."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([
        settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src),
        "-af", f"aresample=48000,adelay={lead_ms}:all=1,apad=whole_dur={total_ms / 1000:.3f},atrim=0:{total_ms / 1000:.3f}",
        *_RATE, str(dest),
    ], cancel=cancel)
    return dest


def mix(
    narration: Path,
    cues: Sequence[tuple[int, Path]],
    music: Path | None,
    dest: Path,
    *,
    total_ms: int,
    settings: Settings,
    profile: str,
    cancel: Any = None,
) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = f"{total_ms / 1000:.3f}"
    cmd = [settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(narration)]
    for _, path in cues:
        cmd += ["-i", str(path)]
    if music is not None:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    lines = ["[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voice][key]"]
    parts = ["[voice]"]
    for index, (at_ms, _) in enumerate(cues, start=1):
        lines.append(f"[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={at_ms}:all=1,volume=0.55[s{index}]")
        parts.append(f"[s{index}]")
    if music is not None:
        m = len(cues) + 1
        lines.append(f"[{m}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total},volume=0.35[bed]")
        lines.append("[bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
        parts.append("[duck]")
    else:
        lines.append("[key]anullsink")
    lines.append(f"{''.join(parts)}amix=inputs={len(parts)}:normalize=0:duration=first[mixed]")
    profile_filter = audio_filter(profile)
    lines.append(f"[mixed]{profile_filter + ',' if profile_filter else ''}apad=whole_dur={total},atrim=0:{total}[out]")
    cmd += ["-filter_complex", ";".join(lines), "-map", "[out]", *_RATE, str(dest)]
    ffmpeg.run(cmd, cancel=cancel)
    return dest
```

- [ ] **Step 4: Implement `video.py`**

```python
"""Join rendered shots — a crossfade where a new camera starts, a hard cut where the
camera continues — and mux the final audio."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ... import ffmpeg
from ...config import Settings
from .timing import XFADE_MS


def join_shots(
    tracks: Sequence[Path],
    joins: Sequence[str],
    nominal_ms: Sequence[int],
    dest: Path,
    *,
    settings: Settings,
    fps: int = 30,
    cancel: Any = None,
) -> Path:
    if not tracks:
        raise ValueError("no shots to join")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [settings.ffmpeg, "-hide_banner", "-nostdin", "-y"]
    for track in tracks:
        cmd += ["-i", str(track)]
    lines = [
        f"[{i}:v]fps={fps},scale=1080:1920,setsar=1,format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v{i}]"
        for i in range(len(tracks))
    ]
    current, elapsed = "[v0]", nominal_ms[0]
    for i in range(1, len(tracks)):
        label = f"[j{i}]"
        if joins[i] == "xfade":
            lines.append(f"{current}[v{i}]xfade=transition=fade:duration={XFADE_MS / 1000:.3f}:offset={elapsed / 1000:.3f}{label}")
        else:
            lines.append(f"{current}[v{i}]concat=n=2:v=1:a=0{label}")
        current, elapsed = label, elapsed + nominal_ms[i]
    lines.append(f"{current}trim=duration={elapsed / 1000:.3f}[out]")
    cmd += ["-filter_complex", ";".join(lines), "-map", "[out]",
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", "-r", str(fps), str(dest)]
    ffmpeg.run(cmd, cancel=cancel)
    return dest


def mux(video: Path, audio: Path, dest: Path, *, total_ms: int, settings: Settings, cancel: Any = None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([
        settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(video), "-i", str(audio),
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{total_ms / 1000:.3f}", "-movflags", "+faststart", str(dest),
    ], cancel=cancel)
    return dest
```

- [ ] **Step 5: Run tests** — `uv run pytest tests/test_explainer3d_engine.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add reelmachine/recipes/explainer3d/audio.py reelmachine/recipes/explainer3d/video.py tests/test_explainer3d_engine.py
git commit -m "explainer3d: synthetic SFX, padded speech, a ducked music bed, and shot joins"
```

---

### Task 10: Stages, recipe and registration

**Files:**
- Create: `reelmachine/recipes/explainer3d/stages.py`, `reelmachine/recipes/explainer3d/recipe.py`
- Modify: `reelmachine/recipes/explainer3d/models.py` (stage payloads), `pyproject.toml` (recipe entry point)
- Test: `tests/test_explainer3d_engine.py` (append)

**Interfaces:**
- Consumes: everything above; narration providers via role `narration` (`synthesize(text, voice=, language=) -> TimedSpeech`); renderer via role `renderer` (`render`, `snapshot`, `missing`, `name`).
- Produces: stage payload models (`ScriptOutput`, `AssetsOutput`, `NarrationOutput`, `ShotsOutput`, `PrepOutput`, `ComposeOutput`, `RenderOutput`) and `Explainer3DRecipe` registered as `explainer3d`. Artifacts: `storyboard.jpg` and `script.resolved.json` (storyboard mode); `reel.mp4`, `captions.srt`, `reel.json`, `timeline.json`, `script.resolved.json` (build).

- [ ] **Step 1: Write the failing engine tests**

```python
# append to tests/test_explainer3d_engine.py
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine

SCRIPT = {
    "title": "The Secret Bait",
    "set": {"environment": "meadow-day", "stage": "stone-slab"},
    "cast": {
        "bowl": {"asset": {"shape": "bowl", "height": 0.2, "material": "stone"}, "at": "stage.center"},
        "potato": {"asset": {"shape": "ball", "height": 0.1, "colour": "#d9b44a"}},
    },
    "shots": [
        {"narration": "First, a potato goes in.", "label": "Step 1",
         "camera": {"move": "orbit", "target": "bowl", "from": 10, "to": 40},
         "actions": [{"move": "fly_in", "actor": "potato"}, {"move": "drop_into", "actor": "potato", "into": "bowl"}]},
        {"narration": "Then we mash it.", "camera": {"move": "push_in", "target": "bowl", "continue": True},
         "actions": [{"move": "mash", "actor": "bowl", "colour": "#d9d27a"}]},
    ],
}


def _run(tmp_path, inputs):
    engine = Engine(_settings(tmp_path), provider_overrides={"renderer": "explainer3d-fake"})
    try:
        job = engine.submit(JobRequest(recipe="explainer3d", inputs=inputs), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=600)
    finally:
        engine.close()


def test_storyboard_stops_after_prep_with_a_contact_sheet(tmp_path):
    job = _run(tmp_path, {**SCRIPT, "mode": "storyboard"})
    assert job.state == JobState.SUCCEEDED, job.error
    names = {a.name for a in job.result.artifacts}
    assert {"storyboard.jpg", "script.resolved.json"} <= names and "reel.mp4" not in names


def test_build_makes_a_reel_whose_length_is_the_narration(tmp_path):
    job = _run(tmp_path, SCRIPT)
    assert job.state == JobState.SUCCEEDED, job.error
    reel = next(a for a in job.result.artifacts if a.name == "reel.mp4")
    info = ffmpeg.probe(reel.path)
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    timeline = next(a for a in job.result.artifacts if a.name == "timeline.json")
    import json
    assert abs(info.duration_s * 1000 - json.loads(Path(timeline.path).read_text())["duration_ms"]) < 100
    assert "captions.ass" not in {a.name for a in job.result.artifacts}


def test_unchanged_shots_are_not_rendered_twice(tmp_path):
    _run(tmp_path, {**SCRIPT, "mode": "storyboard"})
    first = _run(tmp_path, SCRIPT)
    second = _run(tmp_path, SCRIPT)
    assert second.state == JobState.SUCCEEDED
    cache = _settings(tmp_path).workdir / "cache" / "explainer3d" / "shots"
    assert len(list(cache.rglob("*.mp4"))) == 2  # two shots, rendered once each


def test_a_bad_script_fails_with_the_field(tmp_path):
    bad = {**SCRIPT, "shots": [{"narration": "x", "actions": [{"move": "teleport", "actor": "bowl"}]}]}
    job = _run(tmp_path, bad)
    assert job.state == JobState.FAILED
    assert job.error.code == "INVALID_INPUT" and job.error.details["path"] == "shots[0].actions[0].move"
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_explainer3d_engine.py -q -k "storyboard or build or rendered or bad"` → FAIL (`recipe 'explainer3d' not found`).

- [ ] **Step 3: Append stage payloads to `models.py`**

```python
from ...core.assets import Licence, Provenance
from ...core.stage import StageOutput
from ...core.timeline import Timeline, WordSpan


class ScriptOutput(StageOutput):
    inputs: Explainer3DInputs
    shots: list[ResolvedShot]


class ResolvedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["shape"] = "shape"
    shape: ShapeSpec
    dims: tuple[float, float, float]
    licence: Licence
    provenance: Provenance


class AssetsOutput(StageOutput):
    inputs: Explainer3DInputs
    shots: list[ResolvedShot]
    models: dict[str, ResolvedModel] = Field(default_factory=dict)


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int
    duration_ms: int
    speech_offset_ms: int
    speech_ms: int


class NarrationOutput(StageOutput):
    inputs: Explainer3DInputs
    shots: list[ResolvedShot]
    models: dict[str, ResolvedModel] = Field(default_factory=dict)
    audio_asset: str
    duration_ms: int
    windows: list[Window]
    words: list[list[WordSpan]]  # absolute ms, one list per shot
    voice: str
    provider: str
    licence: Licence | None = None


class ShotProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    index: int
    dir: str
    key: str
    nominal_ms: int
    render_ms: int
    join: Literal["none", "xfade", "cut"]


class ShotsOutput(StageOutput):
    narration: NarrationOutput
    projects: list[ShotProject]
    cues: list[dict[str, Any]] = Field(default_factory=list)


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    path: str
    render_ms: int
    renderer: str


class PrepOutput(StageOutput):
    shots: ShotsOutput
    tracks: list[Track] = Field(default_factory=list)
    storyboard: str = ""
    renderer: str = ""


class ComposeOutput(StageOutput):
    prep: PrepOutput
    timeline: Timeline
    render_options: dict[str, Any] = Field(default_factory=dict)


class RenderOutput(StageOutput):
    video: str
    ass: str = ""
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = "hyperframes"
```

(Move the three `from ...core…` imports to the top of `models.py` with the others.)

- [ ] **Step 4: Implement `stages.py`**

```python
"""explainer3d stages: script → assets → narration → shots → prep → compose → render."""

from __future__ import annotations

import json
import math
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Licence, Provenance
from ...core.errors import InvalidInput, RendererUnavailable, VoiceUnavailable
from ...core.stage import StageContext
from ...core.timeline import Caption, Clip, Timeline, TimedAudioRef, WordSpan
from ...core.timing import proportional_word_spans
from ...render.captions import write_srt
from ..doodle.narration import provider_for
from .audio import mix, pad_speech, synth_sfx
from .compile import FPS, project_key, shot_document, write_project
from .geometry import dims
from .models import (
    AssetsOutput, ComposeOutput, Explainer3DInputs, NarrationOutput, PrepOutput, RenderOutput,
    ResolvedModel, ScriptOutput, ShapeSpec, ShotProject, ShotsOutput, Track, Window,
)
from .state import build_ctx, plan_shots
from .timing import XFADE_MS, ShotWindow
from .validate import resolve_script
from .video import join_shots, mux

GENERATED = Licence(name="CC0-1.0", url="https://creativecommons.org/publicdomain/zero/1.0/",
                    attribution="generated by reel-machine")


def _workers() -> int:
    try:
        return max(1, int(os.environ.get("REEL_EXPLAINER3D_WORKERS", "2")))
    except ValueError:
        return 2


def _artifacts(ctx: StageContext) -> Path:
    path = ctx.workdir / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


class ScriptStage:
    id = "script"
    revision = 1
    cacheable = False
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: Any) -> ScriptOutput:
        inputs = Explainer3DInputs.model_validate(payload)
        try:
            shots = resolve_script(inputs)
        except InvalidInput as exc:
            exc.stage = self.id
            raise
        return ScriptOutput(inputs=inputs, shots=shots)


class AssetsStage:
    """Plan 1: every cast member is a shape, built in the browser; plan 2 adds model files."""

    id = "assets"
    revision = 1
    cacheable = False
    output_model = AssetsOutput

    def run(self, ctx: StageContext, payload: ScriptOutput) -> AssetsOutput:
        models = {}
        for key, member in payload.inputs.cast.items():
            shape = member.asset
            assert isinstance(shape, ShapeSpec)
            models[key] = ResolvedModel(shape=shape, dims=dims(shape), licence=GENERATED,
                                        provenance=Provenance(provider="shape", source=shape.shape))
        return AssetsOutput(inputs=payload.inputs, shots=payload.shots, models=models)


class NarrationStage:
    id = "narration"
    revision = 1
    cacheable = False
    output_model = NarrationOutput

    def run(self, ctx: StageContext, payload: AssetsOutput) -> NarrationOutput:
        from .timing import shot_windows

        inputs, settings = payload.inputs, ctx.config
        provider = ctx.providers.get("narration")
        name = getattr(provider, "name", "")
        if provider_for(inputs.voice) != name:
            raise VoiceUnavailable(
                f"the voice {inputs.voice!r} needs the {provider_for(inputs.voice)!r} narration provider, "
                f"but this deployment serves {name!r}",
                hint=f"use voice '{name}:<voice-id>', or set REEL_TTS on the deployment",
                stage=self.id,
            )
        work = ctx.workdir / "lines"
        work.mkdir(parents=True, exist_ok=True)
        speeches, licence = [], None
        for shot in payload.shots:
            ctx.cancel.raise_if_cancelled()
            speech = provider.synthesize(shot.narration, voice=inputs.voice, language=inputs.language)
            ms = speech.duration_ms or int(ffmpeg.probe(speech.path).duration_s * 1000)
            local = work / f"{shot.id}{Path(speech.path).suffix or '.wav'}"
            shutil.copy2(speech.path, local)
            words = list(speech.segments[0].words) if speech.segments else []
            speeches.append((local, ms, words, shot.narration))
            licence = speech.licence or licence

        windows = shot_windows([ms for _, ms, _, _ in speeches])
        padded, all_words = [], []
        for (path, ms, words, text), window in zip(speeches, windows):
            padded.append(pad_speech(path, work / f"{path.stem}.padded.wav", lead_ms=window.speech_offset_ms,
                                     total_ms=window.duration_ms, settings=settings, cancel=ctx.cancel))
            base = window.start_ms + window.speech_offset_ms
            if not words:
                words = proportional_word_spans(text.split(), 0, ms)
            all_words.append([WordSpan(text=w.text, start_ms=base + w.start_ms, end_ms=base + w.end_ms) for w in words])
        joined = ctx.workdir / "narration.wav"
        if len(padded) == 1:
            shutil.copy2(padded[0], joined)
        else:
            ffmpeg.concat_audio(padded, joined, cancel=ctx.cancel)
        total = windows[-1].end_ms
        asset = ctx.assets.put(joined, kind=AssetKind.NARRATION, licence=licence,
                               provenance=Provenance(provider=name, source=inputs.voice), ext=".wav")
        return NarrationOutput(
            inputs=inputs, shots=payload.shots, models=payload.models, audio_asset=asset.id, duration_ms=total,
            windows=[Window(start_ms=w.start_ms, duration_ms=w.duration_ms, speech_offset_ms=w.speech_offset_ms,
                            speech_ms=w.speech_ms) for w in windows],
            words=all_words, voice=inputs.voice, provider=name, licence=licence,
        )


class ShotsStage:
    id = "shots"
    revision = 1
    cacheable = False
    output_model = ShotsOutput

    def run(self, ctx: StageContext, payload: NarrationOutput) -> ShotsOutput:
        inputs = payload.inputs
        windows = [ShotWindow(i, w.start_ms, w.duration_ms, w.speech_offset_ms, w.speech_ms)
                   for i, w in enumerate(payload.windows)]
        plans = plan_shots(inputs, payload.shots, windows)
        scene_ctx = build_ctx(inputs)
        projects, cues = [], []
        for i, (plan, window) in enumerate(zip(plans, windows)):
            tail = XFADE_MS if i + 1 < len(plans) and plans[i + 1].join == "xfade" else 0
            words = [{"text": w.text, "start": (w.start_ms - window.start_ms) / 1000, "end": (w.end_ms - window.start_ms) / 1000}
                     for w in payload.words[i]]
            doc = shot_document(inputs, plan, window, words, scene_ctx, render_ms=window.duration_ms + tail)
            key = project_key(doc)
            directory = write_project(doc, ctx.workdir / "projects" / plan.shot_id)
            projects.append(ShotProject(shot_id=plan.shot_id, index=i, dir=str(directory), key=key,
                                        nominal_ms=window.duration_ms, render_ms=window.duration_ms + tail, join=plan.join))
            cues.extend(plan.cues)
        resolved = {
            "shots": [shot.model_dump() for shot in payload.shots],
            "plans": [plan.model_dump() for plan in plans],
            "windows": [w.model_dump() for w in payload.windows],
        }
        (_artifacts(ctx) / "script.resolved.json").write_text(json.dumps(resolved, indent=2, ensure_ascii=False), encoding="utf-8")
        return ShotsOutput(narration=payload, projects=projects, cues=cues)


class PrepStage:
    """Render every shot (cached by content) or, in storyboard mode, one still per shot."""

    id = "prep"
    revision = 1
    cacheable = False
    output_model = PrepOutput

    def halt_for(self, payload: ShotsOutput) -> bool:
        return payload.narration.inputs.mode == "storyboard"

    def run(self, ctx: StageContext, payload: ShotsOutput) -> PrepOutput:
        settings: Settings = ctx.config
        renderer = ctx.providers.get("renderer")
        problems = renderer.missing()
        if problems:
            raise RendererUnavailable("the shot renderer is not usable on this deployment",
                                      hint="; ".join(problems), stage=self.id)
        if self.halt_for(payload):
            return self._storyboard(ctx, payload, renderer, settings)

        cache = settings.workdir / "cache" / "explainer3d" / "shots"

        def render_one(project: ShotProject) -> Track:
            cached = cache / project.key[:2] / f"{project.key}.mp4"
            if not cached.is_file():
                ctx.progress.detail(f"  · rendering {project.shot_id} ({project.render_ms / 1000:.1f}s)")
                tmp = cached.with_suffix(".partial.mp4")
                renderer.render(Path(project.dir), tmp, fps=FPS, seconds=project.render_ms / 1000, cancel=ctx.cancel)
                tmp.replace(cached)
            else:
                ctx.progress.detail(f"  · {project.shot_id}: cached")
            return Track(shot_id=project.shot_id, path=str(cached), render_ms=project.render_ms, renderer=renderer.name)

        with ThreadPoolExecutor(max_workers=_workers()) as pool:
            tracks = list(pool.map(render_one, payload.projects))
        return PrepOutput(shots=payload, tracks=tracks, renderer=renderer.name)

    def _storyboard(self, ctx, payload: ShotsOutput, renderer, settings) -> PrepOutput:
        stills = ctx.workdir / "stills"
        frames = []
        for project in payload.projects:
            frames.append(renderer.snapshot(Path(project.dir), project.nominal_ms / 1000 * 0.6,
                                            stills / f"{project.index:03d}.png", cancel=ctx.cancel))
        cols = min(4, len(frames))
        rows = math.ceil(len(frames) / cols)
        for index in range(len(frames), cols * rows):  # fill the grid so `tile` emits it
            shutil.copy2(frames[-1], stills / f"{index:03d}.png")
        sheet = _artifacts(ctx) / "storyboard.jpg"
        ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-framerate", "1",
                    "-i", str(stills / "%03d.png"), "-vf", f"scale=360:640,tile={cols}x{rows}:padding=8:color=white",
                    "-frames:v", "1", "-q:v", "3", str(sheet)], cancel=ctx.cancel)
        return PrepOutput(shots=payload, storyboard=str(sheet), renderer=renderer.name, halt_pipeline=True)


class ComposeStage:
    id = "compose"
    revision = 1
    cacheable = False
    output_model = ComposeOutput

    def run(self, ctx: StageContext, payload: PrepOutput) -> ComposeOutput:
        narration = payload.shots.narration
        inputs = narration.inputs
        settings: Settings = ctx.config
        video = [Clip(element_id=t.shot_id, asset=t.path, start_ms=w.start_ms, duration_ms=w.duration_ms, origin="scene")
                 for t, w in zip(payload.tracks, narration.windows)]
        captions = [
            Caption(start_ms=words[0].start_ms if words else w.start_ms, end_ms=words[-1].end_ms if words else w.start_ms + w.duration_ms,
                    text=shot.narration, lang=inputs.language, style_ref="explainer3d.caption", words=words,
                    meta={"source": shot.id})
            for shot, words, w in zip(narration.shots, narration.words, narration.windows)
        ]
        timeline = Timeline(
            duration_ms=narration.duration_ms, aspect="vertical", render_mode="hyperframes", style=inputs.look,
            video=video, captions=captions,
            audio=[TimedAudioRef(element_id="narration", asset=narration.audio_asset,
                                 duration_ms=narration.duration_ms, role="narration")],
            meta={"joins": [p.join for p in payload.shots.projects], "renderer": payload.renderer},
        )
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        name = inputs.name or ctx.job.id
        return ComposeOutput(prep=payload, timeline=timeline, render_options={
            "outdir": str(outdir), "reel_name": name, "workdir": str(settings.workdir / "render" / name),
            "credits": [f"Narration: {inputs.voice}", "3D: reel-machine explainer3d (three.js, HyperFrames)"],
        })


class RenderStage:
    id = "render"
    revision = 1
    cacheable = False
    output_model = RenderOutput

    def run(self, ctx: StageContext, payload: ComposeOutput) -> RenderOutput:
        settings: Settings = ctx.config
        prep, timeline, options = payload.prep, payload.timeline, payload.render_options
        narration = prep.shots.narration
        inputs = narration.inputs
        work, outdir = Path(options["workdir"]), Path(options["outdir"])
        work.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)
        name = options["reel_name"]

        joined = join_shots([Path(t.path) for t in prep.tracks], [p.join for p in prep.shots.projects],
                            [p.nominal_ms for p in prep.shots.projects], work / "video.mp4",
                            settings=settings, fps=FPS, cancel=ctx.cancel)
        sfx_dir = work / "sfx"
        cues = [(cue["at_ms"], synth_sfx(cue["sfx"], sfx_dir / f"{cue['sfx']}.wav", settings=settings))
                for cue in prep.shots.cues]
        music = Path(inputs.music).expanduser() if inputs.music else None
        if music is not None and not music.is_file():
            raise InvalidInput(f"music file not found: {music}", details={"path": "music"}, stage=self.id)
        audio = mix(ctx.assets.get(narration.audio_asset), cues, music, work / "audio.wav",
                    total_ms=timeline.duration_ms, settings=settings, profile=inputs.audio_profile, cancel=ctx.cancel)
        video = mux(joined, audio, outdir / f"{name}.mp4", total_ms=timeline.duration_ms, settings=settings, cancel=ctx.cancel)
        srt = write_srt(timeline.captions, work / f"{name}.srt")
        manifest = {
            "reel": str(video), "recipe": "explainer3d", "mode": "hyperframes",
            "created": datetime.now(timezone.utc).isoformat(), "durationMs": timeline.duration_ms,
            "shots": len(prep.tracks), "look": inputs.look, "renderer": prep.renderer,
            "models": {k: {"licence": m.licence.model_dump(), "provenance": m.provenance.model_dump()}
                       for k, m in narration.models.items()},
            "credits": options["credits"],
        }
        manifest_path = outdir / f"{name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return RenderOutput(video=str(video), srt=str(srt), manifest=str(manifest_path),
                            duration_ms=timeline.duration_ms)
```

- [ ] **Step 5: Implement `recipe.py` (verify and probe come in Task 11)**

```python
"""The explainer3d recipe: a scene script becomes a narrated 3D reel, shot by shot."""

from __future__ import annotations

from typing import Any

from ...core.manifest import VerificationReport
from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec, StageSpec
from ...core.stage import StageContext
from .models import Explainer3DInputs
from .stages import AssetsStage, ComposeStage, NarrationStage, PrepStage, RenderStage, ScriptStage, ShotsStage


class Explainer3DRecipe:
    spec = RecipeSpec(
        id="explainer3d",
        title="3D explainer",
        summary=(
            "A scene script — cast, shots, moves, camera — becomes a narrated vertical reel "
            "rendered in 3D (three.js via HyperFrames). Objects carry over between shots and "
            "a camera can continue across a cut. `storyboard` renders one still per shot."
        ),
        input_model=Explainer3DInputs,
        version=1,
        stages=(
            StageSpec(stage=ScriptStage()),
            StageSpec(stage=AssetsStage(), feeds="script"),
            StageSpec(stage=NarrationStage(), feeds="assets", uses=("narration",)),
            StageSpec(stage=ShotsStage(), feeds="narration"),
            StageSpec(stage=PrepStage(), feeds="shots", uses=("renderer",)),
            StageSpec(stage=ComposeStage(), feeds="prep"),
            StageSpec(stage=RenderStage(), feeds="compose"),
        ),
        providers={
            "narration": ProviderReq(group="narration", default="fake",
                                     description="Text-to-speech with word timings"),
            "renderer": ProviderReq(group="renderers", default="hyperframes",
                                    description="Renders each shot (Node >= 22, HyperFrames, Chrome, ffprobe)"),
        },
        cost=CostNote(unit="tts_characters", estimate="narration length",
                      notes="the fake voice is free; cloud voices bill per character",
                      providers=("elevenlabs", "cartesia", "fake")),
        artifacts=("reel.mp4", "captions.srt", "reel.json", "timeline.json", "storyboard.jpg", "script.resolved.json"),
        render_modes=("hyperframes",),
        example={
            "mode": "storyboard",
            "title": "The Secret Bait That Attracts Big Carp",
            "cast": {"bowl": {"asset": {"shape": "bowl", "height": 0.2, "material": "stone"}, "at": "stage.center"},
                     "potato": {"asset": {"shape": "ball", "height": 0.1, "colour": "#d9b44a"}}},
            "shots": [{"narration": "First, a steamed potato goes into the bowl.", "label": "Step 1",
                       "camera": {"move": "orbit", "target": "bowl", "from": 10, "to": 40},
                       "actions": [{"move": "fly_in", "actor": "potato"},
                                   {"move": "drop_into", "actor": "potato", "into": "bowl"}]}],
        },
    )

    def probe(self, inputs: Explainer3DInputs, ctx: ProbeContext) -> ProbeReport:
        return ProbeReport(ok=True)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> VerificationReport | None:
        return None
```

In `pyproject.toml` `[project.entry-points."reelmachine.recipes"]` add `explainer3d = "reelmachine.recipes.explainer3d.recipe:Explainer3DRecipe"`, then `uv sync`.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_explainer3d_engine.py tests/test_explainer3d.py tests/test_engine.py tests/test_categories.py -q`
Expected: PASS. If a test enumerating recipes asserts an exact list, add `explainer3d` to its expectation.

- [ ] **Step 7: Commit**

```bash
git add reelmachine/recipes/explainer3d pyproject.toml uv.lock tests/
git commit -m "explainer3d: the recipe end to end — storyboard and build, shots cached by content"
```

---

### Task 11: Verifier and probe

**Files:**
- Create: `reelmachine/recipes/explainer3d/verify.py`
- Modify: `reelmachine/recipes/explainer3d/recipe.py`
- Test: `tests/test_explainer3d.py` (probe, fast), `tests/test_explainer3d_engine.py` (verify, slow)

**Interfaces:**
- Produces: `verify_explainer3d(ctx, outputs) -> VerificationReport`; `motion_score(video, start_s, end_s, *, settings) -> float`; `MOTION_MIN = 0.5`. Probe `details`: `{"shots": [{"id", "estimatedMs"}], "estimatedMs": int, "cost": {"ttsCharacters": int, "voice": str}}`; checks `script`, `renderer`, `voice`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_explainer3d.py
from reelmachine.core.recipe import ProbeContext
from reelmachine.core.stage import StaticProviders
from reelmachine.recipes.explainer3d.recipe import Explainer3DRecipe


class _Voice:
    name = "fake"


class _Renderer:
    name = "hyperframes"

    def __init__(self, problems):
        self.problems = problems

    def missing(self):
        return self.problems


def _probe(inputs, problems=()):
    ctx = ProbeContext(providers=StaticProviders({"narration": _Voice(), "renderer": _Renderer(list(problems))}))
    return Explainer3DRecipe().probe(Explainer3DInputs.model_validate(inputs), ctx)


def test_probe_estimates_length_and_cost():
    report = _probe(_script(shots=[{"narration": "one two three four five", "camera": {"move": "orbit"}}]))
    assert report.ok and report.details["estimatedMs"] >= 1500
    assert report.details["cost"]["ttsCharacters"] == len("one two three four five")


def test_probe_reports_script_errors_with_the_path():
    report = _probe(_script(look="neon"))
    check = next(c for c in report.checks if c.name == "script")
    assert not report.ok and not check.ok and "look" in check.detail


def test_probe_reports_a_missing_renderer():
    report = _probe(_script(), problems=["node is not on PATH"])
    assert not report.ok and "node" in next(c for c in report.checks if c.name == "renderer").detail
```

```python
# append to tests/test_explainer3d_engine.py
from reelmachine.recipes.explainer3d.verify import MOTION_MIN, motion_score


def test_motion_score_tells_a_frozen_clip_from_a_moving_one(tmp_path):
    settings = _settings(tmp_path)
    moving = _clip(settings, tmp_path / "m.mp4", 2.0)
    frozen = tmp_path / "f.mp4"
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                "color=c=gray:size=1080x1920:rate=30:duration=2", "-pix_fmt", "yuv420p", str(frozen)])
    assert motion_score(moving, 0.0, 2.0, settings=settings) >= MOTION_MIN
    assert motion_score(frozen, 0.0, 2.0, settings=settings) < MOTION_MIN
```

Also append the full-build verification check (it reads the job manifest the executor writes):

```python
def test_build_is_verified(tmp_path):
    import json
    job = _run(tmp_path, SCRIPT)
    manifest = next(a for a in job.result.artifacts if a.name == "manifest.json")
    verification = json.loads(Path(manifest.path).read_text())["verification"]
    assert verification["ok"], verification
    assert {c["name"] for c in verification["checks"]} >= {"format", "duration", "motion", "licences", "renderer"}
```

- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError: ...verify`; probe returns no details).

- [ ] **Step 3: Implement `verify.py`**

```python
"""Verify a finished explainer3d reel by inspecting the file itself."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from ... import ffmpeg
from ...config import Settings
from ...core.manifest import CheckResult, VerificationReport

MOTION_MIN = 0.5  # mean absolute grey difference (0–255) between a shot's early and late frames
_W, _H = 108, 192


def _grey(video: Path, at_s: float, settings: Settings) -> np.ndarray:
    proc = subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{at_s:.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={_W}:{_H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    pixels = np.frombuffer(proc.stdout or b"", dtype=np.uint8)
    return pixels.reshape(_H, _W).astype(np.float32) if pixels.size == _W * _H else np.zeros((_H, _W), np.float32)


def motion_score(video: Path, start_s: float, end_s: float, *, settings: Settings) -> float:
    """How much the picture changes across a window, ignoring the caption band."""
    span = end_s - start_s
    first = _grey(video, start_s + span * 0.15, settings)
    last = _grey(video, start_s + span * 0.85, settings)
    top, bottom = int(_H * 0.12), int(_H * 0.70)  # skip the title banner and the captions
    return float(np.abs(first[top:bottom] - last[top:bottom]).mean())


def verify_explainer3d(ctx: Any, outputs: dict[str, Any]) -> VerificationReport:
    settings: Settings = ctx.config
    render, compose = outputs.get("render"), outputs.get("compose")
    if render is None or compose is None:  # storyboard mode: nothing to verify
        return VerificationReport(ok=True, render_mode="hyperframes", notes="storyboard")
    video = Path(render.video)
    info = ffmpeg.probe(video)
    timeline = compose.timeline
    checks = [
        CheckResult(name="format", ok=(info.width, info.height) == (1080, 1920) and info.has_video and info.has_audio,
                    detail=f"{info.width}x{info.height} video={info.has_video} audio={info.has_audio}"),
        CheckResult(name="duration", ok=abs(info.duration_s * 1000 - timeline.duration_ms) <= 100,
                    detail=f"file {info.duration_s * 1000:.0f} ms, narration {timeline.duration_ms} ms"),
    ]
    frozen = []
    for clip in timeline.video:
        score = motion_score(video, clip.start_ms / 1000, (clip.start_ms + clip.duration_ms) / 1000, settings=settings)
        if score < MOTION_MIN:
            frozen.append(f"{clip.element_id} ({score:.2f})")
    checks.append(CheckResult(name="motion", ok=not frozen, detail="frozen: " + ", ".join(frozen) if frozen else "every shot moves"))
    models = compose.prep.shots.narration.models
    unlicensed = sorted(k for k, m in models.items() if not m.licence.name)
    checks.append(CheckResult(name="licences", ok=not unlicensed, detail=", ".join(unlicensed) or "every model licensed"))
    renderers = {t.renderer for t in compose.prep.tracks}
    checks.append(CheckResult(name="renderer", ok=renderers == {compose.prep.renderer},
                              detail=f"tracks by {sorted(renderers)}, manifest names {compose.prep.renderer!r}"))
    return VerificationReport(ok=all(c.ok for c in checks), checks=checks, render_mode="hyperframes")
```

- [ ] **Step 4: Wire probe and verify into `recipe.py`**

Replace `probe` and `verify` in `Explainer3DRecipe` with:

```python
    def probe(self, inputs: Explainer3DInputs, ctx: ProbeContext) -> ProbeReport:
        from ...core.errors import InvalidInput
        from ...core.manifest import CheckResult
        from ..doodle.narration import provider_for
        from .timing import estimate_speech_ms, shot_windows
        from .validate import resolve_script

        checks: list[CheckResult] = []
        details: dict[str, Any] = {}
        try:
            shots = resolve_script(inputs)
            checks.append(CheckResult(name="script", ok=True, detail=f"{len(shots)} shots"))
            windows = shot_windows([estimate_speech_ms(s.narration, inputs.language) for s in shots])
            details["shots"] = [{"id": s.id, "estimatedMs": w.duration_ms} for s, w in zip(shots, windows)]
            details["estimatedMs"] = windows[-1].end_ms
        except InvalidInput as exc:
            checks.append(CheckResult(name="script", ok=False, detail=f"{exc.details.get('path', '')}: {exc.message}"))
            details["error"] = {"path": exc.details.get("path", ""), "message": exc.message, "hint": exc.hint}
        details["cost"] = {"ttsCharacters": sum(len(s.narration) for s in inputs.shots), "voice": inputs.voice}

        providers = ctx.providers
        renderer = providers.get("renderer") if providers else None
        problems = renderer.missing() if renderer is not None else ["no renderer bound"]
        checks.append(CheckResult(name="renderer", ok=not problems, detail="; ".join(problems) or getattr(renderer, "name", "")))
        voice = providers.get("narration") if providers else None
        bound = getattr(voice, "name", "")
        checks.append(CheckResult(name="voice", ok=provider_for(inputs.voice) == bound,
                                  detail=f"requested {provider_for(inputs.voice)!r}, deployment serves {bound!r}"))
        return ProbeReport(ok=all(c.ok for c in checks), checks=checks, details=details)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> VerificationReport | None:
        from .verify import verify_explainer3d

        return verify_explainer3d(ctx, result)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_explainer3d.py tests/test_explainer3d_engine.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add reelmachine/recipes/explainer3d tests/test_explainer3d.py tests/test_explainer3d_engine.py
git commit -m "explainer3d: a verifier that refuses frozen shots, and a probe that costs the script"
```

---

### Task 12: Real renders, docs and CI

**Files:**
- Create: `tests/test_explainer3d_render.py`
- Modify: `README.md`, `.env.example`, `docs/MCP.md` (one example under `probe`), `.github/workflows/ci.yml`

- [ ] **Step 1: Write the real-render tests (they skip without the renderer)**

```python
# tests/test_explainer3d_render.py
"""Real HyperFrames renders: determinism, camera continuity and a full reel."""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import numpy as np
import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.render.hyperframes import HyperframesRenderer

SETTINGS = get_settings()
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(bool(HyperframesRenderer(SETTINGS).missing()), reason="HyperFrames renderer not available"),
]

from tests.test_explainer3d_engine import SCRIPT  # noqa: E402


def _settings(tmp_path):
    return dataclasses.replace(SETTINGS, workdir=tmp_path / "work", outdir=tmp_path / "out", tts="fake")


def _frame(video: Path, index: int) -> np.ndarray:
    out = subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{index})",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(out, dtype=np.uint8).astype(np.int16)


def _build(tmp_path, inputs):
    engine = Engine(_settings(tmp_path))
    try:
        job = engine.submit(JobRequest(recipe="explainer3d", inputs=inputs), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=1800)
    finally:
        engine.close()


def test_a_real_reel_builds_and_verifies(tmp_path):
    import json
    job = _build(tmp_path, SCRIPT)
    assert job.state == JobState.SUCCEEDED, job.error
    manifest = next(a for a in job.result.artifacts if a.name == "manifest.json")
    assert json.loads(Path(manifest.path).read_text())["verification"]["ok"]


def test_the_same_shot_renders_identical_pixels(tmp_path):
    job = _build(tmp_path, {**SCRIPT, "shots": SCRIPT["shots"][:1]})
    shot_dir = next(Path(tmp_path / "work").rglob("projects/shot-1"))
    renderer = HyperframesRenderer(_settings(tmp_path))
    a = renderer.render(shot_dir, tmp_path / "a.mp4", seconds=3)
    b = renderer.render(shot_dir, tmp_path / "b.mp4", seconds=3)
    assert np.array_equal(_frame(a, 40), _frame(b, 40))


def test_a_continuing_camera_has_no_jump_at_the_cut(tmp_path):
    import json
    job = _build(tmp_path, SCRIPT)  # shot 2 continues shot 1's camera, so the join is a hard cut
    by_name = {a.name: Path(a.path) for a in job.result.artifacts}
    windows = json.loads(by_name["script.resolved.json"].read_text())["windows"]
    boundary = round(windows[1]["start_ms"] / 1000 * 30)  # first frame of shot 2
    reel = by_name["reel.mp4"]
    across = np.abs(_frame(reel, boundary - 1) - _frame(reel, boundary)).mean()
    within = np.abs(_frame(reel, boundary - 2) - _frame(reel, boundary - 1)).mean()
    assert across < max(12.0, 4 * within)  # a cut inside a chain looks like any other frame step
```

- [ ] **Step 2: Run them locally**

Run: `REEL_FFPROBE=/path/to/ffprobe uv run pytest tests/test_explainer3d_render.py -q -m slow`
Expected: PASS where HyperFrames is available; SKIP otherwise. If the continuity threshold is too strict for the look (shadows, particles), measure the actual mean difference at a continuing cut and at a crossfade boundary of the same reel and set the threshold between them — never above half the crossfade's value.

- [ ] **Step 3: Docs**

`README.md`: add a row to the recipe table:

```markdown
| `explainer3d` | narrated 3D explainer reels: an agent writes a scene script (cast, shots, moves, camera); each shot is a three.js scene rendered by HyperFrames, objects carry over between shots and a camera can continue across a cut; `storyboard` mode renders one still per shot first | ffmpeg **and ffprobe**, Node ≥ 22 (HyperFrames runs through `npx`, pinned); cloud voices need a key |
```

and to *Layout*: `explainer3d/  script → assets → narration → shots → prep (HyperFrames) → compose → render`.

`.env.example` (near `REEL_FFPROBE`):

```bash
# explainer3d renders shots with HyperFrames, which refuses to run without ffprobe.
# REEL_FFPROBE=/opt/homebrew/opt/ffmpeg-full/bin/ffprobe
# Shots rendered in parallel (each HyperFrames render also parallelises its own capture).
# REEL_EXPLAINER3D_WORKERS=2
```

`docs/MCP.md`, under `probe`, add:

````markdown
For `explainer3d`, probe validates the whole scene script without rendering — a refusal
names the field (`details.error.path`, e.g. `shots[2].actions[0].into`) — and returns the
estimated length per shot and the voice characters it will spend:

```json
{"recipeId": "explainer3d", "inputs": {"mode": "storyboard", "cast": {"bowl": {"asset": {"shape": "bowl"}, "at": "stage.center"}}, "shots": [{"narration": "A bowl.", "camera": {"move": "orbit", "target": "bowl"}}]}}
```
````

- [ ] **Step 4: CI**

Add a job to `.github/workflows/ci.yml`:

```yaml
  explainer3d:
    name: explainer3d (Node + HyperFrames)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - name: Install ffmpeg (with ffprobe)
        run: sudo apt-get update && sudo apt-get install -y ffmpeg
      - name: Sync
        run: uv sync --frozen
      - name: Runtime unit tests
        run: node --test reelmachine/recipes/explainer3d/runtime/tests
      - name: Recipe tests, including real renders
        run: uv run pytest -q tests/test_explainer3d.py tests/test_explainer3d_engine.py tests/test_explainer3d_render.py tests/test_hyperframes_renderer.py
```

- [ ] **Step 5: Full suite**

Run: `uv run pytest -q && node --test reelmachine/recipes/explainer3d/runtime/tests`
Expected: PASS (slow real-render tests skip without the renderer).

- [ ] **Step 6: Commit**

```bash
git add tests/test_explainer3d_render.py README.md .env.example docs/MCP.md .github/workflows/ci.yml
git commit -m "explainer3d: real-render tests, docs, and a CI job with Node and HyperFrames"
```

---

## Self-review notes

- **Spec coverage (plan-1 scope):** §3 stages → Tasks 9–10; §3.1 timing → Task 2; §4 script/rules → Tasks 1, 4; §5 runtime contract, containers, looks → Tasks 6–7; §5.1 in-shot captions (word highlight; ruby is plan 4) → Task 7; §6 state + camera chains → Task 5; §8 renderer → Task 8; §9 probe + modes → Tasks 10–11; §11 errors (`RENDERER_UNAVAILABLE`, `SHOT_RENDER_FAILED`, `INVALID_INPUT` paths) → Tasks 1, 4, 8, 10; §12 verifier → Task 11; §13 tests → every task + Task 12. Deferred by design: §5.2, §5.3, §7, `list_moves`/`find_models`, custom code, music loops → plans 2–4.
- **Clarification of spec §6:** "joins move at constant velocity" is implemented as *no easing at interior joins* (motion never stops at a cut); exact speed equality across different move types is not attempted. Recorded in the spec's next revision.
- **Validation is stricter than the spec's verifier-only rule for static shots** (Review Focus 1): a shot that cannot move is refused before any render.
