"""The built-in character library: hand-drawn SVGs shipped with the recipe."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from importlib import resources

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.dialoguereel.svg import recolour

_PACKAGE = "reelmachine.recipes.dialoguereel"


@cache
def _index() -> tuple[dict, ...]:
    text = resources.files(_PACKAGE).joinpath("characters/index.json").read_text(encoding="utf-8")
    return tuple(json.loads(text))


def builtin_names() -> list[dict]:
    """Every built-in character: its name, a one-line description and a suggested role."""
    return [{"name": e["name"], "description": e["description"], "role": e["role"]} for e in _index()]


@cache
def _svg(name: str) -> str:
    return resources.files(_PACKAGE).joinpath(f"characters/{name}.svg").read_text(encoding="utf-8")


def load_builtin(name: str, colors: Mapping[str, str]) -> str:
    """The named built-in's SVG, recoloured. The name is looked up, never used as a path."""
    names = [entry["name"] for entry in _index()]
    if name not in names:
        raise InvalidInput(f"there is no built-in character {name!r}; the built-ins are {', '.join(names)}",
                           hint="pick a built-in name, or supply your own character as {\"kind\": \"svg\"}",
                           details={"path": "source.name", "names": names})
    return recolour(_svg(name), colors)
