"""Retention lint: what keeps a viewer to the end, checked before anything is rendered.

Warnings, not refusals — the agent decides. Each one says what to change.
"""

from __future__ import annotations

import re
from typing import Sequence

from .camera import default_start
from .markup import display_text, parse_markup
from .models import Explainer3DInputs, ResolvedShot
from .moves import MOVES
from .sets import reel_sets, stage_anchors
from .timing import ShotWindow

HOOK_S = 0.5          # something big must start this early
MAX_GAP_S = 2.8       # longest stretch with no beat (a cut, a move, an impact)
REHOOK_FROM_S, REHOOK_TO_S = 20.0, 35.0
REHOOK_OVER_S = 40.0  # reels longer than this need a second hook
SECONDS_PER_MOMENT = 2.7  # what one big moment can hold a viewer for
PADDING = 1.3            # a target this far past what the material supports feels slow
BLOCKING = {"stone-slab": (0.8, 0.5), "counter": (0.8, 0.35)}  # solid stages: half-size in x, z (m)
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
        for effect in _effects(inputs, shot):  # an agent-written effect is a big moment too
            t0 = start + effect.at[0] * seconds
            beats.extend([t0, t0 + (effect.at[1] - effect.at[0]) * seconds / 2])
            big_starts.append(t0)
    total = windows[-1].end_ms / 1000 if windows else 0.0
    beats = sorted(set(round(b, 3) for b in beats)) + [total]

    if not any(t <= HOOK_S for t in big_starts):
        warnings.append(
            "hook: nothing big happens in the first half second — open on the payoff with a big move "
            f"({', '.join(name for name, m in MOVES.items() if m.big)}) or a component effect, "
            "with `at` starting at 0")
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
    big, suggested = length_advice(shots, windows, inputs)
    if inputs.length and inputs.length > suggested * PADDING:
        warnings.append(f"padding: {big} big moments support about {suggested}s; stretching to {inputs.length}s "
                        "will feel slow — add big moments, or choose a shorter length")
    if inputs.length and total and not _length_ok(total, inputs.length):
        verb = "add" if total < inputs.length else "cut"
        warnings.append(f"length: about {total:.0f}s against a target of {inputs.length}s — {verb} shots or "
                        "narration (tails stretch at most 1.6x)")
    return warnings


def _effects(inputs: Explainer3DInputs | None, shot: ResolvedShot) -> list:
    if inputs is None:
        return []
    return list(inputs.shot_list()[shot.index].effects)


def length_advice(
    shots: Sequence[ResolvedShot], windows: Sequence[ShotWindow], inputs: Explainer3DInputs | None = None
) -> tuple[int, int]:
    """(big moments, a length in seconds the material supports): ~2.7 s a moment, never
    under the spoken narration, rounded to 5 s, between 15 and 90."""
    big = sum(1 for shot in shots for a in shot.actions if MOVES[a.move].big)
    big += sum(len(_effects(inputs, shot)) for shot in shots)
    spoken = sum(w.speech_ms + w.speech_offset_ms for w in windows) / 1000
    raw = max(big * SECONDS_PER_MOMENT, spoken)
    return big, int(min(90, max(15, 5 * round(raw / 5))))


def _cameras_in_the_stage(inputs: Explainer3DInputs, shots: Sequence[ResolvedShot]) -> list[str]:
    """A fresh camera aimed at an anchor past the stone slab, from the other side of it,
    sees only rock: its line of sight runs through the slab."""
    reel, _ = reel_sets(inputs)
    if reel.stage not in BLOCKING:
        return []
    marks = stage_anchors(inputs)
    top = marks["stage.center"][1]

    half = BLOCKING[reel.stage]

    def inside(p: list[float]) -> bool:
        return abs(p[0]) < half[0] and abs(p[2]) < half[1] and p[1] < top

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
