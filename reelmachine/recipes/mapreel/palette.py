# reelmachine/recipes/mapreel/palette.py
"""Vivid fills that read on a satellite map: no two alike on screen or across a cut."""

from __future__ import annotations

import colorsys

#: the reference's purple, red, blue, orange, magenta, green … then more
PALETTE = ["#8a2be2", "#e0201b", "#3333ff", "#f28c0f", "#e01ec8", "#35b52a",
           "#00b3c7", "#ffcc00", "#ff5a7a", "#1e90ff", "#7cdf00", "#b8005c"]
GOLDEN_DEG = 137.508  # successive hues a golden angle apart never bunch up


def extra(n: int) -> str:
    """The n-th colour past the palette: vivid (85 % saturation, 50 % lightness), hues spread."""
    r, g, b = colorsys.hls_to_rgb((GOLDEN_DEG * n) % 360 / 360, 0.5, 0.85)
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (r, g, b)))


def assign(groups: list[list[str]], fixed: dict[str, str] | None = None) -> dict[str, str]:
    """`groups` are the codes on screen together, in reel order. A code keeps its colour for
    the whole reel; a new code takes the first palette colour not used by its own group or
    the groups either side of it (fixed colours included), cycling the start so consecutive reels of one country vary.
    When all twelve are taken, a new colour is made, so a group never repeats one."""
    colours: dict[str, str] = dict(fixed or {})
    cursor = extras = 0
    for i, group in enumerate(groups):
        for code in group:
            if code in colours:
                continue
            taken = {colours[c] for j in (i - 1, i, i + 1) if 0 <= j < len(groups)
                     for c in groups[j] if c in colours}
            ring = (PALETTE[(cursor + step) % len(PALETTE)] for step in range(len(PALETTE)))
            free = [c for c in ring if c not in taken]
            if free:
                colours[code] = free[0]
                cursor = (PALETTE.index(free[0]) + 1) % len(PALETTE)
                continue
            candidate = extra(extras)  # all twelve on screen: make a new one
            extras += 1
            while candidate in taken:
                candidate = extra(extras)
                extras += 1
            colours[code] = candidate
    return colours
