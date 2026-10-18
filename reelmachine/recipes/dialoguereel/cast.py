"""Casts: the pair of characters, content-hashed so a reel can reuse one, kept per caller."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from reelmachine.config import Settings
from reelmachine.core.errors import InvalidInput
from reelmachine.core.stage import StageContext
from reelmachine.recipes.dialoguereel.library import load_builtin
from reelmachine.recipes.dialoguereel.models import CastSpec
from reelmachine.recipes.dialoguereel.svg import check_svg

CAST_ID = re.compile(r"^cast_[0-9a-f]{12}$")


class ResolvedCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    voice: str
    side: str
    svg: str                      # recoloured, not yet scoped
    parts: list[str]


class ResolvedCast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    characters: list[ResolvedCharacter]


def cast_id(spec: CastSpec) -> str:
    """A hash of how the characters look and are named; voices are not part of it."""
    looks = [{"id": c.id, "name": c.name, "side": c.side, "source": c.source.model_dump(mode="json")}
             for c in spec.characters]
    digest = hashlib.sha256(json.dumps(looks, sort_keys=True).encode("utf-8")).hexdigest()
    return "cast_" + digest[:12]


def resolve(spec: CastSpec, *, path: str = "cast") -> ResolvedCast:
    out: list[ResolvedCharacter] = []
    for index, char in enumerate(spec.characters):
        where = f"{path}.characters[{index}].source"
        source = char.source
        try:
            if source.kind == "builtin":
                where += ".name"
                svg = load_builtin(source.name, source.colors)
            else:
                svg = source.svg
            where = f"{path}.characters[{index}].source.svg"
            parts = check_svg(svg, path=where)
        except InvalidInput as exc:
            exc.details = {**exc.details, "path": where}
            raise
        out.append(ResolvedCharacter(id=char.id, name=char.name, voice=char.voice, side=char.side,
                                     svg=svg, parts=parts))
    return ResolvedCast(id=cast_id(spec), characters=out)


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
