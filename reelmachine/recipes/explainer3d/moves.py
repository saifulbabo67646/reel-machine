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


class _Coloured(_Params):
    @field_validator("colour", check_fields=False)
    @classmethod
    def check_colour(cls, value):
        return hex_colour(value)


class NoParams(_Params):
    pass


class FlyInParams(_Params):
    from_: Literal["sky", "left", "right", "front"] = Field("sky", alias="from")
    to: str | None = None


class DropIntoParams(_Coloured):
    into: str
    fill_add: float = Field(0.15, ge=0, le=1)
    colour: str | None = None


class PlaceOnParams(_Params):
    on: str


class SpinParams(_Params):
    turns: float = Field(1.0, gt=0, le=4)


class ExitParams(_Params):
    to: Literal["sky", "left", "right"] = "right"


class PourParams(_Coloured):
    into: str
    fill_add: float = Field(0.2, ge=0, le=1)
    colour: str = "#f2efe6"


class SprinkleParams(_Coloured):
    into: str
    fill_add: float = Field(0.03, ge=0, le=1)
    colour: str = "#ffffff"


class MashParams(_Coloured):
    colour: str | None = None


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
