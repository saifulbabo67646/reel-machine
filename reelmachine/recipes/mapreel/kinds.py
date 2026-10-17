"""One builder per segment kind: what the stage loop computed goes in, the segment's places, items,
extras, sound cues and resolved-script entries come out. Camera, layers and project writing stay in the loop."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.timeline import WordSpan
from . import compose
from .geometry import HEIGHT, SAFE, WIDTH, Frame, shape_of
from .models import ResolvedSegment
from .places import Atlas
from .timing import match, match_any, pop_times

#: a rank segment's country and items sit below the badge (top left) and the stat card (top centre)
RANK_SAFE = (0.08, 0.24, 0.92, 0.80)
RANK_AFTER_FILL_MS = 150   # the badge lands just after the country fills ...
STAT_AFTER_FILL_MS = 600   # ... and an unheard stat a beat after that
#: a versus panel's country sits between its name (top) and its stat card (bottom), as fractions of the panel
PANEL_SAFE = (0.08, 0.20, 0.92, 0.70)
PANEL_ENTER_MS = 120       # panel k slides in at k * this
VS_AT_MS = 250             # the VS disc slams down once the first panel is in


def panel_rects(n: int) -> list[tuple[int, int, int, int]]:
    """A versus's panels: n full-width rows, top to bottom, as (x, y, w, h) px."""
    h = HEIGHT // n
    return [(0, k * h, WIDTH, h) for k in range(n)]


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
    panels: list[dict[str, Any]] = field(default_factory=list)  # a versus: the geo stage's panels


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


def _items(seg: ResolvedSegment, spec: Any, ctx: KindContext, built: Built,
           safe: tuple[float, float, float, float] = SAFE) -> None:
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
    built.items = compose.item_entries(items_in, shape_of(ctx.atlas.places[seg.codes[0]]), ctx.landed, safe=safe)
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


def count_spec(value: str) -> dict[str, Any] | None:
    """How a stat counts up: its first number, with the text around it kept as is ("$1,200.50 bn")."""
    found = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    if found is None:
        return None
    digits = found.group()
    whole, _, fraction = digits.partition(".")
    return {"prefix": value[:found.start()], "number": float(digits.replace(",", "")), "decimals": len(fraction),
            "comma": "," in whole, "suffix": value[found.end():]}


def build_rank(seg: ResolvedSegment, spec: Any, ctx: KindContext) -> Built:
    """A country as a ranking entry: a big #N badge as it fills, a stat card that counts up when said."""
    code = seg.codes[0]
    fill = max(300, ctx.fly_ms - 200)
    built = Built(places=[_place(seg, code, fill, ctx)])
    _items(seg, spec, ctx, built, safe=RANK_SAFE)
    rank = {"n": spec.rank, "atMs": fill + RANK_AFTER_FILL_MS, "colour": seg.colours[code], "gold": spec.rank == 1}
    built.extras["rank"] = built.resolved["rank"] = rank
    built.cues.append((rank["atMs"], "ding"))
    if spec.stat is not None:
        (heard,) = match_any([[spec.stat.value]], ctx.spoken)
        # a value said before the badge lands waits for it: the number belongs to the rank
        at = fill + STAT_AFTER_FILL_MS if heard is None else max(ctx.speech_start + heard, rank["atMs"])
        stat = {"value": spec.stat.value, "label": spec.stat.label, "atMs": min(at, ctx.last_ms),
                "count": count_spec(spec.stat.value)}
        built.extras["stat"] = built.resolved["stat"] = stat
        built.cues.append((stat["atMs"], "thud"))
    return built


def build_versus(seg: ResolvedSegment, spec: Any, ctx: KindContext) -> Built:
    """Countries side by side, one per panel, sliding in under a VS slam; each stat counts up when said.
    No flight: the segment cuts in, and the document's own places, items and detail layer stay empty."""
    # a stat pops on its value or, failing that, on its country's name; values and names never share a word
    alternatives = []
    for code, query, stat in zip(seg.codes, spec.countries, spec.stats):
        alternatives += [[stat.value], ctx.atlas.heard_as(code, query)]
    found = match_any(alternatives, ctx.spoken)
    heard = [value if value is not None else name for value, name in zip(found[::2], found[1::2])]
    times, _ = pop_times(heard, overrides=[None] * len(heard), speech_start_ms=ctx.speech_start,
                         speech_end_ms=ctx.speech_end)
    built = Built()
    built.extras["vs"] = built.resolved["vs"] = {"atMs": VS_AT_MS}
    built.cues += [(0, "whoosh"), (VS_AT_MS, "boom")]
    panels, resolved = [], []
    for k, (code, stat, at, panel) in enumerate(zip(seg.codes, spec.stats, times, ctx.panels)):
        frame = Frame(**panel["frame"])
        url = f"layers/panel-{k}.jpg"
        built.links[url] = Path(panel["layer"])
        card = {"value": stat.value, "label": stat.label, "atMs": min(at, ctx.last_ms), "count": count_spec(stat.value)}
        place = compose.place_entry(code, seg.names[code], seg.colours[code], shape_of(ctx.atlas.places[code]),
                                    frame, 0, ctx.atlas.places[code].label)
        panels.append({"rect": list(panel["rect"]), "camera": frame.to_dict(),
                       "layers": [{"url": url, "bbox": list(panel["bbox"])}], "place": place,
                       "name": seg.names[code], "stat": card, "enterMs": k * PANEL_ENTER_MS})
        resolved.append({"code": code, "rect": list(panel["rect"]), "camera": frame.to_dict(), "stat": card})
        built.cues.append((card["atMs"], "thud"))
    built.extras["panels"], built.resolved["panels"] = panels, resolved
    return built


BUILDERS = {"country": build_country, "highlight": build_highlight, "rank": build_rank, "versus": build_versus}


def build(seg: ResolvedSegment, spec: Any, ctx: KindContext) -> Built:
    return BUILDERS[spec.kind](seg, spec, ctx)
