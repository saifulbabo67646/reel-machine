"""Line art helpers: deterministic sketch generation for tests and dry runs."""

from __future__ import annotations

import hashlib
from pathlib import Path


def art_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def sketch(path: Path, *, size: tuple[int, int], keyword: str, seed: int = 0) -> Path:
    """Draw a deterministic piece of line art (a card, the keyword, a small figure)."""
    from PIL import Image, ImageDraw

    width, height = size
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    ink = (20, 20, 20)
    digest = hashlib.sha256(f"{keyword}|{seed}".encode("utf-8")).digest()
    wobble = digest[0] % 7

    margin = int(min(width, height) * 0.08)
    draw.rounded_rectangle(
        (margin, margin, width - margin, height - margin), radius=36, outline=ink, width=6
    )
    centre_x, centre_y = width // 2, height // 2
    radius = int(min(width, height) * 0.18) + wobble
    draw.ellipse(
        (centre_x - radius, centre_y - radius, centre_x + radius, centre_y + radius),
        outline=ink,
        width=6,
    )
    eye = max(4, radius // 8)
    for side in (-1, 1):
        ex = centre_x + side * radius // 2
        draw.ellipse((ex - eye, centre_y - eye, ex + eye, centre_y + eye), fill=ink)
    draw.arc(
        (centre_x - radius // 2, centre_y, centre_x + radius // 2, centre_y + radius // 2),
        start=20,
        end=160,
        fill=ink,
        width=5,
    )
    baseline = height - margin * 3
    draw.line((margin * 2, baseline, width - margin * 2, baseline), fill=ink, width=5)
    for index, character in enumerate(keyword[:12]):
        x = margin * 2 + index * int(min(width, height) * 0.05)
        draw.line((x, baseline - 6, x + 12 + (ord(character) % 9), baseline - 34), fill=ink, width=4)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path
