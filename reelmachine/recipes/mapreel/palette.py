# reelmachine/recipes/mapreel/palette.py
"""Vivid fills that read on a satellite map: no two alike on screen or across a cut."""

from __future__ import annotations

#: the reference's purple, red, blue, orange, magenta, green … then more
PALETTE = ["#8a2be2", "#e0201b", "#3333ff", "#f28c0f", "#e01ec8", "#35b52a",
           "#00b3c7", "#ffcc00", "#ff5a7a", "#1e90ff", "#7cdf00", "#b8005c"]


def assign(groups: list[list[str]], fixed: dict[str, str] | None = None) -> dict[str, str]:
    """`groups` are the codes on screen together, in reel order. A code keeps its colour for
    the whole reel; a new code takes the first palette colour not used by its own group or
    the groups either side of it (fixed colours included), cycling the start so consecutive reels of one country vary."""
    colours: dict[str, str] = dict(fixed or {})
    cursor = 0
    for i, group in enumerate(groups):
        for code in group:
            if code in colours:
                continue
            taken = {colours[c] for j in (i - 1, i, i + 1) if 0 <= j < len(groups)
                     for c in groups[j] if c in colours}
            for step in range(len(PALETTE)):
                candidate = PALETTE[(cursor + step) % len(PALETTE)]
                if candidate not in taken:
                    break
            colours[code] = candidate
            cursor = (PALETTE.index(candidate) + 1) % len(PALETTE)
    return colours
