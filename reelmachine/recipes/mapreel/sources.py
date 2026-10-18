"""sources.md: what a reel's description needs, namely each claim's source, each image's licence and the credits."""

from __future__ import annotations

from ...core.assets import Licence
from ..explainer3d.markup import parse_markup, spoken_text
from .models import MapReelInputs, ResolvedSegment

CLAIM_CHARS = 120
NO_SOURCES = "- No sources were given for the claims in this reel."
NO_IMAGES = "- No images (items are shown as text badges)."
NATURAL_EARTH_URL = "https://www.naturalearthdata.com/"
BLUE_MARBLE_URL = "https://visibleearth.nasa.gov/"


def _name(code_or_query: str, seg: ResolvedSegment | None) -> str:
    if seg is not None and code_or_query in seg.names:
        return seg.names[code_or_query]
    return code_or_query


def _label(spec, seg: ResolvedSegment | None) -> str:
    """`#N <country>` for rank, `<A> vs <B>` for versus, the country name otherwise."""
    codes = list(seg.codes) if seg is not None and seg.codes else None
    if spec.kind == "versus":
        names = [_name(c, seg) for c in codes] if codes else list(spec.countries)
        return " vs ".join(names)
    if spec.kind == "highlight":
        names = [_name(c, seg) for c in codes] if codes else list(spec.countries)
        return ", ".join(names)
    country = _name(codes[0], seg) if codes else spec.country
    return f"#{spec.rank} {country}" if spec.kind == "rank" else country


def _stat_text(stat) -> str:
    return f"{stat.value} {stat.label}" if stat.label else stat.value


def _claims(spec) -> list[tuple[str, str]]:
    """(url, claim text) in script order: the segment's own source first, then its stats, then its items."""
    found: list[tuple[str, str]] = []
    if spec.source:
        text = spoken_text(parse_markup(spec.narration)).strip()[:CLAIM_CHARS].rstrip()
        found.append((spec.source, text))
    stats = [spec.stat] if getattr(spec, "stat", None) else list(getattr(spec, "stats", []) or [])
    found += [(s.source, _stat_text(s)) for s in stats if s.source]
    found += [(i.source, i.name) for i in getattr(spec, "items", []) if i.source]
    return found


def _image_order(key: str) -> tuple[int, int]:
    seg, _, index = key.partition("/")
    tail = seg.rpartition("-")[2]
    return (int(tail) if tail.isdigit() else 0, int(index) if index.isdigit() else 0)


def render(inputs: MapReelInputs, segments_ids: list[str], image_credits: dict[str, dict], *,
           voice_licence: str, data: dict[str, Licence], look: str = "satellite",
           resolved: list[ResolvedSegment] | None = None) -> str:
    """The reel's sources as Markdown. `data` holds the map data licences by key (`countries`, `world`);
    `resolved` gives the country names the script stage found (the written place is used without it)."""
    by_id = {seg.id: seg for seg in resolved or []}
    lines = [f"# Sources — {inputs.title}", "", "## Claims"]
    claims: list[str] = []
    for seg_id, spec in zip(segments_ids, inputs.segments):
        merged: dict[str, list[str]] = {}
        for url, text in _claims(spec):
            texts = merged.setdefault(url, [])
            if text not in texts:
                texts.append(text)
        label = _label(spec, by_id.get(seg_id))
        claims += [f"- {label}: {', '.join(texts)} — {url}" for url, texts in merged.items()]
    lines += claims or [NO_SOURCES]

    lines += ["", "## Images"]
    images = []
    for key in sorted(image_credits, key=_image_order):
        c = image_credits[key]
        line = f"- {c['name']}: {c['licence']} — {c.get('attribution') or 'no credit given'} ({c['id_or_path']})"
        if c.get("restrictions"):
            line += f" — {c['restrictions']}"
        images.append(line)
    lines += images or [NO_IMAGES]

    lines += ["", "## Map data and voice"]
    lines.append(f"- Natural Earth — {data['countries'].name} — {NATURAL_EARTH_URL}")
    if look == "satellite":
        lines.append(f"- NASA Blue Marble — {data['world'].name} — {BLUE_MARBLE_URL}")
    lines.append(f"- Narration — {inputs.voice} — {voice_licence}")
    return "\n".join(lines) + "\n"
