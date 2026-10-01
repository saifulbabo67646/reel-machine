"""Content-addressed assets with provenance and licence metadata.

An asset is a file plus everything a hosted product needs to answer "where did this come
from": which provider produced it, from which source, at which version, under which
licence. Storing the same bytes twice is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AssetKind(StrEnum):
    SOURCE = "source"
    SOURCE_CUT = "source_cut"
    SCENE_TRACK = "scene_track"
    NARRATION = "narration"
    MUSIC = "music"
    BACKGROUND = "background"
    LINE_ART = "line_art"
    IMAGE = "image"
    STROKE_MASK = "stroke_mask"
    VIDEO = "video"
    SUBTITLE = "subtitle"
    FONT = "font"
    TIMELINE = "timeline"
    PLAN = "plan"
    MANIFEST = "manifest"
    REPORT = "report"
    OTHER = "other"


class Provenance(BaseModel):
    """Where an asset came from and what produced it."""

    model_config = ConfigDict(extra="forbid")

    provider: str = ""
    provider_version: str = ""
    source: str = ""
    source_id: str = ""
    upstream: str = ""
    notes: str = ""


class Licence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    url: str = ""
    attribution: str = ""
    share_alike: bool = False


class Asset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: AssetKind
    path: str  # relative to the asset store root
    sha256: str
    bytes: int
    mime: str = ""
    provenance: Provenance = Field(default_factory=Provenance)
    licence: Licence | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    def resolved(self, root: Path) -> Path:
        return root / self.path


class AssetNotFound(KeyError):
    pass


def digest_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _sidecar(root: Path, asset_id: str) -> Path:
    # metadata lives in its own namespace so a `.json` blob can never collide with it
    return root / "_meta" / asset_id[:2] / f"{asset_id}.json"


class AssetStore:
    """Local, content-addressed asset store.

    Layout::

        <root>/<id[:2]>/<id><ext>      the bytes
        <root>/<id[:2]>/<id>.json      kind, provenance, licence, …

    `id` is the first 32 hex characters of the sha256 of the content, so identical bytes
    are stored once regardless of which job or recipe produced them.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- writing ---------------------------------------------------------------
    def put(
        self,
        source: Path,
        *,
        kind: AssetKind,
        provenance: Provenance | None = None,
        licence: Licence | None = None,
        ext: str | None = None,
        move: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> Asset:
        """Store `source` and return its asset record (idempotent)."""
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        full = digest_file(source)
        asset_id = full[:32]
        suffix = ext if ext is not None else source.suffix
        rel = f"{asset_id[:2]}/{asset_id}{suffix}"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            if move:
                os.replace(source, dest)
            else:
                shutil.copy2(source, dest)
        asset = Asset(
            id=asset_id,
            kind=kind,
            path=rel,
            sha256=full,
            bytes=dest.stat().st_size,
            mime=mimetypes.guess_type(dest.name)[0] or "",
            provenance=provenance or Provenance(),
            licence=licence,
            meta=dict(meta or {}),
        )
        self._write_sidecar(asset)
        return asset

    def write_bytes(
        self,
        data: bytes,
        *,
        kind: AssetKind,
        ext: str = ".bin",
        provenance: Provenance | None = None,
        licence: Licence | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Asset:
        """Store `data` — for small generated artifacts (JSON, SRT, …)."""
        full = hashlib.sha256(data).hexdigest()
        asset_id = full[:32]
        rel = f"{asset_id[:2]}/{asset_id}{ext}"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(data)
        asset = Asset(
            id=asset_id,
            kind=kind,
            path=rel,
            sha256=full,
            bytes=len(data),
            mime=mimetypes.guess_type(dest.name)[0] or "",
            provenance=provenance or Provenance(),
            licence=licence,
            meta=dict(meta or {}),
        )
        self._write_sidecar(asset)
        return asset

    def _write_sidecar(self, asset: Asset) -> None:
        path = _sidecar(self.root, asset.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(asset.model_dump_json(indent=2), encoding="utf-8")

    # -- reading ---------------------------------------------------------------
    def metadata(self, asset_id: str) -> Asset | None:
        path = _sidecar(self.root, asset_id)
        if not path.is_file():
            return None
        return Asset.model_validate_json(path.read_text(encoding="utf-8"))

    def get(self, asset_id: str) -> Path:
        asset = self.metadata(asset_id)
        if asset is None:
            raise AssetNotFound(asset_id)
        return self.root / asset.path

    def all(self) -> list[Asset]:
        assets: list[Asset] = []
        meta_root = self.root / "_meta"
        if not meta_root.is_dir():
            return assets
        for sidecar in sorted(meta_root.glob("*/*.json")):
            try:
                assets.append(Asset.model_validate_json(sidecar.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, ValueError):
                continue
        return assets


def provenance_dict(provenance: Provenance | None) -> dict[str, Any]:
    return (provenance or Provenance()).model_dump()
