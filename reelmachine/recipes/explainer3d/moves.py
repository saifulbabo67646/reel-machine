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
from .world import Ctx, World, add, center, interior, point, pour_fill, set_actor, set_fill, top

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


# ---- cinematic moves
class LeapParams(_Params):
    spot: str = "lake.near"  # where on the water the arc is centred
    span: float = Field(1.6, gt=0.2, le=6)
    height: float = Field(0.9, gt=0.1, le=4)
    slowmo: float = Field(0.6, ge=0, le=0.9)  # how much the apex is slowed
    direction: Literal["right", "left"] = "right"


class SlamParams(_Params):
    on: str | None = None  # an anchor or an actor's top; home when omitted
    drop: float = Field(0.9, gt=0.1, le=4)


class BurstParams(_Coloured):
    into: str
    fill_add: float = Field(0.2, ge=0, le=1)
    colour: str | None = None  # the crumbs; the actor's colour when omitted


class DrizzleParams(_Coloured):
    into: str
    colour: str = "#f0a020"
    fill_add: float = Field(0.05, ge=0, le=1)


class SmashParams(_Coloured):
    into: str
    colour: str = "#c97a34"  # the paste it all becomes
    fill_add: float = Field(0.15, ge=0, le=1)


class PopOutParams(_Params):
    from_: str = Field(alias="from")  # the container the pieces come out of
    to: str = "stage.front"
    take: float = Field(0.3, ge=0, le=1)  # how much of the fill they use up


class ThrowParams(_Params):
    to: str = "lake.far"


class SplashParams(_Params):
    spot: str = "lake.near"
    size: float = Field(1.0, gt=0.1, le=3)


class SinkParams(_Params):
    from_: str = Field("underwater.surface", alias="from")
    to: str = "underwater.center"


class SchoolParams(_Params):
    around: str  # the bait: an actor or an anchor
    eat: bool = False  # the lead fish lunges and takes it
    radius: float = Field(0.45, gt=0.1, le=5)


# ---- characters
class WalkToParams(_Params):
    to: str  # an anchor, a mark, or an actor (they stop in front of it)
    gait: Literal["walk", "jog", "sprint"] = "walk"
    stop: float | None = Field(None, ge=0, le=3)  # metres short of `to`; 0.45 for an actor, 0 for a place


class TurnToParams(_Params):
    to: str


class ActParams(_Params):
    clip: str  # a friendly clip name (see the guide): dance, pick_up, push, fix, jump, punch, cast…

    @field_validator("clip")
    @classmethod
    def check_clip(cls, value: str) -> str:
        from .characters import CLIPS

        if value not in CLIPS:
            raise ValueError(f"unknown clip; one of {sorted(CLIPS)}")
        return value


class SitParams(_Params):
    on: str | None = None  # a seat (an anchor, mark or actor); where they stand when omitted


class PickUpParams(_Params):
    item: str  # a cast member; it rides in their right hand until put_down


class PutDownParams(_Params):
    on: str  # an anchor, a mark or an actor's top


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
    extra_sfx: tuple[tuple[str, float], ...] = ()
    big: bool = False                     # counts as a hook / a beat in the retention lint
    character: bool = False               # the actor must be a character


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


def _dest(ctx, world, ref):
    return point(ctx, world, ref) if ref in ctx["anchors"] else top(ctx, world, ref)


def _leap(world, action, ctx):
    p = action["params"]
    c = point(ctx, world, p["spot"])
    sign = 1.0 if p["direction"] == "right" else -1.0
    return set_actor(world, action["actor"], position=[c[0] + sign * p["span"] / 2, -0.6, c[2]], visible=False)


def _slam(world, action, ctx):
    on = action["params"].get("on")
    dest = list(ctx["homes"][action["actor"]]) if on is None else _dest(ctx, world, on)
    return set_actor(world, action["actor"], position=dest, visible=True, scale=1.0)


def _swallow(world, actor, into, ctx):
    inside = interior(ctx, world, into, world[into]["fill"]["level"])
    return set_actor(world, actor, position=inside, visible=False, scale=0.0)


def _burst(world, action, ctx):
    p, actor = action["params"], action["actor"]
    fill = world[p["into"]]["fill"]
    world = _swallow(world, actor, p["into"], ctx)
    return set_fill(world, p["into"], level=min(1.0, fill["level"] + p["fill_add"]), surface="crumbs",
                    colour=p.get("colour") or ctx["colours"][actor])


def _drizzle(world, action, ctx):
    p = action["params"]
    fill = world[p["into"]]["fill"]
    return set_fill(world, p["into"], level=min(1.0, fill["level"] + p["fill_add"]), glaze=p["colour"])


def _smash(world, action, ctx):
    p = action["params"]
    fill = world[p["into"]]["fill"]
    world = _swallow(world, action["actor"], p["into"], ctx)
    return set_fill(world, p["into"], level=min(1.0, fill["level"] + p["fill_add"]), surface="paste",
                    colour=p["colour"], glaze=None)


def _pop_out(world, action, ctx):
    p = action["params"]
    fill = world[p["from"]]["fill"]
    world = set_fill(world, p["from"], level=max(0.0, fill["level"] - p["take"]))
    return set_actor(world, action["actor"], position=point(ctx, world, p["to"]), visible=True, scale=1.0)


def _throw(world, action, ctx):
    to = point(ctx, world, action["params"]["to"])
    return set_actor(world, action["actor"], position=[to[0], -0.4, to[2]], visible=False)


def _sink(world, action, ctx):
    return set_actor(world, action["actor"], position=point(ctx, world, action["params"]["to"]), visible=True)


HAND = 0.55  # a held prop rides at about this fraction of the holder's height, in Python's view
STOP_AT_ACTOR = 0.45


def _floor(p):
    return [p[0], 0.0, p[2]]


def _walk_to(world, action, ctx):
    p, actor = action["params"], action["actor"]
    to = p["to"]
    start = world[actor]["position"]
    dest = _floor(point(ctx, world, to))
    stop = p["stop"] if p.get("stop") is not None else (0.0 if to in ctx["anchors"] else STOP_AT_ACTOR)
    dx, dz = dest[0] - start[0], dest[2] - start[2]
    dist = math.hypot(dx, dz)
    heading = math.atan2(dx, dz) if dist > 1e-9 else world[actor]["rotationY"]
    k = max(0.0, dist - stop) / dist if dist > 1e-9 else 0.0
    end = [start[0] + dx * k, start[1], start[2] + dz * k]
    world = set_actor(world, actor, position=end, rotationY=heading, visible=True)
    return _carry(world, actor, ctx)


def _turn_to(world, action, ctx):
    actor = action["actor"]
    pos, target = world[actor]["position"], point(ctx, world, action["params"]["to"])
    return set_actor(world, actor, rotationY=math.atan2(target[0] - pos[0], target[2] - pos[2]))


def _carry(world, actor, ctx):
    held = world[actor].get("holding")
    if not held:
        return world
    pos, h = world[actor]["position"], ctx["dims"][actor][1] * world[actor]["scale"]
    return set_actor(world, held, position=[pos[0], pos[1] + h * HAND, pos[2]], visible=True)


def _act(world, action, ctx):
    return world


def _sit(world, action, ctx):
    actor, on = action["actor"], action["params"].get("on")
    world = set_actor(world, actor, pose="sit")
    if on is not None:
        world = set_actor(world, actor, position=_floor(point(ctx, world, on)))
    return _carry(world, actor, ctx)


def _stand_up(world, action, ctx):
    return set_actor(world, action["actor"], pose="stand")


def _pick_up(world, action, ctx):
    actor, item = action["actor"], action["params"]["item"]
    world = set_actor(world, actor, holding=item)
    return _carry(world, actor, ctx)


def _put_down(world, action, ctx):
    actor = action["actor"]
    item = world[actor].get("holding")
    world = set_actor(world, actor, holding=None)
    if item:
        world = set_actor(world, item, position=_dest(ctx, world, action["params"]["on"]), visible=True)
    return world


def _school(world, action, ctx):
    p = action["params"]
    world = set_actor(world, action["actor"], position=center(ctx, world, p["around"]), visible=True, scale=1.0)
    if p["eat"] and p["around"] in world:
        world = set_actor(world, p["around"], visible=False, scale=0.0)
    return world


MOVES: dict[str, Move] = {
    move.name: move
    for move in (
        Move("appear", "object", "Fade/scale an actor into view where it stands.", NoParams,
             {"move": "appear", "actor": "bowl"}, ("pop", 0.05), "required", _appear),
        Move("scale_pop", "object", "Pop an actor into view with a small overshoot.", NoParams,
             {"move": "scale_pop", "actor": "bowl"}, ("pop", 0.0), "required", _appear, big=True),
        Move("fly_in", "object", "Fly an actor in from the sky or a side and land it (on `to`, or home).",
             FlyInParams, {"move": "fly_in", "actor": "potato", "params": {"from": "sky"}},
             ("whoosh", 0.0), "required", _fly_in, refs=("to",), big=True),
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
        # ---- cinematic: built for the lake/underwater sets and the cinematic look
        Move("leap", "cinematic", "A fish bursts out of the water in a slow-motion arc and dives back in, "
             "with splashes. The hook of a fishing reel.", LeapParams,
             {"move": "leap", "actor": "fish", "params": {"spot": "lake.near", "slowmo": 0.6}},
             ("splash", 0.02), "required", _leap, refs=("spot",), extra_sfx=(("whoosh", 0.25), ("splash", 0.92)), big=True),
        Move("slam", "cinematic", "Drop an actor from above onto its spot: squash, dust, camera shake.", SlamParams,
             {"move": "slam", "actor": "potato"}, ("thud", 0.45), "required", _slam, refs=("on",), big=True),
        Move("burst", "cinematic", "Hop an actor into a container and explode it into crumbs that settle inside "
             "(bread, biscuits). White flash.", BurstParams, {"move": "burst", "actor": "bread", "params": {"into": "bowl"}},
             ("crunch", 0.55), "required", _burst, refs=("into",), container_refs=("into",), extra_sfx=(("boom", 0.55),),
             big=True),
        Move("drizzle", "cinematic", "A glossy stream (honey, syrup) pours from above and coils on top.",
             DrizzleParams, {"move": "drizzle", "params": {"into": "bowl", "colour": "#f0a020"}},
             ("ding", 0.05), "forbidden", _drizzle, refs=("into",), container_refs=("into",), big=True),
        Move("smash", "cinematic", "Lift an actor over a container, hold, then slam it in: flash, shockwave, "
             "flying chunks; the contents become a paste.", SmashParams,
             {"move": "smash", "actor": "potato", "params": {"into": "bowl"}},
             ("boom", 0.62), "required", _smash, refs=("into",), container_refs=("into",),
             extra_sfx=(("whoosh", 0.5), ("crunch", 0.63)), big=True),
        Move("pop_out", "cinematic", "Pieces (a cast member with count > 1, e.g. bait balls) pop out of a "
             "container one by one into a row at `to`.", PopOutParams,
             {"move": "pop_out", "actor": "balls", "params": {"from": "bowl", "to": "stage.front"}},
             ("pop", 0.3), "required", _pop_out, refs=("from", "to"), container_refs=("from",),
             extra_sfx=(("pop", 0.45), ("pop", 0.6), ("pop", 0.75)), big=True),
        Move("throw", "cinematic", "Throw an actor in an arc into the water at `to`: it splashes in and is gone.",
             ThrowParams, {"move": "throw", "actor": "ball", "params": {"to": "lake.far"}},
             ("whoosh", 0.15), "required", _throw, refs=("to",), extra_sfx=(("splash", 0.6),), big=True),
        Move("splash", "cinematic", "A splash on the water at `spot`: droplets, mist, ripples.", SplashParams,
             {"move": "splash", "params": {"spot": "lake.near"}}, ("splash", 0.0), "forbidden", _steam,
             refs=("spot",), big=True),
        Move("sink", "cinematic", "Underwater: an actor sinks from `from` to `to`, trailing bubbles.", SinkParams,
             {"move": "sink", "actor": "ball", "params": {"to": "underwater.center"}},
             ("bubbles", 0.0), "required", _sink, refs=("from", "to")),
        Move("school", "cinematic", "Underwater: fish (a model with count > 1) circle in from the murk around "
             "`around`; with eat, the lead fish lunges and takes it.", SchoolParams,
             {"move": "school", "actor": "fish", "params": {"around": "ball", "eat": True}},
             ("bubbles", 0.1), "required", _school, refs=("around",), extra_sfx=(("gulp", 0.78),), big=True),
        # ---- characters (cast members with {"character": ...})
        Move("walk_to", "character", "Walk (or jog, sprint) to a place, a mark or someone; they arrive facing "
             "the way they went and stop short of an actor.", WalkToParams,
             {"move": "walk_to", "actor": "chef", "params": {"to": "front"}}, None, "required", _walk_to,
             refs=("to",), character=True),
        Move("turn_to", "character", "Turn to face a place or someone.", TurnToParams,
             {"move": "turn_to", "actor": "chef", "params": {"to": "stage.center"}}, None, "required", _turn_to,
             refs=("to",), character=True),
        Move("act", "character", "Play one of the animation clips: dance, jump, pick_up, interact, push, fix, "
             "crouch, punch, hit, roll, cast, fall…", ActParams,
             {"move": "act", "actor": "chef", "params": {"clip": "dance"}}, None, "required", _act,
             character=True, big=True),
        Move("talk", "character", "Talk with their hands while the line plays (seated if sitting).", NoParams,
             {"move": "talk", "actor": "chef"}, None, "required", _act, character=True),
        Move("sit", "character", "Sit down (on a seat, if given) and stay seated.", SitParams,
             {"move": "sit", "actor": "chef"}, None, "required", _sit, refs=("on",), character=True),
        Move("stand_up", "character", "Stand up from sitting.", NoParams,
             {"move": "stand_up", "actor": "chef"}, None, "required", _stand_up, character=True),
        Move("pick_up", "character", "Reach for a cast member and hold it in the right hand.", PickUpParams,
             {"move": "pick_up", "actor": "chef", "params": {"item": "cup"}}, ("pop", 0.55), "required", _pick_up,
             refs=("item",), character=True),
        Move("put_down", "character", "Put what they hold on a place or an actor's top.", PutDownParams,
             {"move": "put_down", "actor": "chef", "params": {"on": "stage.center"}}, ("thud", 0.6), "required",
             _put_down, refs=("on",), character=True),
    )
}
