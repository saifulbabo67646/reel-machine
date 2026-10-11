"""Retention lint: what keeps a viewer to the end, checked before anything is rendered.

Warnings, not refusals — the agent decides. Each one says what to change.
"""

from __future__ import annotations

import re
from typing import Sequence

from .camera import default_start
from .geometry import anchors
from .markup import display_text, parse_markup
from .models import Explainer3DInputs, ResolvedShot
from .moves import MOVES
from .timing import ShotWindow

HOOK_S = 0.5          # something big must start this early
MAX_GAP_S = 2.8       # longest stretch with no beat (a cut, a move, an impact)
REHOOK_FROM_S, REHOOK_TO_S = 20.0, 35.0
REHOOK_OVER_S = 40.0  # reels longer than this need a second hook
SLAB_HALF = (0.8, 0.5)  # the stone slab, metres either side of the stage centre (x, z)
CTA = re.compile(r"\b(save|follow|share|comment|subscribe|try|like)\b", re.I)


def _length_ok(estimated_s: float, target_s: float) -> bool:
    return abs(estimated_s - target_s) <= max(3.0, 0.12 * target_s)


def retention_warnings(
    inputs: Explainer3DInputs, shots: Sequence[ResolvedShot], windows: Sequence[ShotWindow]
) -> list[str]:
    warnings: list[str] = []
    beats: list[float] = []
    big_starts: list[float] = []
    for shot, w in zip(shots, windows):
        start, seconds = w.start_ms / 1000, w.duration_ms / 1000
        beats.append(start)
        for action in shot.actions:
            move = MOVES[action.move]
            t0 = start + action.at[0] * seconds
            span = (action.at[1] - action.at[0]) * seconds
            beats.append(t0)
            for _, frac in ((move.sfx,) if move.sfx else ()) + move.extra_sfx:
                beats.append(t0 + span * frac)
            if move.big:
                big_starts.append(t0)
                beats.append(t0 + span / 2)  # the middle of a big move (a leap's apex) holds attention
    total = windows[-1].end_ms / 1000 if windows else 0.0
    beats = sorted(set(round(b, 3) for b in beats)) + [total]

    if not any(t <= HOOK_S for t in big_starts):
        warnings.append(
            "hook: nothing big happens in the first half second — open on the payoff "
            "(leap, slam, burst, smash, throw, splash) with `at` starting at 0")
    for a, b in zip(beats, beats[1:]):
        if b - a > MAX_GAP_S:
            where = next((s.id for s, w in zip(shots, windows) if w.start_ms / 1000 <= a < w.end_ms / 1000), "")
            warnings.append(f"beat: {b - a:.1f}s with nothing new from {a:.1f}s ({where}) — add a move, "
                            "a camera move or a cut; aim for a beat every 2-3 s")
    if total > REHOOK_OVER_S and not any(REHOOK_FROM_S <= t <= REHOOK_TO_S for t in big_starts):
        warnings.append("re-hook: a reel this long loses viewers around 25 s — put a big move and a "
                        "curiosity line (\"but here's the trick…\") between 20 and 35 s")
    last = shots[-1] if shots else None
    if last is not None and not CTA.search(f"{display_text(parse_markup(last.narration))} {last.card or ''}"):
        warnings.append("call to action: end with one (\"Save this…\", \"Follow for part 2\") in the last "
                        "shot's narration or card")
    warnings.extend(_cameras_in_the_stage(inputs, shots))
    if inputs.length and total and not _length_ok(total, inputs.length):
        verb = "add" if total < inputs.length else "cut"
        warnings.append(f"length: about {total:.0f}s against a target of {inputs.length}s — {verb} shots or "
                        "narration (tails stretch at most 1.6x)")
    return warnings


def _cameras_in_the_stage(inputs: Explainer3DInputs, shots: Sequence[ResolvedShot]) -> list[str]:
    """A fresh camera aimed at an anchor past the stone slab, from the other side of it,
    sees only rock: its line of sight runs through the slab."""
    if inputs.set.stage == "none":
        return []
    marks = anchors(inputs.set.stage)
    top = marks["stage.center"][1]

    def inside(p: list[float]) -> bool:
        return abs(p[0]) < SLAB_HALF[0] and abs(p[2]) < SLAB_HALF[1] and p[1] < top

    out = []
    for shot in shots:
        cam = shot.camera
        if cam.continues or cam.target not in marks or shot.environment == "underwater":
            continue
        aim = cam.params.get("aim") or (0.0, 0.0, 0.0)
        target = [c + o for c, o in zip(marks[cam.target], aim)]
        eye = default_start(cam.move, cam.params, target)["position"]
        if any(inside([e + (t - e) * k / 20 for e, t in zip(eye, target)]) for k in range(1, 20)):
            out.append(f"camera: {shot.id} looks through the stone slab — move it (another `from`, radius, "
                       "height or target) so the stage is not between the camera and the action")
    return out
