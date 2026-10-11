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
            "fill": {"level": 0.0, "colour": DEFAULT_FILL, "surface": "liquid"}
            if shape.shape in CONTAINERS else None,
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
        continues_into = i + 1 < len(plans) and plans[i + 1].join == "cut"
        plan.camera["easeIn"] = plan.join != "cut"
        plan.camera["easeOut"] = not continues_into
    return plans
