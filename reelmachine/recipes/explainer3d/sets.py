"""Sets are data. A preset is a set saved as JSON in `sets/`; a script names one, changes
it, or builds a set from parts — sky, floor, walls, stage, water, fog, props, marks."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...core.errors import InvalidInput
from .models import ResolvedSet, SetSpec

SETS_DIR = Path(__file__).resolve().parent / "sets"
DEFAULT_PRESET = "meadow-day"


@lru_cache(maxsize=None)
def _presets() -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(SETS_DIR.glob("*.json"))}


PRESETS = _presets()


def resolve_set(spec: SetSpec | dict[str, Any] | None, base: ResolvedSet | None = None, *, path: str = "set") -> ResolvedSet:
    """A preset (or `base`, the reel's set, when no preset is named) with this spec's
    fields on top: scalars replace, props add, marks merge."""
    spec = SetSpec.model_validate(spec or {})
    name = spec.preset or spec.environment
    if name is not None:
        if name not in PRESETS:
            raise InvalidInput(f"unknown set preset {name!r}", hint=f"one of {sorted(PRESETS)}, or build a set from parts",
                               details={"path": f"{path}.preset"})
        start = ResolvedSet.model_validate({"name": name, **PRESETS[name]})
    elif base is not None:
        start = base
    else:
        given = spec.model_dump(exclude_none=True, exclude={"props", "marks"})
        start = ResolvedSet.model_validate({"name": "custom" if given else DEFAULT_PRESET,
                                            **({} if given else PRESETS[DEFAULT_PRESET])})
    patch = spec.model_dump(exclude_none=True, exclude={"preset", "environment", "props", "marks"})
    merged = start.model_copy(update={
        **{k: getattr(spec, k) for k in patch},
        "props": list(start.props) + list(spec.props),
        "marks": {**start.marks, **spec.marks},
    })
    return ResolvedSet.model_validate(merged.model_dump())


def reel_sets(inputs: Any) -> tuple[ResolvedSet, list[ResolvedSet]]:
    """The reel's set and each shot's (a shot without a set uses the reel's)."""
    reel = resolve_set(inputs.set)
    shots = [resolve_set(shot.set, reel, path=f"shots[{i}].set") if shot.set is not None else reel
             for i, shot in enumerate(inputs.shots)]
    return reel, shots


def all_marks(inputs: Any) -> dict[str, tuple[float, float, float]]:
    reel, shots = reel_sets(inputs)
    marks: dict[str, tuple[float, float, float]] = {}
    for s in [reel, *shots]:
        marks.update(s.marks)
    return marks


def stage_anchors(inputs: Any) -> dict[str, tuple[float, float, float]]:
    """Every anchor a script may name: the stage's marks (from the reel set's stage) and
    the marks of every set it uses."""
    from .geometry import anchors

    reel, _ = reel_sets(inputs)
    return {**anchors(reel.stage), **all_marks(inputs)}
