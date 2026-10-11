"""The world shared by Python and the JS runtime: plain JSON-shaped dicts.

Python only ever needs a move's end state (p = 1) — to carry the scene from one shot to
the next — while the runtime evaluates every frame. Both use these exact formulas; a
cross-check test runs the runtime and compares.
"""

from __future__ import annotations

from typing import Any

from .geometry import fill_y

World = dict[str, dict[str, Any]]
Ctx = dict[str, Any]


def add(a, b) -> list[float]:
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


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
    """Mix two #rrggbb colours, rounding half up — the runtime does the same."""
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
