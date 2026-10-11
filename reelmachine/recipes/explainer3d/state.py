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
from .geometry import CONTAINERS, dims
from .models import Explainer3DInputs, ResolvedShot, ShapeSpec
from .moves import MOVES
from .sets import stage_anchors
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


MODEL_COLOUR = "#b07a45"


def actor_dims(inputs: Explainer3DInputs, key: str, models: dict[str, Any] | None = None) -> list[float]:
    """Measured dims once the assets stage has run; before that a model is nominal."""
    if models and key in models:
        return list(models[key].dims)
    asset = inputs.cast[key].asset
    if isinstance(asset, ShapeSpec):
        return list(dims(asset))
    side = asset.height if asset.height is not None else (asset.size or 0.3)
    return [side, side, side]


def is_container(inputs: Explainer3DInputs, key: str) -> bool:
    asset = inputs.cast[key].asset
    return asset.shape in CONTAINERS if isinstance(asset, ShapeSpec) else asset.container


def actor_colour(inputs: Explainer3DInputs, key: str) -> str:
    asset = inputs.cast[key].asset
    if isinstance(asset, ShapeSpec):
        return asset.colour or MATERIAL_COLOURS[asset.material]
    return asset.colour or MODEL_COLOUR


def build_ctx(inputs: Explainer3DInputs, models: dict[str, Any] | None = None) -> Ctx:
    marks = stage_anchors(inputs)
    ctx: Ctx = {"anchors": {k: list(v) for k, v in marks.items()}, "dims": {}, "homes": {}, "colours": {}}
    for key, member in inputs.cast.items():
        ctx["dims"][key] = actor_dims(inputs, key, models)
        ctx["homes"][key] = list(marks[member.at or "stage.center"])
        ctx["colours"][key] = actor_colour(inputs, key)
    return ctx


def initial_world(inputs: Explainer3DInputs, models: dict[str, Any] | None = None) -> World:
    marks = stage_anchors(inputs)
    world: World = {}
    for key, member in inputs.cast.items():
        world[key] = {
            "position": list(marks[member.at or "stage.center"]),
            "visible": member.at is not None,
            "rotationY": 0.0,
            "scale": 1.0,
            "fill": {"level": 0.0, "colour": DEFAULT_FILL, "surface": "liquid", "glaze": None}
            if is_container(inputs, key) else None,
        }
    return world


def _seed(shot_id: str, actor: str | None, index: int) -> int:
    return int(hashlib.sha256(f"{shot_id}|{actor}|{index}".encode()).hexdigest()[:8], 16)


def plan_shots(
    inputs: Explainer3DInputs, shots: list[ResolvedShot], windows: list[ShotWindow],
    models: dict[str, Any] | None = None,
) -> list[ShotPlan]:
    ctx = build_ctx(inputs, models)
    world = initial_world(inputs, models)
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
            move = MOVES[action["move"]]
            for sfx in ((move.sfx,) if move.sfx else ()) + move.extra_sfx:
                at = action["t0"] + (action["t1"] - action["t0"]) * sfx[1]
                cues.append({"at_ms": window.start_ms + int(round(at * 1000)), "sfx": sfx[0]})

        cam = shot.camera
        aim = cam.params.get("aim") or (0.0, 0.0, 0.0)
        target = [c + o for c, o in zip(center(ctx, world, cam.target), aim)]
        if cam.continues and previous_end is not None:
            start = previous_end
        else:
            start = default_start(cam.move, cam.params, target)
        end_world = world
        for action in actions:
            end_world = MOVES[action["move"]].end(end_world, action, ctx)
        # a tracking camera ends on where its actor ends up
        end_target = ([c + o for c, o in zip(center(ctx, end_world, cam.target), aim)]
                      if cam.move == "track" else target)
        end = pose(cam.move, cam.params, start, end_target, 1.0)
        plans.append(ShotPlan(
            shot_id=shot.id, index=shot.index, world=world, actions=actions,
            camera={"move": cam.move, "params": cam.params, "start": start, "target": target,
                    "end": end, "easeIn": True, "easeOut": True,
                    "track": cam.target if cam.move == "track" else None},
            join="none" if shot.index == 0 else ("cut" if cam.continues else "xfade"),
            cues=cues,
        ))
        world = end_world
        previous_end = end

    # a chain eases in at its first shot and out at its last; joins inside keep moving
    for i, plan in enumerate(plans):
        continues_into = i + 1 < len(plans) and plans[i + 1].join == "cut"
        plan.camera["easeIn"] = plan.join != "cut"
        plan.camera["easeOut"] = not continues_into
    return plans
