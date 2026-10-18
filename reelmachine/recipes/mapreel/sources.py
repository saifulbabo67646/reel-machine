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


def _clean(text: object) -> str:
    """One line, and never a line that starts a heading, a bullet, a quote or emphasis."""
    line = " ".join(str(text).split())
    return "\\" + line if line[:1] in ("#", "-", ">", "*") else line


def _link(ref: str) -> str:
    """An http(s) URL as it is; anything else (a local path, a stray string) as code, never a link."""
    line = " ".join(str(ref).split())
    if line.startswith(("http://", "https://")):
        return line.replace("<", "%3C").replace(">", "%3E")
    return "`" + line.replace("`", "'") + "`"


def _name(code_or_query: str, seg: ResolvedSegment | None) -> str:
    if seg is not None and code_or_query in seg.names:
        return seg.names[code_or_query]
    return code_or_query


def _label(spec, seg: ResolvedSegment | None) -> str:
    """`#N <country>` for rank, `<A> vs <B>` for versus, the country name otherwise."""
    codes = list(seg.codes) if seg is not None and seg.codes else None
    if spec.kind == "versus":
        names = [_name(c, seg) for c in codes] if codes else list(spec.countries)
        return " vs ".join(_clean(n) for n in names)
    if spec.kind == "highlight":
        names = [_name(c, seg) for c in codes] if codes else list(spec.countries)
        return ", ".join(_clean(n) for n in names)
    country = _clean(_name(codes[0], seg) if codes else spec.country)
    return f"#{spec.rank} {country}" if spec.kind == "rank" else country


def _stat_text(stat) -> str:
    return f"{stat.value} {stat.label}" if stat.label else stat.value


def _cut(text: str) -> str:
    """At most CLAIM_CHARS, ending on a word boundary with an ellipsis when it was cut."""
    text = " ".join(text.split())
    if len(text) <= CLAIM_CHARS:
        return text
    head = text[:CLAIM_CHARS]
    if text[CLAIM_CHARS] != " " and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip() + "…"


def _claims(spec, countries: list[str]) -> list[tuple[str, str]]:
    """(url, claim text) in script order: the segment's own source first, then its stats, then its items."""
    found: list[tuple[str, str]] = []
    if spec.source:
        found.append((spec.source, _cut(spoken_text(parse_markup(spec.narration)))))
    if getattr(spec, "stat", None) and spec.stat.source:
        found.append((spec.stat.source, _stat_text(spec.stat)))
    for index, stat in enumerate(getattr(spec, "stats", []) or []):  # versus: one per country
        if stat.source:
            who = countries[index] if index < len(countries) else None
            found.append((stat.source, f"{_clean(who)}: {_stat_text(stat)}" if who else _stat_text(stat)))
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
    lines = [f"# Sources — {_clean(inputs.title)}", "", "## Claims"]
    claims: list[str] = []
    for seg_id, spec in zip(segments_ids, inputs.segments):
        merged: dict[str, list[str]] = {}
        seg = by_id.get(seg_id)
        countries = ([_name(c, seg) for c in seg.codes] if seg is not None and seg.codes
                     else list(getattr(spec, "countries", [])))
        for url, text in _claims(spec, countries):
            texts = merged.setdefault(_link(url), [])
            text = _clean(text).rstrip(".")
            if text not in texts:
                texts.append(text)
        label = _label(spec, seg)
        claims += [f"- {label}: {'; '.join(texts)} — {url}" for url, texts in merged.items()]
    lines += claims or [NO_SOURCES]

    lines += ["", "## Images"]
    images = []
    for key in sorted(image_credits, key=_image_order):
        c = image_credits[key]
        line = (f"- {_clean(c['name'])}: {_clean(c['licence'])} — "
                f"{_clean(c.get('attribution') or 'no credit given')} ({_link(c['id_or_path'])})")
        if c.get("restrictions"):
            line += f" — {_clean(c['restrictions'])}"
        images.append(line)
    lines += images or [NO_IMAGES]

    lines += ["", "## Map data and voice"]
    lines.append(f"- Natural Earth — {data['countries'].name} — {NATURAL_EARTH_URL}")
    if look == "satellite":
        lines.append(f"- NASA Blue Marble — {data['world'].name} — {BLUE_MARBLE_URL}")
    lines.append(f"- Narration — {_clean(inputs.voice)} — {_clean(voice_licence)}")
    return "\n".join(lines) + "\n"
