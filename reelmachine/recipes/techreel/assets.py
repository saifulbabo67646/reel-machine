"""techreel's caller images and agent drawings.

Images (`image` visuals, an `image` hero and the creator's avatar) are fetched once with dialoguereel's guarded download (a remote
caller reaches public addresses only; local paths are read for local runs alone), normalised to PNGs at most
1600 px on the longest side and named by the hash of their `src`. Drawings (`svg` visuals and an `svg` hero) pass
dialoguereel's SVG safety rules; compose inlines them scoped by `inline_svg`, which checks them again.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

from ...core.errors import InvalidInput
from ..dialoguereel.images import LOCAL_REFUSED, read_image
from ..dialoguereel.svg import check_drawing
from ..mapreel.stages import _write_atomic
from .models import TechReelInputs

MAX_SIDE = 1600   # a screenshot stays sharp at the stage's full width (952 px) and well past it


def image_name(src: str) -> str:
    return f"img-{hashlib.sha256(src.encode('utf-8')).hexdigest()[:16]}.png"


def image_sources(inputs: TechReelInputs) -> Iterator[tuple[str, str]]:
    """Every image `src` with the path of its first use, each once: the hero's, the visuals' in order, then the
    creator's avatar (the follow card draws it)."""
    seen: set[str] = set()
    found = [("hero.src", inputs.hero.src)] if inputs.hero and inputs.hero.kind == "image" else []
    found += [(f"beats[{b}].visuals[{v}].src", visual.src)
              for b, beat in enumerate(inputs.beats) for v, visual in enumerate(beat.visuals) if visual.kind == "image"]
    if inputs.creator and inputs.creator.avatar:
        found.append(("creator.avatar", inputs.creator.avatar))
    for path, src in found:
        if src is not None and src not in seen:
            seen.add(src)
            yield path, src


def _is_url(src: str) -> bool:
    return urlsplit(src).scheme.lower() in ("http", "https")


def is_local(src: str) -> bool:
    """Whether `src` names a file on this machine: no scheme, or a one-letter one (a Windows drive, "C:\\a.png")."""
    return len(urlsplit(src).scheme) <= 1


def check_sources(inputs: TechReelInputs, *, allow_local: bool) -> None:
    """What can be said of the images without fetching them: a URL is http(s), a path needs local files."""
    for path, src in image_sources(inputs):
        if _is_url(src):
            continue
        if not is_local(src):
            scheme = urlsplit(src).scheme.lower()
            raise InvalidInput(f"an image is an https URL or a local path, not a {scheme}: URL",
                               details={"path": path})
        if not allow_local:
            raise InvalidInput(LOCAL_REFUSED, details={"path": path})


def fetch_images(inputs: TechReelInputs, folder: Path, *, allow_local: bool,
                 cache: Path | None = None) -> dict[str, str]:
    """Write each distinct image into `folder` as `img-<hash>.png`: {src: file name}. With `cache`, URLs are kept
    there by the same name and read from it again (local files are always read, they may have changed)."""
    check_sources(inputs, allow_local=allow_local)
    folder = Path(folder)
    names: dict[str, str] = {}
    for path, src in image_sources(inputs):
        name = image_name(src)
        target = folder / name
        kept = cache / name if cache is not None and _is_url(src) else None
        folder.mkdir(parents=True, exist_ok=True)
        if kept is not None and kept.is_file():
            shutil.copy2(kept, target)
        else:
            data = read_image(src, allow_local=allow_local, path=path, what="image", trim=False,
                              max_side=MAX_SIDE)
            _write_atomic(target, data)
            if kept is not None:
                kept.parent.mkdir(parents=True, exist_ok=True)
                _write_atomic(kept, data)
        names[src] = name
    return names


def check_svgs(inputs: TechReelInputs) -> None:
    """Every drawing against dialoguereel's SVG safety rules; InvalidInput names the first that breaks them."""
    if inputs.hero and inputs.hero.svg is not None:
        check_drawing(inputs.hero.svg, path="hero.svg")
    for b, beat in enumerate(inputs.beats):
        for v, visual in enumerate(beat.visuals):
            if visual.kind == "svg":
                check_drawing(visual.svg, path=f"beats[{b}].visuals[{v}].svg")
