"""Casts: the pair of characters, content-hashed so a reel can reuse one, kept per caller."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from reelmachine.config import Settings
from reelmachine.core.errors import InvalidInput
from reelmachine.core.stage import StageContext
from reelmachine.recipes.dialoguereel.images import POSES
from reelmachine.recipes.dialoguereel.library import load_builtin
from reelmachine.recipes.dialoguereel.models import CastSpec, ResolvedCast, ResolvedCharacter
from reelmachine.recipes.dialoguereel.svg import check_svg

__all__ = ["CAST_ID", "ResolvedCast", "ResolvedCharacter", "caller_root", "cast_folder", "cast_id", "load",
           "poses_on_disk", "resolve", "save"]

CAST_ID = re.compile(r"^cast_[0-9a-f]{12}$")


def cast_id(spec: CastSpec) -> str:
    """A hash of how the characters look and are named; voices are not part of it."""
    looks = [{"id": c.id, "name": c.name, "side": c.side, "source": c.source.model_dump(mode="json")}
             for c in spec.characters]
    digest = hashlib.sha256(json.dumps(looks, sort_keys=True).encode("utf-8")).hexdigest()
    return "cast_" + digest[:12]


def resolve(spec: CastSpec, *, path: str = "cast") -> ResolvedCast:
    """Load and check every character. Errors carry `<path>.characters[i]...` (no prefix when `path` is empty)."""
    prefix = f"{path}." if path else ""
    out: list[ResolvedCharacter] = []
    for index, char in enumerate(spec.characters):
        where = f"{prefix}characters[{index}].source"
        source = char.source
        if source.kind == "image":   # its poses are drawn (or fetched) by the cast stage, not here
            parts = list(POSES) if source.provider else [p for p in POSES if p in source.poses]
            out.append(ResolvedCharacter(id=char.id, name=char.name, voice=char.voice, side=char.side, kind="image",
                                         parts=parts, provider=source.provider or ""))
            continue
        try:
            if source.kind == "builtin":
                where += ".name"
                svg = load_builtin(source.name, source.colors)
            else:
                svg = source.svg
            where = f"{prefix}characters[{index}].source.svg"
            parts = check_svg(svg, path=where)
        except InvalidInput as exc:
            exc.details = {**exc.details, "path": where}
            raise
        out.append(ResolvedCharacter(id=char.id, name=char.name, voice=char.voice, side=char.side,
                                     svg=svg, parts=parts))
    return ResolvedCast(id=cast_id(spec), characters=out)


def cast_folder(root: Path, cast_id: str, char_id: str) -> Path:
    """Where an image character's poses live."""
    return Path(root) / "casts" / cast_id / char_id


def poses_on_disk(root: Path, resolved: ResolvedCast) -> int:
    """The image calls still needed: generated poses missing from disk (supplied poses cost none)."""
    return sum(not (cast_folder(root, resolved.id, c.id) / f"{pose}.png").exists()
               for c in resolved.characters if c.kind == "image" and c.provider for pose in POSES)


def _write_atomic(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
        tmp.write(data)
    try:
        os.replace(tmp.name, path)
    except OSError:
        Path(tmp.name).unlink(missing_ok=True)
        raise


def save(root: Path, spec: CastSpec) -> str:
    ident = cast_id(spec)
    folder = Path(root) / "casts"
    folder.mkdir(parents=True, exist_ok=True)
    _write_atomic(folder / f"{ident}.json", spec.model_dump_json(indent=2).encode("utf-8"))
    return ident


def load(root: Path, ident: str) -> CastSpec:
    path = Path(root) / "casts" / f"{ident}.json"
    if not CAST_ID.match(ident) or not path.is_file():
        raise InvalidInput(f"there is no saved cast {ident!r}", hint="call make_cast first, or pass the cast inline",
                           details={"path": "cast"})
    return CastSpec.model_validate_json(path.read_text(encoding="utf-8"))


def caller_root(ctx: StageContext) -> Path:
    """The caller's directory (casts live in `<root>/casts/`); outside the engine, the cache dir."""
    settings: Settings = ctx.config
    job_id = getattr(ctx.job, "id", "")
    if job_id and settings.workdir.name == job_id:
        return settings.workdir.parent
    return settings.cache_dir
