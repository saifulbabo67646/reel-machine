"""Map spoken word spans onto caption units, so captions highlight in step with the voice."""

from __future__ import annotations

from itertools import pairwise

from reelmachine.core.timeline import WordSpan

from .markup import Unit


def _chars(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def unit_times(spans: list[WordSpan], us: list[Unit], speech_ms: int) -> list[tuple[int, int]]:
    """Line-relative (start, end) per unit; monotonic and within [0, speech_ms]."""
    if not us:
        return []

    def clamp(v: float) -> int:
        return max(0, min(speech_ms, round(v)))

    if len(spans) == len(us):
        return [(clamp(s.start_ms), max(clamp(s.start_ms), clamp(s.end_ms))) for s in spans]

    # Piecewise-linear map: spoken-character position -> time.
    pts: list[tuple[float, float]] = []
    total = 0
    for s in spans:
        pts.append((total, s.start_ms))
        total += _chars(s.text)
    if spans and total:
        pts.append((total, spans[-1].end_ms))
        end_ms: float = spans[-1].end_ms
    else:
        total = sum(_chars(u.text) for u in us) or 1
        pts = [(0, 0), (total, speech_ms)]
        end_ms = speech_ms

    def at(x: float) -> float:
        for (x0, t0), (x1, t1) in pairwise(pts):
            if x <= x1:
                return t0 if x1 == x0 else t0 + (t1 - t0) * (x - x0) / (x1 - x0)
        return pts[-1][1]

    caption_total = sum(_chars(u.text) for u in us) or 1
    starts: list[int] = []
    before = 0
    prev = 0
    for u in us:
        # Monotonic even if the spans are not.
        prev = max(prev, clamp(at(before / caption_total * total)))
        starts.append(prev)
        before += _chars(u.text)
    ends = [*starts[1:], max(starts[-1], clamp(end_ms))]
    return list(zip(starts, ends))
