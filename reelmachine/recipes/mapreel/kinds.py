"""One builder per segment kind: what the stage loop computed goes in, the segment's places, items,
extras, sound cues and resolved-script entries come out. Camera, layers and project writing stay in the loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.timeline import WordSpan
from . import compose
from .geometry import Frame, shape_of
from .models import ResolvedSegment
from .places import Atlas
from .timing import match, match_any, pop_times


@dataclass
class KindContext:
    """What the stage loop computed for this segment (all ms are segment-relative)."""

    atlas: Atlas
    landed: Frame
    fly_ms: int
    speech_start: int
    speech_end: int
    last_ms: int
    spoken: list[WordSpan]
    images: dict[str, str | None]


@dataclass
class Built:
    places: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)       # merged into the segment document
    cues: list[tuple[int, str]] = field(default_factory=list)  # (segment ms, sfx name)
    resolved: dict[str, Any] = field(default_factory=dict)     # merged into the script.resolved entry
    warnings: list[str] = field(default_factory=list)
    links: dict[str, Path] = field(default_factory=dict)       # project path -> local file (item images)


def _place(seg: ResolvedSegment, code: str, fill: int, ctx: KindContext) -> dict[str, Any]:
    return compose.place_entry(code, seg.names[code], seg.colours[code], shape_of(ctx.atlas.places[code]),
                               ctx.landed, fill, ctx.atlas.places[code].label)


def _items(seg: ResolvedSegment, spec: Any, ctx: KindContext, built: Built) -> None:
    """The segment's items, popping as each is said, laid out inside its one country."""
    heard = match([it.say or it.name for it in spec.items], ctx.spoken)
    overrides = [None if it.at is None else int(it.at * 1000) for it in spec.items]
    times, spread = pop_times(heard, overrides=overrides, speech_start_ms=ctx.speech_start,
                              speech_end_ms=ctx.speech_end)
    items_in = []
    for j, (item, at) in enumerate(zip(spec.items, times)):
        if at > ctx.last_ms:
            built.warnings.append(f"{seg.id} {item.name}: pops at {at / 1000:.2f}s, after the segment "
                                  f"ends; moved to {ctx.last_ms / 1000:.2f}s")
            at = ctx.last_ms
        local = ctx.images.get(f"{seg.id}/{j}")
        image = f"images/{Path(local).name}" if local else None
        items_in.append({"name": item.name, "emoji": item.emoji, "atMs": at, "image": image})
        if local:
            built.links[image] = Path(local)
    for j in spread:
        built.warnings.append(f"{seg.id} {spec.items[j].name}: not heard in the narration; "
                              "its pop is spread evenly (add `say` or `at`)")
    built.items = compose.item_entries(items_in, shape_of(ctx.atlas.places[seg.codes[0]]), ctx.landed)
    built.cues.extend((it["atMs"], "pop") for it in built.items)


def build_country(seg: ResolvedSegment, spec: Any, ctx: KindContext) -> Built:
    """One country, filled as the camera lands, its items popping as they are said."""
    built = Built(places=[_place(seg, seg.codes[0], max(300, ctx.fly_ms - 200), ctx)])
    _items(seg, spec, ctx, built)
    return built


def build_highlight(seg: ResolvedSegment, spec: Any, ctx: KindContext) -> Built:
    """Many countries, each filled when named, under an optional legend."""
    # any name the place goes by, or what the script itself wrote for it
    heard = match_any([ctx.atlas.heard_as(c, query) for c, query in zip(seg.codes, spec.countries)], ctx.spoken)
    fills, _ = pop_times(heard, overrides=[None] * len(seg.codes), speech_start_ms=ctx.speech_start,
                         speech_end_ms=ctx.speech_end)
    return Built(places=[_place(seg, c, min(t, ctx.last_ms), ctx) for c, t in zip(seg.codes, fills)],
                 extras={"legend": spec.legend})


BUILDERS = {"country": build_country, "highlight": build_highlight}


def build(seg: ResolvedSegment, spec: Any, ctx: KindContext) -> Built:
    return BUILDERS[spec.kind](seg, spec, ctx)
