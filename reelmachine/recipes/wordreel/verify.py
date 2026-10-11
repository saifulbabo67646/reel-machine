"""What a finished wordreel job can be checked for.

The failure this recipe actually has is not a crash — it is a reel that renders
perfectly and teaches the wrong thing, or teaches nothing: a line where the target
turned out to be a different word, a caption whose highlight landed on nothing, a
reel that is one film six times. Each of those is a warning here rather than an error,
because the reel is still watchable and the caller may still want it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.manifest import CheckResult, VerificationReport
from ...core.stage import StageContext
from ...render.captions import EMPHASIS_KEY
from ..wordreel.render import GLOSS_STYLE, LINE_STYLE, WORD_STYLE

#: A reel shorter than this is a clip, not a lesson.
MIN_REEL_MS = 8_000


def verify_wordreel(ctx: StageContext, outputs: dict[str, Any]) -> VerificationReport:
    checks: list[CheckResult] = []
    degradations: list[str] = []

    render = outputs.get("render")
    prepped = outputs.get("prep")
    scanned = outputs.get("scan")
    word = ""
    if prepped is not None:
        word = getattr(prepped, "word", "") or ""
    elif scanned is not None:
        word = getattr(scanned, "word", "") or ""

    if render is not None:
        video = Path(str(getattr(render, "video", "")))
        checks.append(
            CheckResult(
                name="reel",
                ok=video.is_file() and video.stat().st_size > 0,
                detail=f"{video} ({video.stat().st_size} B)" if video.is_file() else str(video),
            )
        )
        checks.append(
            CheckResult(
                name="duration",
                ok=int(getattr(render, "duration_ms", 0)) >= MIN_REEL_MS,
                detail=f"{int(getattr(render, 'duration_ms', 0)) / 1000:.1f}s",
            )
        )

    if prepped is not None:
        clips = list(getattr(prepped, "clips", []) or [])
        skipped = list(getattr(prepped, "skipped", []) or [])
        checks.append(
            CheckResult(name="clips", ok=bool(clips), detail=f"{len(clips)} example(s)")
        )
        if skipped:
            degradations.append(f"{len(skipped)} matching line(s) could not be cut")

        labels = {record.candidate.media_label for record in clips}
        if len(clips) >= 3 and len(labels) == 1:
            degradations.append(
                f"all {len(clips)} examples come from one title — the reel teaches one voice"
            )

        # Does every line actually contain the word being taught?
        from .matching import WordMatcher

        try:
            matcher = WordMatcher(word) if word else None
        except ValueError:
            matcher = None
        if matcher is not None:
            missing = [
                record.candidate.text
                for record in clips
                if not matcher.matches(record.candidate.text)
            ]
            checks.append(
                CheckResult(
                    name="target_present",
                    ok=not missing,
                    detail=(
                        f"{len(clips)} line(s) use {word!r}"
                        if not missing
                        else f"{len(missing)} line(s) do not contain {word!r}"
                    ),
                )
            )
            if missing:
                degradations.append(
                    "a clip's line does not contain the target word; it will be captioned "
                    "without a highlight"
                )

        bad_highlight = [
            record.index
            for record in clips
            if record.emphasis and record.emphasis.lower() not in record.candidate.text.lower()
        ]
        if bad_highlight:
            degradations.append(
                f"clip(s) {bad_highlight[:5]} highlight a form that is not in the line"
            )

        timeline = getattr(prepped, "timeline", None)
        if timeline is not None:
            styles = {caption.style_ref for caption in timeline.captions}
            checks.append(
                CheckResult(
                    name="captions",
                    ok=LINE_STYLE in styles and WORD_STYLE in styles,
                    detail=", ".join(sorted(styles)) or "none",
                )
            )
            if GLOSS_STYLE in styles:
                checks.append(
                    CheckResult(name="gloss", ok=True, detail="meaning shown on the card")
                )
            unserialised = [
                caption.text
                for caption in timeline.captions
                if caption.style_ref == LINE_STYLE and not caption.words
            ]
            if unserialised:
                degradations.append(
                    f"{len(unserialised)} caption(s) have no word timings; karaoke is off for them"
                )
            covered = sum(
                1 for caption in timeline.captions if caption.meta.get(EMPHASIS_KEY)
            )
            if covered != len(clips):
                degradations.append(
                    f"{len(clips) - covered} clip(s) carry no emphasis marker"
                )

    if scanned is not None:
        stats = getattr(scanned, "stats", {}) or {}
        checks.append(
            CheckResult(
                name="scan",
                ok=bool(stats.get("matches")),
                detail=(
                    f"{stats.get('matches', 0)} match(es) across "
                    f"{stats.get('sources', 0)} source(s)"
                ),
            )
        )

    ok = all(check.ok for check in checks)
    return VerificationReport(
        ok=ok,
        checks=checks,
        notes=f"wordreel: {word!r}" if word else "wordreel",
        render_mode="wordreel",
        visible_degradation=degradations,
    )
