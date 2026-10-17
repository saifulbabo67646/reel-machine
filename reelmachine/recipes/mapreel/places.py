# reelmachine/recipes/mapreel/places.py
"""Countries from Natural Earth, found by code or by any of their names."""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...config import Settings
from ...core.errors import InvalidInput
from . import geodata

Ring = tuple[tuple[float, float], ...]

#: names people use that Natural Earth does not carry (normalised key -> codes)
ALIASES: dict[str, tuple[str, ...]] = {
    "holland": ("NLD",), "uk": ("GBR",), "britain": ("GBR",), "greatbritain": ("GBR",),
    "england": ("GBR",), "scotland": ("GBR",), "wales": ("GBR",),
    "us": ("USA",), "usa": ("USA",), "america": ("USA",), "unitedstates": ("USA",),
    "korea": ("KOR", "PRK"), "congo": ("COD", "COG"), "turkiye": ("TUR",), "burma": ("MMR",),
    "ivorycoast": ("CIV",), "uae": ("ARE",), "czechrepublic": ("CZE",), "russia": ("RUS",),
    "vatican": ("VAT",), "southkorea": ("KOR",), "northkorea": ("PRK",),
}
_NAME_FIELDS = ("NAME", "NAME_LONG", "NAME_EN", "ADMIN", "FORMAL_EN", "BRK_NAME", "ABBREV", "NAME_ALT")


def norm(text: str) -> str:
    """'  The Netherlands ' -> 'netherlands'; accents and punctuation dropped."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold().strip()
    text = re.sub(r"^the\s+", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    polygons: tuple[tuple[Ring, ...], ...]
    label: tuple[float, float]
    level: str = "country"


def _code(props: dict[str, Any], adm0: set[str]) -> str:
    """The ISO code, unless the feature is a territory sharing its country's ISO code
    (Clipperton I. carries ISO_A3_EH 'FRA', Coral Sea Is. 'AUS'): those keep their own ADM0_A3."""
    code = props.get("ISO_A3_EH") or "-99"
    if code == "-99" or (code != props["ADM0_A3"] and code in adm0):
        return props["ADM0_A3"]
    return code


def _rings(geometry: dict[str, Any]) -> tuple[tuple[Ring, ...], ...]:
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return tuple(tuple(tuple((float(x), float(y)) for x, y, *_ in ring) for ring in poly) for poly in polys)


class Atlas:
    def __init__(self, places: dict[str, Place], keys: dict[str, set[str]]) -> None:
        self.places = places
        self.keys = keys  # normalised name/code -> codes

    @staticmethod
    def load(path: Path) -> "Atlas":
        path = Path(path)
        return _load(str(path), path.stat().st_mtime_ns)

    def resolve(self, query: str) -> Place:
        key = norm(query)
        raw = (query or "").strip()
        if raw.upper() in self.places:  # an exact code wins
            return self.places[raw.upper()]
        codes = sorted(self.keys.get(key, set()))
        if len(codes) == 1:
            return self.places[codes[0]]
        if len(codes) > 1:
            names = ", ".join(f"{c} ({self.places[c].name})" for c in codes)
            raise InvalidInput(f"{query!r} could mean more than one country", hint=f"use one of: {names}",
                               details={"query": query, "candidates": codes})
        names = sorted({p.name for p in self.places.values()})
        close = difflib.get_close_matches(query.strip().title(), names, n=5, cutoff=0.6)
        raise InvalidInput(f"no country called {query!r}",
                           hint=f"did you mean {', '.join(close)}?" if close else "use an ISO code like 'ITA' (find_places)",
                           details={"query": query, "suggestions": close})

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        key = norm(query)
        scored: dict[str, float] = {}
        for name, codes in self.keys.items():
            if not key:
                break
            if name == key:
                score = 3.0
            elif name.startswith(key):
                score = 2.0
            elif key in name:
                score = 1.0
            else:
                ratio = difflib.SequenceMatcher(None, key, name).ratio()
                score = ratio if ratio >= 0.75 else 0.0
            for code in codes:
                if score and code in self.places:
                    scored[code] = max(scored.get(code, 0.0), score)
        exact = self.keys.get(key, set())
        ranked = sorted(scored, key=lambda c: (-scored[c], self.places[c].name))[:limit]
        return [{"code": c, "name": self.places[c].name, "level": "country",
                 "ambiguous": len(exact) > 1 and c in exact} for c in ranked]


@lru_cache(maxsize=4)
def _load(path: str, _mtime: int) -> Atlas:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    places: dict[str, Place] = {}
    keys: dict[str, set[str]] = {}
    adm0 = {f["properties"]["ADM0_A3"] for f in data["features"] if f.get("geometry")}
    for feature in data["features"]:
        props, geometry = feature["properties"], feature.get("geometry")
        if not geometry:
            continue
        code = _code(props, adm0)
        iso = props.get("ISO_A3_EH")
        # a territory must not answer to its country's codes; a place with no ISO-3 (Kosovo, XK) keeps its ISO-2
        owns_iso = code == iso or iso in (None, "", "-99")
        label = (float(props.get("LABEL_X") or 0.0), float(props.get("LABEL_Y") or 0.0))
        places[code] = Place(code=code, name=props.get("NAME") or code, polygons=_rings(geometry), label=label)
        for field in ("ISO_A3_EH", "ADM0_A3", "ISO_A2_EH") if owns_iso else ("ADM0_A3",):
            value = props.get(field)
            if value and value != "-99":
                keys.setdefault(norm(value), set()).add(code)
        for field in _NAME_FIELDS:
            if props.get(field):
                keys.setdefault(norm(props[field]), set()).add(code)
    for alias, codes in ALIASES.items():  # aliases replace whatever a short code collided with
        present = {c for c in codes if c in places}
        if present:
            keys[alias] = present
    return Atlas(places, keys)


def atlas_for(settings: Settings) -> Atlas:
    return Atlas.load(geodata.ensure(geodata.geo_cache(settings), "countries"))
