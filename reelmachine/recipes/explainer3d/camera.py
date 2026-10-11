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

    fov: float | None = Field(None, ge=15, le=90)  # vertical degrees; wider for big scenes
    aim: tuple[float, float, float] = (0.0, 0.0, 0.0)  # look this far from the target (up at a leap's apex)


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


class TrackParams(_Params):
    offset: tuple[float, float, float] = (0.0, 0.35, 1.3)  # from the tracked actor


class DiveParams(_Params):
    depth: float = Field(0.5, gt=0.05, le=3)  # how far below the surface it looks


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
        CameraMove("track", "Follow the target actor as it moves (a throw, a sinking bait), keeping `offset`.",
                   TrackParams, {"offset": [0, 0.35, 1.3]}),
        CameraMove("dive", "Plunge toward the target on the water and down through the surface; cut to an "
                   "underwater shot next.", DiveParams, {"depth": 0.5}),
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
    fov = params.get("fov") or FOV
    if move == "track":
        o = params["offset"]
        return {"position": [target[i] + o[i] for i in range(3)], "target": list(target), "fov": fov}
    if move == "dive":
        return {"position": [target[0], target[1] + 0.9, target[2] + 2.2], "target": list(target), "fov": fov}
    if move == "orbit":
        angle = math.radians(params["from"])
        position = [
            target[0] + params["radius"] * math.sin(angle),
            target[1] + params["height"],
            target[2] + params["radius"] * math.cos(angle),
        ]
    else:
        position = [target[i] + DEFAULT_OFFSET[i] for i in range(3)]
    return {"position": position, "target": list(target), "fov": fov}


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
    if move == "track":
        o = params["offset"]
        return {"position": [target[i] + o[i] for i in range(3)], "target": list(target), "fov": start["fov"]}
    if move == "dive":
        dest = [target[0], target[1] + 0.05, target[2] + 0.45]  # just above the splash; cut underwater next
        look = [target[0], target[1] - params["depth"], target[2]]
        return {"position": lerp3(sp, dest, p), "target": lerp3(st, look, p), "fov": start["fov"]}
    raise KeyError(move)
