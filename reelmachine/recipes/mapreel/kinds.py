"""One builder per segment kind: what the stage loop computed goes in, the segment's places, items,
extras, sound cues and resolved-script entries come out. Camera, layers and project writing stay in the loop."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.timeline import WordSpan
from . import compose
from .geometry import BADGE_DOWN, BADGE_HALF_W, BADGE_RISE, BADGE_UP, HEIGHT, SAFE, WIDTH, Frame, Shape, shape_of
from .models import ResolvedSegment
from .places import Atlas
from .regions import RegionAtlas
from .timing import match, match_any, pop_times

#: a rank segment's country and items sit below the badge (top left) and the stat card (top centre)
RANK_SAFE = (0.08, 0.24, 0.92, 0.80)
def fill_ms(fly_ms: int) -> int:
    """When a segment's country fills: just before the flight lands, or at once when the camera does not fly."""
    return max(300, fly_ms - 200)


RANK_AFTER_FILL_MS = 150   # the badge lands just after the country fills ...
STAT_AFTER_FILL_MS = 600   # ... and an unheard stat a beat after that
#: a versus panel's country sits between its name (top) and its stat card (bottom), as fractions of the panel
PANEL_SAFE = (0.08, 0.20, 0.92, 0.70)
PANEL_ENTER_MS = 120       # panel k slides in at k * this
VS_AT_MS = 250             # the VS disc slams down once the first panel is in
COUNTRY_FAINT = 0.25       # a regions segment's country under its states (runtime/draw.mjs)
REGION_ITEM_MAX, REGION_ITEM_MIN = 220, 120  # a state's items are smaller than a country's: they share it


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
    regions: RegionAtlas | None = None  # a regions segment: the admin-1 data


@dataclass
class Built:
    places: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)       # merged into the segment document
    cues: list[tuple[int, str]] = field(default_factory=list)  # (segment ms, sfx name)
    resolved: dict[str, Any] = field(default_factory=dict)     # merged into the script.resolved entry
    warnings: list[str] = field(default_factory=list)
    links: dict[str, Path] = field(default_factory=dict)       # project path -> local file (item images)
    # what is filled on screen at the cut, for the next segment to fade out (its `previous`); None: `places`.
    # An entry may carry "alpha", the share of a full fill it was drawn at
    shown: list[dict[str, Any]] | None = None


def _place(seg: ResolvedSegment, code: str, fill: int, ctx: KindContext) -> dict[str, Any]:
    return compose.place_entry(code, seg.names[code], seg.colours[code], shape_of(ctx.atlas.places[code]),
                               ctx.landed, fill, ctx.atlas.places[code].label)


def _timed(seg: ResolvedSegment, items: list[Any], ctx: KindContext, built: Built,
           floors: list[int] | None = None) -> list[dict[str, Any]]:
    """Each item's pop: when it is said (or its `at`), never before its floor, clamped to the segment's last pop.
    Image links and warnings go into `built`."""
    heard = match([it.say or it.name for it in items], ctx.spoken)
    overrides = [None if it.at is None else int(it.at * 1000) for it in items]
    times, spread = pop_times(heard, overrides=overrides, speech_start_ms=ctx.speech_start,
                              speech_end_ms=ctx.speech_end)
    items_in = []
    for j, (item, at) in enumerate(zip(items, times)):
        if floors is not None:
            at = max(at, floors[j])
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
        built.warnings.append(f"{seg.id} {items[j].name}: not heard in the narration; "
                              "its pop is spread evenly (add `say` or `at`)")
    return items_in


def _items(seg: ResolvedSegment, spec: Any, ctx: KindContext, built: Built,
           safe: tuple[float, float, float, float] = SAFE) -> None:
    """The segment's items, popping as each is said, laid out inside its one country."""
    items_in = _timed(seg, spec.items, ctx, built)
    built.items = compose.item_entries(items_in, shape_of(ctx.atlas.places[seg.codes[0]]), ctx.landed, safe=safe)
    built.cues.extend((it["atMs"], "pop") for it in built.items)


def build_country(seg: ResolvedSegment, spec: Any, ctx: KindContext) -> Built:
    """One country, filled as the camera lands, its items popping as they are said."""
    built = Built(places=[_place(seg, seg.codes[0], fill_ms(ctx.fly_ms), ctx)])
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
    fill = fill_ms(ctx.fly_ms)
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


TAG_PX, TAG_GAP = 44, 8  # a region's name tag (runtime/draw.mjs regionTag), and its air above an item


Box = tuple[float, float, float, float]  # centre x, centre y, half width, half height (px)


#: words a region's tag leaves off: "Ōsaka Prefecture" is "Ōsaka" on the map
_GENERIC = re.compile(r"\s+(prefecture|province|state|region|governorate|oblast|department|county)$", re.I)


def tag_text(name: str) -> str:
    """A region's tag: its name without a trailing generic word (never down to nothing)."""
    return _GENERIC.sub("", name).strip() or name


def tag_boxes(tags: list[tuple[str, dict[str, float]]], items: list[dict[str, Any]], frame: Frame) -> list[Box]:
    """Where each region's name tag goes on the landed frame: at its label point or, when it would cover an item
    badge or an earlier tag, beside what it hits (left, then right) and only then above it. A state's label
    point is near its middle, where its first item lands."""
    # each badge as drawn: its emoji reaches higher above its anchor than its text below, and it rises as it holds
    taken: list[Box] = []
    for it in items:
        top, bottom = it["y"] - BADGE_UP * it["size"] - BADGE_RISE, it["y"] + BADGE_DOWN * it["size"]
        taken.append((it["x"], (top + bottom) / 2, BADGE_HALF_W * it["size"], (bottom - top) / 2))
    out = []
    for text, label in tags:
        half_w, half_h = 0.4 * TAG_PX * len(text) + 4, TAG_PX / 2 + 4  # Arial Black runs about 0.8 em a letter
        lo, hi = 8 + half_w, WIDTH - 8 - half_w  # on screen, as the runtime keeps it

        def hits(x: float, y: float) -> list[Box]:
            return [b for b in taken if abs(b[0] - x) < half_w + b[2] and abs(b[1] - y) < half_h + b[3]]

        x = min(max((label["x"] - frame.x) * frame.s + WIDTH / 2, lo), hi)
        y = (label["y"] - frame.y) * frame.s + HEIGHT / 2
        for _ in range(len(taken) + 1):
            blocking = hits(x, y)
            if not blocking:
                break
            sides = (min(b[0] - b[2] for b in blocking) - half_w - TAG_GAP,
                     max(b[0] + b[2] for b in blocking) + half_w + TAG_GAP)
            beside = next((nx for nx in sides if lo <= nx <= hi and not hits(nx, y)), None)
            if beside is not None:
                x = beside
                break
            y = min(b[1] - b[3] for b in blocking) - half_h - TAG_GAP
        box = (x, y, half_w, half_h)
        taken.append(box)
        out.append(box)
    return out


def _main_body(shape: Shape) -> Shape:
    """Only the rings of a shape's main cluster: a state's far islands (Tokyo's reach a thousand km south)
    never pull its items out to sea."""
    x0, y0, x1, y1 = shape.main
    eps = 1e-9
    rings = [r for r in shape.rings if r[:, 0].min() >= x0 - eps and r[:, 0].max() <= x1 + eps
             and r[:, 1].min() >= y0 - eps and r[:, 1].max() <= y1 + eps]
    return Shape(rings=rings or shape.rings, main=shape.main, bbox=shape.main if rings else shape.bbox)


def build_regions(seg: ResolvedSegment, spec: Any, ctx: KindContext) -> Built:
    """A country's states: the country faint under every state border, each named state filling in its own
    colour when it is heard, its items laid out inside it and popping as they are said (never before it fills)."""
    country = seg.codes[0]
    built = Built(places=[_place(seg, country, fill_ms(ctx.fly_ms), ctx)])
    built.extras["countryFaint"] = True
    built.extras["borders"] = [ring for place in ctx.regions.in_country(country)
                               for ring in compose.place_rings(shape_of(place), ctx.landed)]
    # a region is heard on its `say`, its name, what the script wrote (unless a code: "US-CA" is not said)
    # or its first item, whichever comes first
    alternatives = []
    for entry, info in zip(spec.regions, seg.regions):
        written = None if entry.region.strip().upper() == info["code"] else entry.region
        first = (entry.items[0].say or entry.items[0].name) if entry.items else None
        alternatives.append([p for p in (entry.say, info["name"], written, first) if p])
    heard = match_any(alternatives, ctx.spoken)
    fills, spread = pop_times(heard, overrides=[None] * len(heard), speech_start_ms=ctx.speech_start,
                              speech_end_ms=ctx.speech_end)
    # no state fills before the faint country and its borders are there
    backdrop = built.places[0]["fillMs"]
    fills = [min(max(t, backdrop), ctx.last_ms) for t in fills]
    for k in spread:
        built.warnings.append(f"{seg.id} {seg.regions[k]['name']}: not heard in the narration; "
                              "its fill is spread evenly (add `say`)")
    floors = [fill for entry, fill in zip(spec.regions, fills) for _ in entry.items]
    items_in = _timed(seg, spec.items, ctx, built, floors=floors)
    regions, start = [], 0
    for entry, info, fill in zip(spec.regions, seg.regions, fills):
        place = ctx.regions.places[info["code"]]
        shape = shape_of(place)
        regions.append(compose.place_entry(info["code"], info["name"], info["colour"], shape, ctx.landed, fill,
                                           place.label))
        mine = items_in[start:start + len(entry.items)]
        start += len(entry.items)
        laid = compose.item_entries(mine, _main_body(shape), ctx.landed, max_size=REGION_ITEM_MAX,
                                    min_size=REGION_ITEM_MIN)
        for item in laid:  # a plate in the state's colour: a small state's item reads over sea or dark land
            item["plate"] = info["colour"]
        built.items += laid
        regions[-1]["tag"] = entry.say or tag_text(info["name"])  # the script's own words stay as written
        built.cues.append((fill, "pop"))
    built.cues.extend((it["atMs"], "pop") for it in built.items)
    boxes = tag_boxes([(r["tag"], r["label"]) for r in regions], built.items, ctx.landed)
    for region, (x, y, _, _) in zip(regions, boxes):
        region["anchor"] = region["label"]  # the region's own label point: a tag moved off it gets a leader
        region["label"] = {"x": ctx.landed.x + (x - WIDTH / 2) / ctx.landed.s,
                           "y": ctx.landed.y + (y - HEIGHT / 2) / ctx.landed.s}
    built.extras["regions"] = regions
    # at the cut the next segment fades out what was on screen: the faint country and the filled states
    built.shown = [{**built.places[0], "alpha": COUNTRY_FAINT}, *regions]
    built.resolved["regions"] = [{"code": r["code"], "name": r["name"], "colour": r["colour"], "fillMs": r["fillMs"]}
                                 for r in regions]
    return built


BUILDERS = {"country": build_country, "highlight": build_highlight, "rank": build_rank, "versus": build_versus,
            "regions": build_regions}


def build(seg: ResolvedSegment, spec: Any, ctx: KindContext) -> Built:
    return BUILDERS[spec.kind](seg, spec, ctx)
