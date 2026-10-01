"""Style packs: named presets carrying licence and provenance metadata.

Neutral defaults only. Third-party visual IP may arrive through a style pack that
declares its licence; it is never hard-coded in core.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .assets import Licence, Provenance


class StylePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = ""
    summary: str = ""
    render_modes: list[str] = Field(default_factory=list)
    aspect: str | None = None
    palette: dict[str, str] = Field(default_factory=dict)
    fonts: dict[str, str] = Field(default_factory=dict)
    params: dict[str, object] = Field(default_factory=dict)
    licence: Licence
    provenance: Provenance = Field(default_factory=Provenance)


class StyleNotFound(KeyError):
    pass


def styles_root() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "styles"


def load_style_pack(path: Path) -> StylePack:
    return StylePack.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def bundled_styles(root: Path | None = None) -> dict[str, StylePack]:
    directory = Path(root) if root is not None else styles_root()
    packs: dict[str, StylePack] = {}
    if not directory.is_dir():
        return packs
    for path in sorted(directory.glob("*.json")):
        try:
            pack = load_style_pack(path)
        except (json.JSONDecodeError, ValueError):
            continue
        packs[pack.id] = pack
    return packs


def get_style(style_id: str, root: Path | None = None) -> StylePack:
    packs = bundled_styles(root)
    try:
        return packs[style_id]
    except KeyError as exc:
        raise StyleNotFound(
            f"unknown style {style_id!r} (available: {', '.join(sorted(packs)) or 'none'})"
        ) from exc
